"""人件費・原価・固定費."""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from restaurant_revenue_estimator.engine.revenue.revenue import _ex_tax

MINIMUM_WAGE_TOKYO: tuple[tuple[date, int], ...] = (
    (date(2025, 10, 1), 1226),
    (date(2026, 10, 1), 1280),
)


@dataclass(frozen=True)
class StaffRole:
    role: str
    count: int
    monthly_salary: int


def _parse_time_hour(value: str | int | float | Decimal) -> Decimal:
    if isinstance(value, str):
        if ":" in value:
            parts = value.split(":", 1)
            return Decimal(parts[0]) + Decimal(parts[1]) / Decimal(60)
        return Decimal(value)
    return Decimal(str(value))


@dataclass(frozen=True)
class ShiftSpec:
    """シフト仕様 (OPT-7)."""

    id: str
    weekdays: tuple[str, ...]
    service: str
    start: str
    end: str
    heads: int
    hourly_wage: Decimal

    def __init__(
        self,
        id: str,
        weekdays: Sequence[str] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
        service: str = "dinner",
        start: str = "17:00",
        end: str = "23:00",
        heads: int = 1,
        hourly_wage: Decimal | int | str = Decimal("1330"),
    ) -> None:
        object.__setattr__(self, "id", str(id))
        object.__setattr__(self, "weekdays", tuple(weekdays))
        object.__setattr__(self, "service", str(service))
        object.__setattr__(self, "start", str(start))
        object.__setattr__(self, "end", str(end))
        object.__setattr__(self, "heads", int(heads))
        object.__setattr__(self, "hourly_wage", Decimal(str(hourly_wage)))

    @property
    def start_hour(self) -> Decimal:
        return _parse_time_hour(self.start)

    @property
    def end_hour(self) -> Decimal:
        return _parse_time_hour(self.end)


@dataclass(frozen=True)
class PartTimeWages:
    """シフト表から計算されたアルバイト賃金とシフト数."""

    total: Decimal
    count: int
    by_shift: dict[str, Decimal] = field(default_factory=dict)
    counts_by_shift: dict[str, int] = field(default_factory=dict)

    @property
    def amount(self) -> Decimal:
        return self.total

    @property
    def shift_count(self) -> int:
        return self.count


@dataclass(frozen=True)
class LaborBreakdown:
    """人件費の内訳."""

    full_time: Decimal
    bonus: Decimal
    part_time: Decimal
    statutory: Decimal
    commute: Decimal

    @property
    def total(self) -> Decimal:
        return (
            self.full_time + self.bonus + self.part_time + self.statutory + self.commute
        )


@dataclass(frozen=True)
class PartTimeShift:
    """1 日あたりのアルバイト. late_hours は 22:00-5:00 にかかる時間."""

    heads: int
    hours_per_day: Decimal
    hourly_wage: Decimal
    late_hours_per_day: Decimal = Decimal("0")


@dataclass(frozen=True)
class LaborCost:
    full_time: Decimal
    part_time: Decimal
    statutory: Decimal
    commute: Decimal
    owner: Decimal

    @property
    def total(self) -> Decimal:
        return self.full_time + self.part_time + self.statutory + self.commute

    @property
    def with_owner(self) -> Decimal:
        return self.total + self.owner


def minimum_wage_at(day: date) -> int:
    applicable = [wage for start, wage in MINIMUM_WAGE_TOKYO if start <= day]
    return applicable[-1] if applicable else MINIMUM_WAGE_TOKYO[0][1]


def wage_warnings(shift: PartTimeShift, day: date) -> list[str]:
    minimum = Decimal(minimum_wage_at(day))
    if shift.hourly_wage < minimum:
        return [f"時給 {shift.hourly_wage} 円が東京都最低賃金 {minimum} 円を下回る"]
    return []


def labor_warnings(
    shifts: Sequence[ShiftSpec],
    days: Sequence[Any],
) -> list[str]:
    """シフトごとに最低賃金を下回る最初の日付で警告を出す."""
    warnings: list[str] = []
    sorted_days = sorted(days, key=lambda d: d.day)
    for shift in shifts:
        for day in sorted_days:
            service_open = (
                day.lunch is not None
                if shift.service == "lunch"
                else day.dinner is not None
                if shift.service == "dinner"
                else getattr(day, "is_open", False)
            )
            if day.weekday in shift.weekdays and service_open:
                min_wage = minimum_wage_at(day.day)
                if shift.hourly_wage < Decimal(min_wage):
                    day_str = day.day.isoformat()
                    warnings.append(
                        f"シフト '{shift.id}': 時給 {shift.hourly_wage} 円が"
                        f"東京都最低賃金 {min_wage:,} 円（{day_str}〜）を下回る"
                    )
                    break
    return warnings


def shift_cost(
    start_hour: Decimal,
    end_hour: Decimal,
    hourly_wage: Decimal,
    premium: Decimal = Decimal("1.25"),
) -> Decimal:
    """1 人分の 1 シフト. 22:00-5:00 は割増."""
    if premium < Decimal("1.25"):
        raise ValueError("割増は 1.25 以上")
    if end_hour <= start_hour:
        end_hour += Decimal(24)
    checkpoints = [
        Decimal(0),
        Decimal(5),
        Decimal(22),
        Decimal(24),
        Decimal(29),
        Decimal(46),
        Decimal(48),
    ]
    breaks = sorted(
        {start_hour, end_hour} | {b for b in checkpoints if start_hour < b < end_hour}
    )
    total = Decimal(0)
    for t1, t2 in itertools.pairwise(breaks):
        duration = t2 - t1
        t_mid = (t1 + t2) / Decimal(2) % Decimal(24)
        is_late = t_mid >= Decimal(22) or t_mid < Decimal(5)
        rate = hourly_wage * premium if is_late else hourly_wage
        total += duration * rate
    return total


def part_time_wages(
    shifts: Sequence[ShiftSpec],
    days: Sequence[Any],
    late_premium: Decimal = Decimal("1.25"),
) -> PartTimeWages:
    """数えたシフト数と賃金（Decimal）."""
    by_shift: dict[str, Decimal] = {}
    counts_by_shift: dict[str, int] = {}
    total_count = 0
    total_wages = Decimal(0)

    for shift in shifts:
        unit_cost = shift_cost(
            shift.start_hour,
            shift.end_hour,
            shift.hourly_wage,
            late_premium,
        )
        daily_shift_wage = unit_cost * Decimal(shift.heads)
        count = 0
        for day in days:
            service_open = (
                day.lunch is not None
                if shift.service == "lunch"
                else day.dinner is not None
                if shift.service == "dinner"
                else getattr(day, "is_open", False)
            )
            if day.weekday in shift.weekdays and service_open:
                count += 1
        shift_total = daily_shift_wage * Decimal(count)
        by_shift[shift.id] = shift_total
        counts_by_shift[shift.id] = count
        total_count += count
        total_wages += shift_total

    return PartTimeWages(
        total=total_wages,
        count=total_count,
        by_shift=by_shift,
        counts_by_shift=counts_by_shift,
    )


def full_time_cost(roles: list[StaffRole]) -> Decimal:
    """職種ごとのユニーク人数 × 月給. 昼夜の二重配置でも 1 人分."""
    by_role: dict[str, StaffRole] = {}
    for role in roles:
        existing = by_role.get(role.role)
        if existing is None or role.count > existing.count:
            by_role[role.role] = role
    return sum(
        (
            Decimal(role.count) * Decimal(role.monthly_salary)
            for role in by_role.values()
        ),
        Decimal(0),
    )


def part_time_cost(
    shifts: list[PartTimeShift],
    *,
    days: int = 30,
    premium: Decimal = Decimal("1.25"),
) -> Decimal:
    total = Decimal(0)
    for shift in shifts:
        daily = Decimal(shift.heads) * shift.hours_per_day * shift.hourly_wage
        daily += (
            Decimal(shift.heads)
            * shift.late_hours_per_day
            * shift.hourly_wage
            * (premium - Decimal(1))
        )
        total += daily * Decimal(days)
    return total


def statutory_cost(
    base: Decimal,
    *,
    itemized: Decimal | None = None,
    flat_rate: Decimal | None = None,
) -> Decimal:
    """法定福利. 個別積算と一括率の両方は指定できない."""
    if itemized is not None and flat_rate is not None:
        raise ValueError("法定福利は個別積算と一括率のどちらか一方だけ")
    if itemized is not None:
        return itemized
    if flat_rate is not None:
        return base * flat_rate
    return Decimal(0)


def labor_breakdown(
    *,
    full_time_staff: Sequence[StaffRole] | None = None,
    full_time: Decimal | int | None = None,
    bonus_months_per_year: Decimal | int = 0,
    bonus: Decimal | int | None = None,
    part_time: Decimal | int = Decimal(0),
    commute: Decimal | int = Decimal(0),
    statutory_rate: Decimal | None = None,
    statutory_itemized: Decimal | None = None,
    statutory: Decimal | int | None = None,
) -> LaborBreakdown:
    """人件費の内訳を計算する.

    法定福利費の対象は給与・賞与・アルバイト賃金（通勤費には掛けない）.
    """
    if full_time is not None:
        ft_annual = Decimal(str(full_time))
        monthly_base = ft_annual / Decimal(12)
    elif full_time_staff is not None:
        monthly_base = full_time_cost(list(full_time_staff))
        ft_annual = monthly_base * Decimal(12)
    else:
        monthly_base = Decimal(0)
        ft_annual = Decimal(0)

    if bonus is not None:
        b_annual = Decimal(str(bonus))
    else:
        b_annual = monthly_base * Decimal(str(bonus_months_per_year))

    pt = Decimal(str(part_time))
    cm = Decimal(str(commute))

    if statutory is not None:
        stat = Decimal(str(statutory))
    else:
        statutory_base = ft_annual + b_annual + pt
        stat = statutory_cost(
            statutory_base,
            itemized=statutory_itemized,
            flat_rate=statutory_rate,
        )

    return LaborBreakdown(
        full_time=ft_annual,
        bonus=b_annual,
        part_time=pt,
        statutory=stat,
        commute=cm,
    )


def owner_labor(hours: Decimal, hourly_wage: Decimal) -> Decimal:
    return hours * hourly_wage


def simple_monthly_labor(
    *,
    full_time_count: int,
    monthly_salary: int,
    daily_heads: int,
    hours_per_day: Decimal,
    hourly_wage: Decimal,
    days: int = 30,
    overhead_rate: Decimal = Decimal("0.15"),
) -> Decimal:
    """spec 2.2 の簡便計算（付帯 15% 一括・深夜割増なし）."""
    base = Decimal(full_time_count) * Decimal(monthly_salary)
    base += Decimal(daily_heads) * hours_per_day * hourly_wage * Decimal(days)
    return base * (Decimal(1) + overhead_rate)


@dataclass(frozen=True)
class VariableCosts:
    food_ala: Decimal = Decimal(0)
    food_course: Decimal = Decimal(0)
    other_variable: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return self.food_ala + self.food_course + self.other_variable


def variable_costs(
    *,
    ala_revenue: int,
    course_revenue: int,
    cost_rate_ala: Decimal,
    cost_rate_course: Decimal,
    other_variable: Decimal = Decimal(0),
) -> VariableCosts:
    return VariableCosts(
        food_ala=Decimal(ala_revenue) * cost_rate_ala,
        food_course=Decimal(course_revenue) * cost_rate_course,
        other_variable=other_variable,
    )


def cost_of_goods(
    month: Any,
    cost_rate_ala: Decimal,
    cost_rate_course: Decimal,
) -> int:
    """OPT-8: 月次の原価（お通しを含む税抜売上に掛ける）."""
    ala_inc = month.ala_inc_tax + month.appetizer_inc_tax
    ala_ex = _ex_tax(ala_inc, Decimal("0.10"))
    course_ex = _ex_tax(month.course_inc_tax, Decimal("0.10"))
    exact = Decimal(ala_ex) * cost_rate_ala + Decimal(course_ex) * cost_rate_course
    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class FixedCosts:
    rent: Decimal = Decimal(0)
    other: dict[str, Decimal] = field(default_factory=dict)
    independence: Decimal = Decimal(0)
    depreciation: Decimal = Decimal(0)
    interest: Decimal = Decimal(0)
    principal: Decimal = Decimal(0)
    capex: Decimal = Decimal(0)

    @property
    def other_total(self) -> Decimal:
        return sum(self.other.values(), Decimal(0))

    @property
    def operating_total(self) -> Decimal:
        return self.rent + self.other_total + self.independence + self.depreciation
