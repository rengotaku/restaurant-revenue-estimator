"""人件費・原価・固定費."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

MINIMUM_WAGE_TOKYO: tuple[tuple[date, int], ...] = (
    (date(2025, 10, 1), 1226),
    (date(2026, 10, 1), 1280),
)


@dataclass(frozen=True)
class StaffRole:
    role: str
    count: int
    monthly_salary: int


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
    total = Decimal(0)
    hour = start_hour
    while hour < end_hour:
        normalised = hour % Decimal(24)
        late = normalised >= Decimal(22) or normalised < Decimal(5)
        rate = hourly_wage * premium if late else hourly_wage
        total += rate
        hour += Decimal(1)
    return total


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
