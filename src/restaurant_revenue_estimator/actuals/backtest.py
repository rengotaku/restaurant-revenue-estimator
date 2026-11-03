"""実績とシナリオの日次売上を突き合わせる.

比較するのは日次の売上（税込）だけ。客数・組数・滞在時間は実績側に
無いことが多いので、ここでは扱わない。

3 シナリオ（悲観・標準・楽観）を予測区間として使わない。区間被覆率は、
replications の分布を日次で保存できるようになってから計算する
（ADR 0008: 3 シナリオを帯や誤差棒で見せると予測区間に見える）。
"""

import json
import statistics
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from restaurant_revenue_estimator.actuals.models import ActualSeries


class BacktestError(Exception):
    """比較できない（重なる日が無い、run の形が想定外など）."""


@dataclass(frozen=True)
class PredictedSeries:
    """run から取り出した 1 本の日次売上系列."""

    scenario: str
    variant: str
    days: dict[date, int]


@dataclass(frozen=True)
class DailyComparison:
    day: date
    actual: int
    predicted: int

    @property
    def diff(self) -> int:
        return self.predicted - self.actual


@dataclass(frozen=True)
class BacktestResult:
    store_id: str
    scenario: str
    variant: str
    source_id: str
    license: str
    coverage_note: str
    actual_period: tuple[date, date]
    compared_days: int
    actual_days: int
    predicted_days: int
    actual_only: int
    predicted_only: int
    mae: int
    median_abs_error: int
    bias: int
    relative_error: float | None
    actual_total: int
    predicted_total: int
    rows: tuple[DailyComparison, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "scenario": self.scenario,
            "variant": self.variant,
            "source_id": self.source_id,
            "license": self.license,
            "coverage_note": self.coverage_note,
            "actual_period": [day.isoformat() for day in self.actual_period],
            "compared_days": self.compared_days,
            "actual_days": self.actual_days,
            "predicted_days": self.predicted_days,
            "actual_only": self.actual_only,
            "predicted_only": self.predicted_only,
            "mae": self.mae,
            "median_abs_error": self.median_abs_error,
            "bias": self.bias,
            "relative_error": self.relative_error,
            "actual_total": self.actual_total,
            "predicted_total": self.predicted_total,
            "rows": [
                {
                    "date": row.day.isoformat(),
                    "actual": row.actual,
                    "predicted": row.predicted,
                    "diff": row.diff,
                }
                for row in self.rows
            ],
        }


def load_predicted_days(
    path: Path, scenario: str = "standard", variant: str | None = None
) -> PredictedSeries:
    """run の JSON から日次売上を取り出す.

    受け付ける形:

    - `rre run` の出力（`scenarios` か `variants` を持つ）
    - `{"days": [{"date": ..., "total": ...}]}` だけの最小形
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BacktestError(f"run の JSON がありません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BacktestError(f"run の JSON を読めません: {path} ({exc})") from exc

    if not isinstance(document, dict):
        raise BacktestError(f"run の JSON が object ではありません: {path}")

    entries = _candidate_entries(document, scenario, path)
    chosen = _choose_variant(entries, variant, scenario)
    days = _read_days(chosen, path)
    if not days:
        label = chosen.get("name") or chosen.get("variant")
        raise BacktestError(
            f"run に日次売上がありません: scenario={scenario} variant={label}"
        )
    return PredictedSeries(
        scenario=str(chosen.get("assumption_set") or scenario),
        variant=str(chosen.get("name") or chosen.get("variant") or "default"),
        days=days,
    )


def _candidate_entries(
    document: dict[str, Any], scenario: str, path: Path
) -> list[dict[str, Any]]:
    raw = document.get("scenarios")
    if isinstance(raw, list) and raw:
        entries = [entry for entry in raw if isinstance(entry, dict)]
        selected = [
            entry for entry in entries if entry.get("assumption_set") == scenario
        ]
        if not selected:
            available = sorted({str(entry.get("assumption_set")) for entry in entries})
            raise BacktestError(
                f"scenario {scenario!r} が run にありません: {path}。"
                f"利用できるのは {', '.join(available)} です。"
            )
        return selected

    raw = document.get("variants")
    if isinstance(raw, list) and raw:
        entries = [entry for entry in raw if isinstance(entry, dict)]
        if scenario != "standard":
            raise BacktestError(
                f"variants だけを持つ run は scenario {scenario!r} を選べません: {path}"
            )
        return entries

    if isinstance(document.get("days"), list):
        return [document]

    raise BacktestError(
        f"run の JSON に scenarios / variants / days がありません: {path}"
    )


def _choose_variant(
    entries: list[dict[str, Any]], variant: str | None, scenario: str
) -> dict[str, Any]:
    if variant is None:
        return entries[0]
    for entry in entries:
        if entry.get("name") == variant or entry.get("variant") == variant:
            return entry
    available = sorted(
        {str(entry.get("name") or entry.get("variant")) for entry in entries}
    )
    raise BacktestError(
        f"variant {variant!r} が scenario {scenario!r} にありません。"
        f"利用できるのは {', '.join(available)} です。"
    )


def _read_days(entry: dict[str, Any], path: Path) -> dict[date, int]:
    raw_days = entry.get("days")
    if not isinstance(raw_days, list):
        raise BacktestError(f"日次売上の配列がありません: {path}")

    days: dict[date, int] = {}
    for row in raw_days:
        if not isinstance(row, dict):
            raise BacktestError(f"日次売上の要素が object ではありません: {path}")
        raw_date = row.get("date")
        total = row.get("total", row.get("total_inc_tax"))
        if raw_date is None or total is None:
            raise BacktestError(f"日次売上に date / total がありません: {row} ({path})")
        try:
            day = date.fromisoformat(str(raw_date))
        except ValueError as exc:
            raise BacktestError(f"日付を解釈できません: {raw_date!r} ({path})") from exc
        if day in days:
            raise BacktestError(f"予測の日付が重複しています: {day} ({path})")
        days[day] = int(total)
    return days


def compare(actual: ActualSeries, predicted: PredictedSeries) -> BacktestResult:
    """重なる日だけで指標を出す. 重なる日が無ければ BacktestError."""
    common = sorted(set(actual.by_day) & set(predicted.days))
    if not common:
        first_actual, last_actual = actual.period
        first_predicted, last_predicted = min(predicted.days), max(predicted.days)
        raise BacktestError(
            "実績と予測で重なる日がありません: "
            f"実績 {first_actual}〜{last_actual}、"
            f"予測 {first_predicted}〜{last_predicted}"
        )

    rows = tuple(
        DailyComparison(
            day=day, actual=actual.by_day[day], predicted=predicted.days[day]
        )
        for day in common
    )
    diffs = [row.diff for row in rows]
    abs_diffs = [abs(diff) for diff in diffs]
    actual_total = sum(row.actual for row in rows)
    predicted_total = sum(row.predicted for row in rows)
    relative = (predicted_total - actual_total) / actual_total if actual_total else None

    return BacktestResult(
        store_id=actual.store_id,
        scenario=predicted.scenario,
        variant=predicted.variant,
        source_id=actual.source_id,
        license=actual.license,
        coverage_note=actual.coverage_note,
        actual_period=actual.period,
        compared_days=len(rows),
        actual_days=len(actual.days),
        predicted_days=len(predicted.days),
        actual_only=len(set(actual.by_day) - set(predicted.days)),
        predicted_only=len(set(predicted.days) - set(actual.by_day)),
        mae=round(sum(abs_diffs) / len(abs_diffs)),
        median_abs_error=round(statistics.median(abs_diffs)),
        bias=round(sum(diffs) / len(diffs)),
        relative_error=relative,
        actual_total=actual_total,
        predicted_total=predicted_total,
        rows=rows,
    )


def render_markdown(result: BacktestResult) -> str:
    """人が読む比較レポート."""
    relative = "—" if result.relative_error is None else f"{result.relative_error:+.1%}"
    lines = [
        f"# 実績との比較: {result.store_id}（{result.scenario} / {result.variant}）",
        "",
        "> 1 店舗・1 期間の比較であり、これだけで予測精度を主張しない。",
        "> 3 シナリオは予測区間ではないため、区間被覆率は出していない。",
        "",
        f"- 実績の出所: {result.source_id}（{result.license}）",
        f"- 実績の範囲: {result.actual_period[0]}〜{result.actual_period[1]}、"
        f"{result.actual_days} 日",
        f"- 実績の欠測: {result.coverage_note}",
        "",
        "| 指標 | 値 |",
        "|---|---|",
        f"| 比較した日数 | {result.compared_days} 日"
        f"（実績 {result.actual_days} 日 / 予測 {result.predicted_days} 日） |",
        f"| 実績合計 | {result.actual_total:,} 円 |",
        f"| 予測合計 | {result.predicted_total:,} 円 |",
        f"| MAE（平均絶対誤差） | {result.mae:,} 円 |",
        f"| 中央値絶対誤差 | {result.median_abs_error:,} 円 |",
        f"| バイアス（予測 − 実績） | {result.bias:+,} 円/日 |",
        f"| 相対誤差（合計） | {relative} |",
        "",
        "## 日別",
        "",
        "| 日付 | 実績 | 予測 | 差 |",
        "|---|---:|---:|---:|",
    ]
    for row in result.rows:
        lines.append(
            f"| {row.day.isoformat()} | {row.actual:,} | "
            f"{row.predicted:,} | {row.diff:+,} |"
        )
    lines.append("")
    return "\n".join(lines)
