"""過去の試算 v5 の検査. 新しい計算の入力には使わない."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from restaurant_revenue_estimator.engine.calendar import build_calendars, load_holidays


class LegacyError(Exception):
    """legacy の値を新しい計算に渡そうとした."""


@dataclass(frozen=True)
class RowDiff:
    month: int
    lunch: int
    dinner: int
    reported: int

    @property
    def diff(self) -> int:
        return self.lunch + self.dinner - self.reported


@dataclass(frozen=True)
class DayDiff:
    month: int
    recorded: int
    calendar: int

    @property
    def diff(self) -> int:
        return self.recorded - self.calendar


@dataclass(frozen=True)
class LegacyCheck:
    label: str
    rows: tuple[RowDiff, ...]
    total_lunch_dinner: int
    total_reported: int
    day_diffs: tuple[DayDiff, ...]
    arithmetic: int
    expense_ratios: dict[str, float]

    @property
    def row_mismatches(self) -> tuple[RowDiff, ...]:
        return tuple(row for row in self.rows if row.diff != 0)

    @property
    def total_mismatch(self) -> int:
        return self.total_lunch_dinner - self.total_reported

    @property
    def hidden_closed_days(self) -> int:
        return -sum(diff.diff for diff in self.day_diffs)


def assert_not_legacy(data: dict[str, Any]) -> None:
    """legacy の dict を新しい計算に渡したらエラー."""
    if data.get("kind") == "legacy_scenario":
        raise LegacyError("legacy は実績でも入力でもない。新しい計算に渡せない")


def load_legacy(directory: Path) -> dict[str, Any]:
    path = directory / "legacy" / "v5.yaml"
    if not path.exists():
        raise LegacyError(f"{path} が無い")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("kind") != "legacy_scenario":
        raise LegacyError(f"{path} の kind が legacy_scenario でない")
    if data.get("is_actual") is not False:
        raise LegacyError("legacy は is_actual: false でなければならない")
    return data


def _calendar_days(data_dir: Path, year: int) -> list[int]:
    holidays = load_holidays(
        data_dir / "calendars" / "jp_holidays_cao.csv",
        data_dir / "calendars" / "jp_holidays_cao.meta.yaml",
        year,
    )
    rule = {
        "id": "current",
        "open_weekdays": ["mon", "tue", "wed", "thu", "fri"],
        "open_on_holidays": False,
        "lunch": {"open": "11:30", "close": "13:30"},
        "dinner": {"open": "17:30", "close": "22:30"},
        "status": "PUBLIC_VERIFIED",
    }
    series = build_calendars(rules=[rule], year=year, mode="steady", holidays=holidays)[
        0
    ]
    return series.monthly_open_days()


def check_legacy(directory: Path, data_dir: Path) -> LegacyCheck:
    data = load_legacy(directory)
    year = int(data.get("year", 2026))
    rows = tuple(
        RowDiff(
            month=int(row["month"]),
            lunch=int(row["lunch"]),
            dinner=int(row["dinner"]),
            reported=int(row["reported"]),
        )
        for row in data["months"]
    )
    calendar_days = _calendar_days(data_dir, year)
    day_diffs = tuple(
        DayDiff(
            month=row.month,
            recorded=int(raw["business_days"]),
            calendar=calendar_days[row.month - 1],
        )
        for row, raw in zip(rows, data["months"], strict=True)
    )
    totals = data.get("totals", {})
    total_lunch_dinner = int(totals.get("lunch", sum(row.lunch for row in rows))) + int(
        totals.get("dinner", sum(row.dinner for row in rows))
    )
    total_reported = int(totals.get("reported", sum(row.reported for row in rows)))

    cost = data.get("cost_summary", {})
    arithmetic = (
        int(cost.get("gross_revenue_ex_tax", 0))
        - int(cost.get("food_cost", 0))
        - int(cost.get("labor_cost", 0))
        - int(cost.get("rent", 0))
        - int(cost.get("other", 0))
    )
    revenue = int(cost.get("gross_revenue_ex_tax", 1)) or 1
    ratios = {
        "food_cost": int(cost.get("food_cost", 0)) / revenue,
        "labor_cost": int(cost.get("labor_cost", 0)) / revenue,
        "rent": int(cost.get("rent", 0)) / revenue,
        "other": int(cost.get("other", 0)) / revenue,
    }
    return LegacyCheck(
        label=str(data.get("label", "legacy")),
        rows=rows,
        total_lunch_dinner=total_lunch_dinner,
        total_reported=total_reported,
        day_diffs=day_diffs,
        arithmetic=arithmetic,
        expense_ratios=ratios,
    )


def format_legacy_check(check: LegacyCheck) -> list[str]:
    """CLI に出す行. 先頭で実績ではないことを言う."""
    lines = [f"過去の試算であり実績ではない: {check.label}"]
    mismatches = ", ".join(
        f"{row.month}月 {row.diff:+d}万円" for row in check.row_mismatches
    )
    lines.append(f"行ごとの差（昼+夜 - 月商）: {mismatches or 'なし'}")
    lines.append(
        f"列の合計: 昼+夜 {check.total_lunch_dinner} 万円 / 月商列 {check.total_reported} 万円 / "
        f"差 {check.total_mismatch:+d} 万円"
    )
    day_mismatches = ", ".join(
        f"{diff.month}月 {diff.diff:+d}日" for diff in check.day_diffs if diff.diff != 0
    )
    recorded = sum(diff.recorded for diff in check.day_diffs)
    calendar_total = sum(diff.calendar for diff in check.day_diffs)
    lines.append(
        f"営業日と暦: {day_mismatches or '差なし'} / 記載 {recorded} 日 / 暦 {calendar_total} 日"
    )
    if check.hidden_closed_days:
        lines.append(f"記載のない休業日が {check.hidden_closed_days} 日ある")
    lines.append(f"営業余剰の再計算: {check.arithmetic} 万円")
    ratios = ", ".join(
        f"{key} {value:.1%}" for key, value in check.expense_ratios.items()
    )
    lines.append(f"費用率（出典が無い）: {ratios}")
    return lines
