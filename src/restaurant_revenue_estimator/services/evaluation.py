"""評価の実行: カレンダー → 昼 → 夜 → 売上 → 費用 → 損益."""

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from restaurant_revenue_estimator import __version__
from restaurant_revenue_estimator.engine.calendar import (
    CalendarSeries,
    ServiceHours,
    calendars_for_store,
    load_holidays,
)
from restaurant_revenue_estimator.engine.cost import (
    FixedCosts,
    PartTimeShift,
    StaffRole,
    full_time_cost,
    owner_labor,
    part_time_cost,
    statutory_cost,
    variable_costs,
)
from restaurant_revenue_estimator.engine.dinner import CourseSpec, plan_dinner
from restaurant_revenue_estimator.engine.lunch import simulate_lunch_day
from restaurant_revenue_estimator.engine.pnl import PnL, build_pnl
from restaurant_revenue_estimator.engine.revenue import (
    CourseSales,
    DaySales,
    RevenueResult,
    ServiceSales,
    aggregate,
    realized_course_price,
)
from restaurant_revenue_estimator.engine.tables import Floor, FloorPolicyName, Table
from restaurant_revenue_estimator.evidence.models import SourceStatus
from restaurant_revenue_estimator.inputs import StoreInputs, input_hash, load_store

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class EvaluationError(Exception):
    """評価できない入力."""


@dataclass(frozen=True)
class EvaluationRequest:
    store_id: str
    assumption_set: str = "standard"
    year: int = 2026
    calendar_mode: str = "steady"
    seed: int = 42
    replications: int = 20
    overrides: dict[str, Any] | None = None
    saturday_closure: str | None = None


@dataclass(frozen=True)
class EvaluationMeta:
    input_hash: str
    model_version: str
    calendar_sha256: str
    seed: int
    replications: int
    overrides: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_hash": self.input_hash,
            "model_version": self.model_version,
            "calendar_sha256": self.calendar_sha256,
            "seed": self.seed,
            "replications": self.replications,
            "overrides": self.overrides,
        }


@dataclass(frozen=True)
class EvaluationResult:
    store_id: str
    variant: str
    year: int
    calendar_mode: str
    assumption_set: str
    revenue: RevenueResult
    pnl: PnL
    open_days: int
    lunch_guests_by_weekday: dict[str, int]
    dinner_guests: int
    lost_lunch_guests: float
    tables: tuple[Table, ...]
    meta: EvaluationMeta
    unresolved: tuple[str, ...] = ()

    @property
    def gross_inc_tax(self) -> int:
        return self.revenue.total_inc_tax

    @property
    def gross_ex_tax(self) -> int:
        return self.revenue.total_ex_tax


def plain(value: Any) -> Any:
    """ProvenancedValue の dict なら value を返す."""
    if isinstance(value, dict) and "status" in value and "value" in value:
        return value["value"]
    return value


def _assumptions(inputs: StoreInputs, name: str) -> dict[str, dict[str, Any]]:
    key = f"assumptions/{name}.yaml"
    raw = inputs.files.get(key)
    if not isinstance(raw, dict):
        raise EvaluationError(f"{key} が無い")
    return raw


def _assumption_value(raw: dict[str, Any]) -> Any:
    return raw.get("value")


def _floors(
    inputs: StoreInputs,
) -> tuple[list[Floor], list[Table], list[tuple[str, list[Table]]]]:
    """(フロア方針, 1 階などの固定卓, 2 階候補ごとの全卓) を返す."""
    layout = inputs.files.get("layout.yaml", {}) or {}
    floors_raw = layout.get("floors")
    variants: list[tuple[str, list[Table]]] = []
    floors: list[Floor] = []

    def add_floor(index: int, raw: dict[str, Any]) -> None:
        policy = plain(raw.get("open_policy")) or "always"
        policy_name: FloorPolicyName = (
            cast(FloorPolicyName, policy)
            if policy in ("always", "when_lower_full", "never")
            else "always"
        )
        floors.append(Floor(str(raw.get("id", f"floor{index}")), policy_name))

    def parse_tables(items: list[dict[str, Any]]) -> list[Table]:
        return [
            Table(
                str(item["id"]),
                int(item["seats"]),
                str(item.get("floor", item.get("floor_id", ""))),
            )
            for item in items
        ]

    if isinstance(floors_raw, dict) and "status" in floors_raw:
        candidates = floors_raw.get("candidates") or {}
        for name, floor_list in candidates.items():
            tables: list[Table] = []
            for index, raw in enumerate(floor_list):
                floor_id = str(raw.get("id", f"floor{index}"))
                for item in raw.get("tables", []):
                    tables.append(Table(str(item["id"]), int(item["seats"]), floor_id))
            variants.append((str(name), tables))
        return floors, [], variants

    fixed: list[Table] = []
    unresolved_floor: tuple[int, dict[str, Any]] | None = None
    for index, raw in enumerate(floors_raw or []):
        add_floor(index, raw)
        floor_id = str(raw.get("id", f"floor{index}"))
        tables_raw = raw.get("tables", {})
        if (
            isinstance(tables_raw, dict)
            and tables_raw.get("status") == SourceStatus.UNRESOLVED.value
        ):
            unresolved_floor = (index, raw)
            continue
        items = tables_raw.get("value") if isinstance(tables_raw, dict) else tables_raw
        for item in items or []:
            fixed.append(Table(str(item["id"]), int(item["seats"]), floor_id))

    if unresolved_floor is None:
        return floors, fixed, [("default", fixed)]

    index, raw = unresolved_floor
    floor_id = str(raw.get("id", f"floor{index}"))
    candidates = raw["tables"].get("candidates") or {}
    for name, items in candidates.items():
        tables = [
            *fixed,
            *[Table(str(item["id"]), int(item["seats"]), floor_id) for item in items],
        ]
        variants.append((str(name), tables))
    return floors, fixed, variants


def _courses(
    inputs: StoreInputs, discount_rate: Decimal
) -> tuple[dict[str, CourseSpec], dict[str, int]]:
    menu = inputs.files.get("menu.yaml", {}) or {}
    specs: dict[str, CourseSpec] = {}
    prices: dict[str, int] = {}
    for course_id, raw in (menu.get("courses") or {}).items():
        normal = int(plain(raw["normal_price"]))
        discounted = int(plain(raw["discounted_price"]))
        price = realized_course_price(normal, discounted, discount_rate)
        specs[str(course_id)] = CourseSpec(
            id=str(course_id),
            name=str(raw.get("name", course_id)),
            price=price,
            duration_minutes=int(plain(raw["duration_minutes"])),
            min_guests=int(raw.get("min_guests", 2)),
            max_guests=int(raw.get("max_guests", 54)),
            includes_drink=bool(plain(raw.get("includes_drink", True))),
        )
        prices[str(course_id)] = price
    return specs, prices


def apply_overrides(
    assumptions: dict[str, dict[str, Any]], overrides: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """上書きできるのは ASSUMPTION か UNRESOLVED のキーだけ."""
    result = {key: dict(value) for key, value in assumptions.items()}
    for key, value in overrides.items():
        if key not in result:
            raise EvaluationError(f"上書きできないキー: {key}")
        status = result[key].get("status")
        if status not in (SourceStatus.ASSUMPTION.value, SourceStatus.UNRESOLVED.value):
            raise EvaluationError(f"上書きできないキー: {key}（status {status}）")
        result[key]["value"] = value
        if status == SourceStatus.UNRESOLVED.value:
            result[key]["candidates"] = {}
        result[key]["status"] = SourceStatus.ASSUMPTION.value
        result[key]["sources"] = [f"scenario:{key}"]
    return result


def _calendar(
    inputs: StoreInputs,
    *,
    year: int,
    mode: str,
    data_dir: Path,
    saturday_closure: str | None,
) -> list[CalendarSeries]:
    holidays = load_holidays(
        data_dir / "calendars" / "jp_holidays_cao.csv",
        data_dir / "calendars" / "jp_holidays_cao.meta.yaml",
        year,
    )
    if mode == "steady":
        return calendars_for_store(inputs, year=year, mode=mode, holidays=holidays)
    series = calendars_for_store(
        inputs,
        year=year,
        mode=mode,
        holidays=holidays,
        saturday_closure_start=saturday_closure,
    )
    return series


def _hours_minutes(hours: ServiceHours, base: str) -> tuple[int, int]:
    open_hour, open_minute = (int(part) for part in hours.open.split(":"))
    close_hour, close_minute = (int(part) for part in hours.close.split(":"))
    base_hour, base_minute = (int(part) for part in base.split(":"))
    start = open_hour * 60 + open_minute - (base_hour * 60 + base_minute)
    end = close_hour * 60 + close_minute - (base_hour * 60 + base_minute)
    return start, end


def evaluate(
    data_dir: Path,
    request: EvaluationRequest,
) -> list[EvaluationResult]:
    """評価を実行する. UNRESOLVED の候補ごとに結果を返す."""
    directory = data_dir / "stores" / request.store_id
    if not directory.exists():
        raise EvaluationError(f"店舗が無い: {request.store_id}")
    inputs = load_store(directory)
    overrides = dict(request.overrides or {})

    assumptions = apply_overrides(
        _assumptions(inputs, request.assumption_set),
        {key: value for key, value in overrides.items() if key != "layout.floor2"},
    )
    floor_choice = overrides.get("layout.floor2")

    discount_rate = Decimal(
        str(_assumption_value(assumptions["discount_application_rate"]))
    )
    course_specs, _ = _courses(inputs, discount_rate)
    menu = inputs.files.get("menu.yaml", {}) or {}
    appetizer_price = int(plain((menu.get("appetizer") or {}).get("price", 0)) or 0)
    appetizer_waived = bool(
        plain((menu.get("appetizer") or {}).get("waived_for_course", True))
    )

    calendars = _calendar(
        inputs,
        year=request.year,
        mode=request.calendar_mode,
        data_dir=data_dir,
        saturday_closure=request.saturday_closure,
    )
    calendar = calendars[0]
    if request.calendar_mode == "steady":
        calendar = _calendar(
            inputs,
            year=request.year,
            mode=request.calendar_mode,
            data_dir=data_dir,
            saturday_closure=None,
        )[0]

    floors, _, variants = _floors(inputs)
    if floor_choice and floor_choice != "default":
        variants = [item for item in variants if item[0] == floor_choice] or variants

    results: list[EvaluationResult] = []
    for variant_name, tables in variants:
        results.append(
            _evaluate_variant(
                inputs=inputs,
                request=request,
                assumptions=assumptions,
                course_specs=course_specs,
                appetizer_price=appetizer_price,
                appetizer_waived=appetizer_waived,
                calendar=calendar,
                floors=floors,
                tables=tables,
                variant=variant_name,
                overrides=overrides,
                data_dir=data_dir,
            )
        )
    return results


def _evaluate_variant(  # noqa: PLR0915
    *,
    inputs: StoreInputs,
    request: EvaluationRequest,
    assumptions: dict[str, dict[str, Any]],
    course_specs: dict[str, CourseSpec],
    appetizer_price: int,
    appetizer_waived: bool,
    calendar: CalendarSeries,
    floors: list[Floor],
    tables: list[Table],
    variant: str,
    overrides: dict[str, Any],
    data_dir: Path,
) -> EvaluationResult:
    values = {key: _assumption_value(raw) for key, raw in assumptions.items()}

    lunch_guests_by_weekday: dict[str, int] = {}
    lost_lunch = 0.0
    lunch_days = [day for day in calendar.open_days if day.lunch is not None]
    if lunch_days:
        for index, weekday in enumerate(WEEKDAYS):
            if not any(day.weekday == weekday for day in lunch_days):
                continue
            sample = next(day for day in lunch_days if day.weekday == weekday)
            assert sample.lunch is not None
            _, end = _hours_minutes(sample.lunch, sample.lunch.open)
            last_entry = end - 30
            lunch = simulate_lunch_day(
                tables=tables,
                floors=floors,
                total_groups=float(values["lunch_arrival_groups"]),
                group_sizes=[int(key) for key in values["group_size_distribution"]],
                group_probs=[
                    float(value) for value in values["group_size_distribution"].values()
                ],
                stay_minutes=int(values["lunch_stay_minutes"]),
                cleanup_minutes=int(values["cleanup_minutes"]),
                max_wait_minutes=int(values["max_wait_minutes"]),
                last_entry_minute=last_entry,
                close_minute=end,
                replications=max(1, request.replications),
                seed=request.seed + index,
            )
            lunch_guests_by_weekday[weekday] = round(lunch.guests)
            lost_lunch += lunch.lost

    seasons = [float(value) for value in values["dinner_seasonality"]]
    demand_by_weekday = values["dinner_demand_by_weekday"]
    dinner_ala_ticket = values["dinner_ala_ticket"]
    base_course_ratio = float(values["course_ratio"])
    lunch_ticket = values["lunch_avg_ticket"]

    days: list[DaySales] = []
    dinner_guests = 0
    for day in calendar.open_days:
        lunch_sales = ServiceSales()
        if day.lunch is not None:
            guests = lunch_guests_by_weekday.get(day.weekday, 0)
            lunch_sales = ServiceSales(ala_guests=guests, ala_ticket=lunch_ticket)
        dinner_sales = ServiceSales()
        if day.dinner is not None:
            base = float(demand_by_weekday.get(day.weekday, 0))
            guests = max(0, round(base * seasons[day.day.month - 1]))
            plan = plan_dinner(
                day=day.day,
                guests=guests,
                courses=course_specs,
                tables=tables,
                base_ratio=base_course_ratio,
                start_minute=0,
            )
            dinner_guests += plan.total_guests
            dinner_sales = ServiceSales(
                ala_guests=plan.ala_guests,
                ala_ticket=dinner_ala_ticket,
                courses=tuple(
                    CourseSales(guests=count, unit_price=course_specs[course_id].price)
                    for course_id, count in plan.course_guests.items()
                ),
                appetizer_yen=appetizer_price,
                appetizer_waived_for_course=appetizer_waived,
            )
        days.append(DaySales(day=day.day, lunch=lunch_sales, dinner=dinner_sales))

    revenue = aggregate(days)

    open_count = max(1, len(calendar.open_days))
    full_time_roles = [
        StaffRole(str(item["role"]), int(item["count"]), int(item["monthly_salary"]))
        for item in values["full_time_staff"]
    ]
    part_time = values["part_time_staff"]
    shifts = [
        PartTimeShift(
            heads=int(part_time["heads_per_day"]),
            hours_per_day=Decimal(str(part_time["hours_per_day"])),
            hourly_wage=Decimal(str(part_time["hourly_wage"])),
            late_hours_per_day=Decimal(str(part_time.get("late_hours_per_day", 0))),
        )
    ]
    full_time_annual = full_time_cost(full_time_roles) * 12
    part_time_annual = part_time_cost(shifts, days=open_count)
    labor_annual = full_time_annual + part_time_annual
    benefits = statutory_cost(labor_annual, flat_rate=Decimal("0.15"))
    owner = (
        owner_labor(
            Decimal(str(values["owner_hours_monthly"])),
            Decimal(str(values["owner_virtual_wage"])),
        )
        * 12
    )

    other_raw = values["other_expenses_monthly"]
    fixed = FixedCosts(
        rent=Decimal(str(values["rent_monthly"])) * 12,
        other={key: Decimal(str(value)) * 12 for key, value in other_raw.items()},
        independence=Decimal(str(values["independence_cost_monthly"])) * 12,
        depreciation=Decimal(str(values["depreciation_monthly"])) * 12,
        interest=Decimal(str(values["loan_interest_monthly"])) * 12,
        principal=Decimal(str(values["loan_principal_monthly"])) * 12,
        capex=Decimal(str(values["capex_monthly"])) * 12,
    )
    variable = variable_costs(
        ala_revenue=revenue.ala_inc_tax,
        course_revenue=revenue.course_inc_tax,
        cost_rate_ala=Decimal(str(values["cost_rate_ala"])),
        cost_rate_course=Decimal(str(values["cost_rate_course"])),
    )
    pnl = build_pnl(
        gross_inc_tax=revenue.total_inc_tax,
        gross_ex_tax=revenue.total_ex_tax,
        variable=variable,
        labor=labor_annual + benefits,
        owner=owner,
        fixed=fixed,
    )

    csv_path = data_dir / "calendars" / "jp_holidays_cao.csv"
    csv_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    meta = EvaluationMeta(
        input_hash=input_hash(inputs, overrides),
        model_version=__version__,
        calendar_sha256=csv_sha,
        seed=request.seed,
        replications=request.replications,
        overrides=overrides,
    )
    unresolved = tuple(
        key
        for key, raw in assumptions.items()
        if raw.get("status") == SourceStatus.UNRESOLVED.value
    )
    return EvaluationResult(
        store_id=request.store_id,
        variant=variant,
        year=request.year,
        calendar_mode=request.calendar_mode,
        assumption_set=request.assumption_set,
        revenue=revenue,
        pnl=pnl,
        open_days=len(calendar.open_days),
        lunch_guests_by_weekday=lunch_guests_by_weekday,
        dinner_guests=dinner_guests,
        lost_lunch_guests=lost_lunch,
        tables=tuple(tables),
        meta=meta,
        unresolved=unresolved,
    )
