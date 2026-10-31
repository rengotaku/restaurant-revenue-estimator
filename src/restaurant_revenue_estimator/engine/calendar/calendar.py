"""営業日カレンダー: 定常年 (steady) と暦年 (historical)."""

import csv
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class CalendarError(Exception):
    """祝日 CSV やルールの不備."""


@dataclass(frozen=True)
class ServiceHours:
    open: str
    close: str
    last_order: str
    last_entry: str


@dataclass(frozen=True)
class DaySchedule:
    day: date
    weekday: str
    holiday: str | None
    lunch: ServiceHours | None
    dinner: ServiceHours | None
    closed_reason: str | None
    status: str

    @property
    def is_open(self) -> bool:
        return self.lunch is not None or self.dinner is not None


@dataclass(frozen=True)
class CalendarSeries:
    name: str
    mode: str
    days: tuple[DaySchedule, ...]

    @property
    def open_days(self) -> tuple[DaySchedule, ...]:
        return tuple(day for day in self.days if day.is_open)

    def monthly_open_days(self) -> list[int]:
        counts = [0] * 12
        for day in self.open_days:
            counts[day.day.month - 1] += 1
        return counts


def load_holidays(csv_path: Path, meta_path: Path, year: int) -> dict[date, str]:
    """内閣府 CSV を読む. sha256 と年を検査する."""
    raw = csv_path.read_bytes()
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    expected = meta.get("sha256")
    actual = hashlib.sha256(raw).hexdigest()
    if expected and expected != actual:
        raise CalendarError(f"祝日 CSV の sha256 が meta と違う: {actual}")

    encoding = meta.get("encoding", "cp932")
    text = raw.decode("shift_jis" if encoding == "Shift_JIS" else encoding)
    holidays: dict[date, str] = {}
    for row in csv.reader(text.splitlines()[1:]):
        if len(row) < 2:
            continue
        try:
            month, day = row[0].split("/")[1:3]
            holiday = date(int(row[0].split("/")[0]), int(month), int(day))
        except (ValueError, IndexError) as exc:
            raise CalendarError(f"祝日 CSV の行が読めない: {row}") from exc
        if holiday.year == year:
            holidays[holiday] = row[1]
    if not holidays:
        raise CalendarError(f"{year} 年の祝日が CSV に無い（祝日なしとして続行しない）")
    return holidays


def _hours(raw: dict[str, Any] | None) -> ServiceHours | None:
    if not raw:
        return None
    return ServiceHours(
        open=str(raw["open"]),
        close=str(raw["close"]),
        last_order=str(raw.get("last_order", raw["close"])),
        last_entry=str(raw.get("last_entry", raw.get("last_order", raw["close"]))),
    )


def _rule_hours(
    rule: dict[str, Any], weekday: str, holiday: bool
) -> tuple[ServiceHours | None, ServiceHours | None]:
    if holiday and rule.get("holiday_hours"):
        return None, _hours(rule["holiday_hours"])
    if weekday == "sun" and rule.get("sunday_hours"):
        return None, _hours(rule["sunday_hours"])
    return _hours(rule.get("lunch")), _hours(rule.get("dinner"))


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _active_rule(
    rules: list[dict[str, Any]], day: date, mode: str
) -> dict[str, Any] | None:
    if mode == "steady":
        usable = []
        for rule in rules:
            start = _parse_date(rule.get("effective_from"))
            if start is None or start <= date(day.year, 12, 31):
                usable.append(rule)
        return usable[-1] if usable else None
    for rule in rules:
        start = _parse_date(rule.get("effective_from"))
        end = _parse_date(rule.get("effective_to"))
        if (start is None or start <= day) and (end is None or day <= end):
            return rule
    return None


def build_series(
    *,
    rules: list[dict[str, Any]],
    year: int,
    mode: str,
    holidays: dict[date, str],
    exceptions: list[dict[str, Any]] | None = None,
    saturday_closure_start: date | None = None,
    name: str = "default",
) -> CalendarSeries:
    """1 本の営業日系列を作る."""
    exception_by_day = {
        _parse_date(item["date"]): item
        for item in (exceptions or [])
        if item.get("date")
    }
    days: list[DaySchedule] = []
    for month in range(1, 13):
        for day_number in range(1, 32):
            try:
                current = date(year, month, day_number)
            except ValueError:
                continue
            weekday = WEEKDAYS[current.weekday()]
            holiday = holidays.get(current)

            exception = exception_by_day.get(current)
            if exception is not None:
                if exception.get("closed"):
                    days.append(
                        DaySchedule(
                            current,
                            weekday,
                            holiday,
                            None,
                            None,
                            "exception",
                            str(exception.get("status", "ASSUMPTION")),
                        )
                    )
                    continue
                lunch = _hours(exception.get("lunch"))
                dinner = _hours(exception.get("dinner"))
                days.append(
                    DaySchedule(
                        current,
                        weekday,
                        holiday,
                        lunch,
                        dinner,
                        None,
                        str(exception.get("status", "ASSUMPTION")),
                    )
                )
                continue

            if holiday is not None and not _rule_opens_holiday(rules, current, mode):
                days.append(
                    DaySchedule(
                        current,
                        weekday,
                        holiday,
                        None,
                        None,
                        "holiday",
                        _rule_status(rules, current, mode),
                    )
                )
                continue

            if (
                weekday == "sat"
                and saturday_closure_start is not None
                and current >= saturday_closure_start
            ):
                days.append(
                    DaySchedule(
                        current,
                        weekday,
                        holiday,
                        None,
                        None,
                        "saturday_closure",
                        "PUBLIC_VERIFIED",
                    )
                )
                continue

            rule = _active_rule(rules, current, mode)
            if rule is None:
                days.append(
                    DaySchedule(
                        current, weekday, holiday, None, None, "no_rule", "UNRESOLVED"
                    )
                )
                continue
            if weekday not in rule.get("open_weekdays", []) and not (
                holiday and rule.get("open_on_holidays")
            ):
                days.append(
                    DaySchedule(
                        current,
                        weekday,
                        holiday,
                        None,
                        None,
                        "weekday_rule",
                        str(rule.get("status", "PUBLIC_VERIFIED")),
                    )
                )
                continue
            lunch, dinner = _rule_hours(rule, weekday, holiday is not None)
            closed_reason = None if (lunch or dinner) else "no_hours"
            days.append(
                DaySchedule(
                    current,
                    weekday,
                    holiday,
                    lunch,
                    dinner,
                    closed_reason,
                    str(rule.get("status", "PUBLIC_VERIFIED")),
                )
            )
    return CalendarSeries(name=name, mode=mode, days=tuple(days))


def _rule_opens_holiday(rules: list[dict[str, Any]], day: date, mode: str) -> bool:
    rule = _active_rule(rules, day, mode)
    return bool(rule and rule.get("open_on_holidays"))


def _rule_status(rules: list[dict[str, Any]], day: date, mode: str) -> str:
    rule = _active_rule(rules, day, mode)
    return str(rule.get("status", "PUBLIC_VERIFIED")) if rule else "UNRESOLVED"


def build_calendars(
    *,
    rules: list[dict[str, Any]],
    year: int,
    mode: str,
    holidays: dict[date, str],
    exceptions: list[dict[str, Any]] | None = None,
    saturday_closure_candidates: dict[str, date | None] | None = None,
) -> list[CalendarSeries]:
    """UNRESOLVED の開始日は候補ごとに系列を出す."""
    if not saturday_closure_candidates:
        return [
            build_series(
                rules=rules,
                year=year,
                mode=mode,
                holidays=holidays,
                exceptions=exceptions,
            )
        ]
    return [
        build_series(
            rules=rules,
            year=year,
            mode=mode,
            holidays=holidays,
            exceptions=exceptions,
            saturday_closure_start=start,
            name=candidate,
        )
        for candidate, start in saturday_closure_candidates.items()
    ]


def calendars_for_store(
    inputs: Any,
    *,
    year: int,
    mode: str,
    holidays: dict[date, str],
    saturday_closure_start: str | None = None,
) -> list[CalendarSeries]:
    """StoreInputs から営業日系列を作る."""
    operations = inputs.files.get("operations.yaml", {}) or {}
    rules_value = operations.get("rules", {})
    rules = rules_value.get("value", []) if isinstance(rules_value, dict) else []
    exceptions_value = (operations.get("exceptions") or {}).get("value", [])
    candidates: dict[str, date | None] = {}
    if saturday_closure_start:
        candidates = {"selected": date.fromisoformat(saturday_closure_start)}
    else:
        unresolved = operations.get("saturday_closure_start") or {}
        for candidate in unresolved.get("candidates", {}) or {}:
            candidates[candidate] = date.fromisoformat(candidate)
    return build_calendars(
        rules=rules,
        year=year,
        mode=mode,
        holidays=holidays,
        exceptions=exceptions_value,
        saturday_closure_candidates=candidates or None,
    )
