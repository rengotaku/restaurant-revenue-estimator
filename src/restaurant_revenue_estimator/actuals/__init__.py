"""実績（POS の日次売上）の読み込みと、シナリオとの突合."""

from restaurant_revenue_estimator.actuals.backtest import (
    BacktestError,
    BacktestResult,
    PredictedSeries,
    compare,
    load_predicted_days,
    render_markdown,
)
from restaurant_revenue_estimator.actuals.loader import load_actuals
from restaurant_revenue_estimator.actuals.models import (
    ActualDay,
    ActualSeries,
    ActualsError,
)

__all__ = [
    "ActualDay",
    "ActualSeries",
    "ActualsError",
    "BacktestError",
    "BacktestResult",
    "PredictedSeries",
    "compare",
    "load_actuals",
    "load_predicted_days",
    "render_markdown",
]
