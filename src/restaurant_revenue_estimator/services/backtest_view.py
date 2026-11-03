"""売上画面に出す「実績との比較」の組み立て."""

from pathlib import Path
from typing import Any

from restaurant_revenue_estimator.actuals import (
    ActualsError,
    BacktestError,
    compare,
    load_actuals,
    series_from_days,
)

WORST_DAYS = 5


def build_backtest_view(
    *,
    store_dir: Path,
    days: list[dict[str, Any]],
    scenario: str,
    variant: str,
    store_id: str,
) -> dict[str, Any] | None:
    """実績が無ければ None。突合できないときは理由だけ返す."""
    if not (store_dir / "actuals").is_dir():
        return None

    try:
        actual = load_actuals(store_dir)
        predicted = series_from_days(
            days, scenario=scenario, variant=variant, store_id=store_id
        )
        result = compare(actual, predicted)
    except (ActualsError, BacktestError) as exc:
        return {"result": None, "worst": [], "note": str(exc)}

    worst = sorted(result.rows, key=lambda row: abs(row.diff), reverse=True)[
        :WORST_DAYS
    ]
    return {"result": result, "worst": worst, "note": None}
