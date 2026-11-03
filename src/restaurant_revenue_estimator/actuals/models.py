"""実績（POS の日次売上）を表す型.

入力 YAML は「仮定」を持ち、実績は持たない。ここはその境界で、
`data/stores/<store_id>/actuals/` に置いた実績だけを読む。
"""

from dataclasses import dataclass
from datetime import date


class ActualsError(Exception):
    """実績データが読めない、または要件を満たさない."""


@dataclass(frozen=True)
class ActualDay:
    """1 日分の実績."""

    day: date
    revenue_inc_tax: int
    orders: int | None = None
    guests: int | None = None


@dataclass(frozen=True)
class ActualSeries:
    """1 店舗分の実績系列."""

    store_id: str
    days: tuple[ActualDay, ...]
    source_id: str
    license: str
    coverage_note: str
    url: str | None = None
    accessed_at: str | None = None

    @property
    def total_inc_tax(self) -> int:
        return sum(day.revenue_inc_tax for day in self.days)

    @property
    def period(self) -> tuple[date, date]:
        return self.days[0].day, self.days[-1].day

    @property
    def by_day(self) -> dict[date, int]:
        return {day.day: day.revenue_inc_tax for day in self.days}
