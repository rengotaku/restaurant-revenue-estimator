"""売上恒等式と月次・年次集計. 円は int、比率は Decimal."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

TEN = Decimal("0.10")
EIGHT = Decimal("0.08")


class SeasonalityError(Exception):
    """季節係数を売上に二重適用した."""


class SeasonalityRegistry:
    """季節係数をどの対象に掛けたかを覚える."""

    def __init__(self) -> None:
        self._targets: dict[str, str] = {}

    def apply(
        self,
        key: str,
        factor: Decimal,
        target: Literal["guests", "course_ratio", "revenue"],
    ) -> Decimal:
        previous = self._targets.get(key)
        if (
            previous is not None
            and previous != target
            and (target == "revenue" or previous == "revenue")
        ):
            raise SeasonalityError(
                f"季節係数 {key} を {previous} に掛けたあと {target} に再適用した"
            )
        self._targets[key] = target
        return factor


@dataclass(frozen=True)
class CourseSales:
    guests: int
    unit_price: int
    includes_drink: bool = True


@dataclass(frozen=True)
class ServiceSales:
    """1 日 1 サービス (昼/夜) の売上. コース客とアラカルト客は排他."""

    ala_guests: int = 0
    ala_ticket: int = 0
    courses: tuple[CourseSales, ...] = ()
    appetizer_yen: int = 0
    appetizer_waived_for_course: bool = True
    other_revenue: int = 0
    tax_rate: Decimal = TEN

    @property
    def course_guests(self) -> int:
        return sum(course.guests for course in self.courses)

    @property
    def guests(self) -> int:
        return self.ala_guests + self.course_guests

    @property
    def ala_revenue(self) -> int:
        return self.ala_guests * self.ala_ticket

    @property
    def course_revenue(self) -> int:
        return sum(course.guests * course.unit_price for course in self.courses)

    @property
    def appetizer_revenue(self) -> int:
        if self.appetizer_waived_for_course:
            return self.ala_guests * self.appetizer_yen
        return self.guests * self.appetizer_yen

    @property
    def total_inc_tax(self) -> int:
        return (
            self.ala_revenue
            + self.course_revenue
            + self.appetizer_revenue
            + self.other_revenue
        )


@dataclass(frozen=True)
class DaySales:
    day: date
    lunch: ServiceSales = field(default_factory=ServiceSales)
    dinner: ServiceSales = field(default_factory=ServiceSales)

    @property
    def total_inc_tax(self) -> int:
        return self.lunch.total_inc_tax + self.dinner.total_inc_tax


@dataclass(frozen=True)
class MonthRevenue:
    year: int
    month: int
    lunch_inc_tax: int
    dinner_inc_tax: int
    ala_inc_tax: int
    course_inc_tax: int
    appetizer_inc_tax: int
    other_inc_tax: int
    tax_10_inc: int
    tax_8_inc: int

    @property
    def total_inc_tax(self) -> int:
        return self.lunch_inc_tax + self.dinner_inc_tax

    @property
    def total_ex_tax(self) -> int:
        return _ex_tax(self.tax_10_inc, TEN) + _ex_tax(self.tax_8_inc, EIGHT)

    @property
    def lunch_ex_tax(self) -> int:
        return _ex_tax(self.lunch_inc_tax, TEN)

    @property
    def dinner_ex_tax(self) -> int:
        return _ex_tax(self.dinner_inc_tax, TEN)


@dataclass(frozen=True)
class RevenueResult:
    months: tuple[MonthRevenue, ...]

    @property
    def total_inc_tax(self) -> int:
        return sum(month.total_inc_tax for month in self.months)

    @property
    def total_ex_tax(self) -> int:
        return sum(month.total_ex_tax for month in self.months)

    @property
    def lunch_inc_tax(self) -> int:
        return sum(month.lunch_inc_tax for month in self.months)

    @property
    def dinner_inc_tax(self) -> int:
        return sum(month.dinner_inc_tax for month in self.months)

    @property
    def ala_inc_tax(self) -> int:
        return sum(month.ala_inc_tax for month in self.months)

    @property
    def course_inc_tax(self) -> int:
        return sum(month.course_inc_tax for month in self.months)


def _ex_tax(amount_inc_tax: int, rate: Decimal) -> int:
    """税込から税抜. 端数は ROUND_HALF_UP で 1 回だけ."""
    exact = Decimal(amount_inc_tax) / (Decimal(1) + rate)
    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def realized_course_price(
    normal: int, discounted: int, application_rate: Decimal
) -> int:
    """通常価格と割引価格を適用率で混ぜた実際の支払額."""
    if not Decimal(0) <= application_rate <= Decimal(1):
        raise ValueError("適用率は 0〜1")
    exact = (
        Decimal(normal) * (Decimal(1) - application_rate)
        + Decimal(discounted) * application_rate
    )
    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class PricePeriod:
    effective_from: date
    price: int


def price_at(periods: Sequence[PricePeriod], day: date) -> int:
    """有効期間の途中の改定を解決する."""
    usable = [period for period in periods if period.effective_from <= day]
    if not usable:
        raise ValueError(f"{day} に有効な価格が無い")
    return usable[-1].price


def aggregate(days: Iterable[DaySales]) -> RevenueResult:
    """月次に集計し、年の値を月の和で作る (丸めは月に 1 回)."""
    buckets: dict[tuple[int, int], list[int]] = {}
    for day_sales in days:
        key = (day_sales.day.year, day_sales.day.month)
        bucket = buckets.setdefault(key, [0] * 8)
        for service in (day_sales.lunch, day_sales.dinner):
            bucket[0] += service.total_inc_tax if service is day_sales.lunch else 0
            bucket[1] += service.total_inc_tax if service is day_sales.dinner else 0
            bucket[2] += service.ala_revenue
            bucket[3] += service.course_revenue
            bucket[4] += service.appetizer_revenue
            bucket[5] += service.other_revenue
            if service.tax_rate == EIGHT:
                bucket[7] += service.total_inc_tax
            else:
                bucket[6] += service.total_inc_tax

    months: list[MonthRevenue] = []
    for (year, month), bucket in sorted(buckets.items()):
        months.append(
            MonthRevenue(
                year=year,
                month=month,
                lunch_inc_tax=bucket[0],
                dinner_inc_tax=bucket[1],
                ala_inc_tax=bucket[2],
                course_inc_tax=bucket[3],
                appetizer_inc_tax=bucket[4],
                other_inc_tax=bucket[5],
                tax_10_inc=bucket[6],
                tax_8_inc=bucket[7],
            )
        )
    return RevenueResult(months=tuple(months))
