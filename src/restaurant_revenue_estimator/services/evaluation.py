"""評価の実行: カレンダー → 昼 → 夜 → 売上 → 費用 → 損益."""

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from restaurant_revenue_estimator import __version__
from restaurant_revenue_estimator.engine.calendar import (
    CalendarSeries,
    DaySchedule,
    ServiceHours,
    calendars_for_store,
    load_holidays,
)
from restaurant_revenue_estimator.engine.cost import (
    FixedCosts,
    LaborBreakdown,
    ShiftSpec,
    StaffRole,
    cost_of_goods,
    full_time_cost,
    labor_warnings,
    owner_labor,
    part_time_wages,
    statutory_cost,
)
from restaurant_revenue_estimator.engine.dinner import (
    CourseSpec,
    can_extend,
    simulate_dinner_day,
)
from restaurant_revenue_estimator.engine.lunch import (
    lunch_arrival_groups_for_weekday,
    lunch_occupancy_stats,
    simulate_lunch_day,
)
from restaurant_revenue_estimator.engine.pnl import MonthlyPnL, PnL, build_monthly_pnl
from restaurant_revenue_estimator.engine.revenue import (
    CourseSales,
    DaySales,
    MonthRevenue,
    RevenueResult,
    SeasonalityRegistry,
    ServiceSales,
    aggregate,
    realized_course_price,
)
from restaurant_revenue_estimator.engine.tables import Floor, FloorPolicyName, Table
from restaurant_revenue_estimator.evidence.models import SourceStatus
from restaurant_revenue_estimator.inputs import StoreInputs, input_hash, load_store
from restaurant_revenue_estimator.services.screens import build_cost_breakdown

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
    saturday_hours_candidate: str | None = None


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
    pnl: MonthlyPnL
    open_days: int
    lunch_guests_by_weekday: dict[str, int]
    dinner_guests: int
    lost_lunch_guests: float
    tables: tuple[Table, ...]
    meta: EvaluationMeta
    unresolved: tuple[str, ...] = ()
    dinner_desired_guests: int = 0
    dinner_lost_guests: int = 0
    dinner_lost_by_reason: dict[str, int] = field(default_factory=dict)
    seasonality_targets: dict[str, str] = field(default_factory=dict)
    labor_breakdown: LaborBreakdown | None = None
    warnings: tuple[str, ...] = ()
    choices: dict[str, str] = field(default_factory=dict)
    days: tuple[DaySales, ...] = ()
    occupied_people: tuple[float, ...] = ()
    lunch_occupancy_max: float = 0.0
    lunch_occupancy_12_13_avg: float = 0.0
    lunch_weekday_used: str = "月曜"
    cost_breakdown: dict[str, Any] = field(default_factory=dict)

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


def floor1_table_counts(inputs: StoreInputs) -> tuple[int, int]:
    """Return (count_4p, count_2p) for 1st floor tables (OBSERVED)."""
    layout = inputs.files.get("layout.yaml", {}) or {}
    floors = layout.get("floors", [])
    if not isinstance(floors, list):
        return 0, 0
    for floor in floors:
        if isinstance(floor, dict) and floor.get("id") == "1f":
            tables = floor.get("tables", {})
            items = tables.get("value") if isinstance(tables, dict) else tables
            if isinstance(items, list):
                four_p = sum(
                    1
                    for t in items
                    if isinstance(t, dict) and t.get("seats") == 4
                )
                two_p = sum(
                    1
                    for t in items
                    if isinstance(t, dict) and t.get("seats") == 2
                )
                return four_p, two_p
    return 0, 0


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
    saturday_closure: str | None = None,
    saturday_hours_candidate: str | None = None,
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
        saturday_hours_candidate=saturday_hours_candidate,
    )
    return series


def _hours_minutes(hours: ServiceHours, base: str) -> tuple[int, int]:
    open_hour, open_minute = (int(part) for part in hours.open.split(":"))
    close_hour, close_minute = (int(part) for part in hours.close.split(":"))
    base_hour, base_minute = (int(part) for part in base.split(":"))
    start = open_hour * 60 + open_minute - (base_hour * 60 + base_minute)
    end = close_hour * 60 + close_minute - (base_hour * 60 + base_minute)
    return start, end


def _resolve_calendar_candidates(
    inputs: StoreInputs,
    request: EvaluationRequest,
    overrides: dict[str, Any],
) -> tuple[list[str | None], list[str | None]]:
    if request.calendar_mode == "steady":
        return [None], [None]

    closure_candidates: list[str | None]
    if request.saturday_closure is not None:
        closure_candidates = [request.saturday_closure]
    elif "saturday_closure_start" in overrides:
        closure_candidates = [str(overrides["saturday_closure_start"])]
    else:
        operations = inputs.files.get("operations.yaml", {}) or {}
        raw_closure = operations.get("saturday_closure_start") or {}
        if (
            isinstance(raw_closure, dict)
            and raw_closure.get("status") == SourceStatus.UNRESOLVED.value
            and raw_closure.get("candidates")
        ):
            closure_candidates = list(raw_closure["candidates"].keys())
        else:
            closure_candidates = [None]

    hours_candidates: list[str | None]
    if request.saturday_hours_candidate is not None:
        hours_candidates = [request.saturday_hours_candidate]
    elif "saturday_hours_before_closure" in overrides:
        hours_candidates = [str(overrides["saturday_hours_before_closure"])]
    else:
        operations = inputs.files.get("operations.yaml", {}) or {}
        raw_hours = operations.get("saturday_hours_before_closure") or {}
        if (
            isinstance(raw_hours, dict)
            and raw_hours.get("status") == SourceStatus.UNRESOLVED.value
            and raw_hours.get("candidates")
        ):
            hours_candidates = list(raw_hours["candidates"].keys())
        else:
            hours_candidates = [None]

    return closure_candidates, hours_candidates


def _build_calendars_map(
    inputs: StoreInputs,
    request: EvaluationRequest,
    data_dir: Path,
    closure_candidates: list[str | None],
    hours_candidates: list[str | None],
) -> dict[tuple[str | None, str | None], CalendarSeries]:
    calendars_map: dict[tuple[str | None, str | None], CalendarSeries] = {}
    for closure_cand in closure_candidates:
        for hours_cand in hours_candidates:
            if request.calendar_mode == "steady":
                cals = _calendar(
                    inputs,
                    year=request.year,
                    mode="steady",
                    data_dir=data_dir,
                    saturday_closure=None,
                    saturday_hours_candidate=None,
                )
            else:
                cals = _calendar(
                    inputs,
                    year=request.year,
                    mode=request.calendar_mode,
                    data_dir=data_dir,
                    saturday_closure=closure_cand,
                    saturday_hours_candidate=hours_cand,
                )
            calendars_map[(closure_cand, hours_cand)] = cals[0]
    return calendars_map


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

    floors, fixed, variants = _floors(inputs)
    if isinstance(floor_choice, list):
        custom = [
            Table(str(item["id"]), int(item["seats"]), "2f") for item in floor_choice
        ]
        variants = [("custom", [*fixed, *custom])]
    elif floor_choice and floor_choice != "default":
        variants = [item for item in variants if item[0] == floor_choice] or variants

    closure_candidates, hours_candidates = _resolve_calendar_candidates(
        inputs, request, overrides
    )
    calendars_map = _build_calendars_map(
        inputs, request, data_dir, closure_candidates, hours_candidates
    )

    results: list[EvaluationResult] = []
    for floor_name, tables in variants:
        for closure_cand in closure_candidates:
            for hours_cand in hours_candidates:
                calendar = calendars_map[(closure_cand, hours_cand)]
                if request.calendar_mode == "steady":
                    variant_name = floor_name
                    choices: dict[str, str] = {"layout.floor2": floor_name}
                else:
                    name_parts = [floor_name]
                    if closure_cand is not None:
                        name_parts.append(str(closure_cand))
                    if hours_cand is not None:
                        name_parts.append(str(hours_cand))
                    variant_name = "/".join(name_parts)

                    choices = {"layout.floor2": floor_name}
                    if closure_cand is not None:
                        choices["saturday_closure_start"] = str(closure_cand)
                    if hours_cand is not None:
                        choices["saturday_hours_before_closure"] = str(hours_cand)

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
                        choices=choices,
                    )
                )
    return results


def _validate_dinner_assumptions(
    values: dict[str, Any], dinner_days: Sequence[DaySchedule]
) -> None:
    stay = values.get("dinner_stay_minutes")
    if not isinstance(stay, (int, float)) or not math.isfinite(stay) or stay <= 0:
        raise EvaluationError("dinner_stay_minutes は正の数値")

    for key in ("dinner_peak_share", "course_extension_rate"):
        rate = values.get(key)
        if (
            not isinstance(rate, (int, float))
            or not math.isfinite(rate)
            or not (0.0 <= rate <= 1.0)
        ):
            raise EvaluationError(f"{key} は有限で 0〜1")

    csw = values.get("course_start_window")
    if not isinstance(csw, dict) or "start" not in csw or "end" not in csw:
        raise EvaluationError("course_start_window は start と end を持つ dict")
    start_str = str(csw["start"])
    end_str = str(csw["end"])

    time_pattern = r"^([01]\d|2[0-3]):[0-5]\d$"
    if not re.match(time_pattern, start_str) or not re.match(time_pattern, end_str):
        raise EvaluationError("course_start_window は HH:MM 形式")

    if start_str > end_str:
        raise EvaluationError("course_start_window は start <= end")

    for day in dinner_days:
        if day.dinner is not None and (
            start_str < day.dinner.open or end_str > day.dinner.last_entry
        ):
            raise EvaluationError(
                f"course_start_window ({start_str}〜{end_str}) が営業範囲 "
                f"({day.dinner.open}〜{day.dinner.last_entry}) 外"
            )


DinnerComboKey = tuple[str, int, bool, str, str, str]


def group_dinner_days(
    days: Sequence[DaySchedule], holidays: set[date] | dict[date, Any]
) -> dict[DinnerComboKey, list[DaySchedule]]:
    """夜の営業日を（曜日, 月, 延長可否, 営業時間）の組合せごとにグループ化する."""
    h_set = set(holidays.keys()) if isinstance(holidays, dict) else holidays
    combos: dict[DinnerComboKey, list[DaySchedule]] = {}
    for day in days:
        if day.dinner is None:
            continue
        can_ext = can_extend(day.day, h_set)
        key: DinnerComboKey = (
            day.weekday,
            day.day.month,
            can_ext,
            day.dinner.open,
            day.dinner.last_entry,
            day.dinner.close,
        )
        combos.setdefault(key, []).append(day)
    return combos


def _evaluate_dinner(  # noqa: PLR0915
    *,
    calendar: CalendarSeries,
    tables: Sequence[Table],
    floors: Sequence[Floor],
    course_specs: dict[str, CourseSpec],
    dinner_ala_ticket: int,
    appetizer_price: int,
    appetizer_waived: bool,
    values: dict[str, Any],
    holidays: set[date],
    request: EvaluationRequest,
) -> tuple[
    dict[date, ServiceSales],
    int,
    int,
    int,
    dict[str, int],
    dict[str, str],
]:
    dinner_days = [day for day in calendar.open_days if day.dinner is not None]
    if not dinner_days:
        return {}, 0, 0, 0, {}, {}

    _validate_dinner_assumptions(values, dinner_days)

    registry = SeasonalityRegistry()
    seasons = [float(v) for v in values["dinner_seasonality"]]
    demand_by_weekday = values["dinner_demand_by_weekday"]
    base_course_ratio = float(values["course_ratio"])

    combos = group_dinner_days(dinner_days, holidays)

    month_effects = [
        float(v) for v in values.get("course_ratio_month_effect", [0.0] * 12)
    ]
    weekday_effects = {
        k: float(v)
        for k, v in values.get(
            "course_ratio_weekday_effect", dict.fromkeys(WEEKDAYS, 0.0)
        ).items()
    }

    combo_results: dict[DinnerComboKey, Any] = {}
    sorted_combos = sorted(combos.keys())

    for combo_idx, combo_key in enumerate(sorted_combos):
        weekday, month, can_ext, d_open, d_last_entry, d_close = combo_key
        sample_day = combos[combo_key][0]
        assert sample_day.dinner is not None

        # 1. 来店希望人数 D
        season_factor = float(
            registry.apply(
                "dinner_seasonality", Decimal(str(seasons[month - 1])), "guests"
            )
        )
        base_demand = float(demand_by_weekday.get(weekday, 0))
        d_guests = max(0, round(base_demand * season_factor))

        # 2. コース比率 r
        b_month = month_effects[month - 1]
        b_weekday = weekday_effects.get(weekday, 0.0)
        if base_course_ratio in (0.0, 1.0):
            r_course = base_course_ratio
        else:
            logit = (
                math.log(base_course_ratio / (1.0 - base_course_ratio))
                + b_month
                + b_weekday
            )
            r_course = 1.0 / (1.0 + math.exp(-logit))

        open_clock = d_open
        open_h, open_m = map(int, d_open.split(":"))
        close_h, close_m = map(int, d_close.split(":"))
        last_h, last_m = map(int, d_last_entry.split(":"))
        open_total = open_h * 60 + open_m
        close_min = (close_h * 60 + close_m) - open_total
        last_entry_min = (last_h * 60 + last_m) - open_total

        combo_seed = request.seed + combo_idx * 1000

        res = simulate_dinner_day(
            tables=tables,
            floors=floors,
            desired_guests=d_guests,
            course_ratio=r_course,
            courses=course_specs,
            dinner_stay_minutes=int(values["dinner_stay_minutes"]),
            dinner_peak_share=float(values["dinner_peak_share"]),
            dinner_group_size_distribution={
                int(k): float(v)
                for k, v in values["dinner_group_size_distribution"].items()
            },
            course_party_size_distribution={
                int(k): float(v)
                for k, v in values["course_party_size_distribution"].items()
            },
            course_start_window=values["course_start_window"],
            course_extension_rate=float(values["course_extension_rate"]),
            can_extend_today=can_ext,
            cleanup_minutes=int(values["cleanup_minutes"]),
            max_wait_minutes=int(values["max_wait_minutes"]),
            open_minute=0,
            last_entry_minute=last_entry_min,
            close_minute=close_min,
            open_clock=open_clock,
            replications=max(1, request.replications),
            seed=combo_seed,
        )
        combo_results[combo_key] = res

    dinner_sales_by_date: dict[date, ServiceSales] = {}
    dinner_guests = 0
    total_dinner_desired = 0
    total_dinner_lost = 0.0
    dinner_lost_by_reason_accum: dict[str, float] = {}

    for day in dinner_days:
        assert day.dinner is not None
        can_ext = can_extend(day.day, holidays)
        combo_key = (
            day.weekday,
            day.day.month,
            can_ext,
            day.dinner.open,
            day.dinner.last_entry,
            day.dinner.close,
        )
        res = combo_results[combo_key]

        total_dinner_desired += round(res.walk_in_desired + res.course_desired)
        total_dinner_lost += res.lost_guests
        for reason, count in res.lost_by_reason.items():
            dinner_lost_by_reason_accum[reason] = (
                dinner_lost_by_reason_accum.get(reason, 0.0) + count
            )

        ala_guests_int = round(res.walk_in_entered)
        course_guests_int = {
            c_id: round(res.course_entered_by_course.get(c_id, 0.0))
            for c_id in course_specs
        }
        total_day_dinner = ala_guests_int + sum(course_guests_int.values())
        dinner_guests += total_day_dinner

        dinner_sales_by_date[day.day] = ServiceSales(
            ala_guests=ala_guests_int,
            ala_ticket=dinner_ala_ticket,
            courses=tuple(
                CourseSales(guests=count, unit_price=course_specs[course_id].price)
                for course_id, count in course_guests_int.items()
                if count > 0
            ),
            appetizer_yen=appetizer_price,
            appetizer_waived_for_course=appetizer_waived,
        )

    dinner_lost_by_reason = {
        reason: round(count) for reason, count in dinner_lost_by_reason_accum.items()
    }
    seasonality_targets = dict(registry._targets)

    return (
        dinner_sales_by_date,
        dinner_guests,
        total_dinner_desired,
        round(total_dinner_lost),
        dinner_lost_by_reason,
        seasonality_targets,
    )


def _compute_pnl_and_labor(
    *,
    year: int,
    revenue: RevenueResult,
    calendar: CalendarSeries,
    values: dict[str, Any],
) -> tuple[MonthlyPnL, LaborBreakdown, tuple[str, ...]]:
    full_time_roles = [
        StaffRole(str(item["role"]), int(item["count"]), int(item["monthly_salary"]))
        for item in values.get("full_time_staff", [])
    ]
    shift_specs = [
        ShiftSpec(
            id=str(item["id"]),
            weekdays=tuple(item["weekdays"]),
            service=str(item["service"]),
            start=str(item["start"]),
            end=str(item["end"]),
            heads=int(item["heads"]),
            hourly_wage=Decimal(str(item["hourly_wage"])),
        )
        for item in values.get("part_time_shifts", [])
    ]
    late_premium = Decimal(str(values.get("late_night_premium", "1.25")))
    statutory_rate = Decimal(str(values["statutory_benefit_rate"]))
    bonus_months = Decimal(str(values.get("bonus_months_per_year", 0)))
    commute_full_time = Decimal(str(values.get("commuting_monthly_per_full_time", 0)))
    commute_part_time = Decimal(str(values.get("commuting_per_part_time_shift", 0)))

    warnings = tuple(labor_warnings(shift_specs, calendar.days))

    rev_by_month = {m.month: m for m in revenue.months}
    cost_rate_ala = Decimal(str(values["cost_rate_ala"]))
    cost_rate_course = Decimal(str(values["cost_rate_course"]))
    owner_hours_monthly = Decimal(str(values["owner_hours_monthly"]))
    owner_virtual_wage = Decimal(str(values["owner_virtual_wage"]))
    owner_m = owner_labor(owner_hours_monthly, owner_virtual_wage)

    other_raw = values["other_expenses_monthly"]
    monthly_fixed = FixedCosts(
        rent=Decimal(str(values["rent_monthly"])),
        other={key: Decimal(str(value)) for key, value in other_raw.items()},
        independence=Decimal(str(values["independence_cost_monthly"])),
        depreciation=Decimal(str(values["depreciation_monthly"])),
        interest=Decimal(str(values["loan_interest_monthly"])),
        principal=Decimal(str(values["loan_principal_monthly"])),
        capex=Decimal(str(values["capex_monthly"])),
    )

    ft_monthly = full_time_cost(full_time_roles)
    total_ft_heads = sum(r.count for r in full_time_roles)
    commute_ft_m = Decimal(total_ft_heads) * commute_full_time
    bonus_m = (ft_monthly * bonus_months) / Decimal(12)

    monthly_pnls: list[PnL] = []
    monthly_ft: list[Decimal] = []
    monthly_bonus: list[Decimal] = []
    monthly_pt: list[Decimal] = []
    monthly_commute: list[Decimal] = []
    monthly_statutory: list[Decimal] = []

    for month_num in range(1, 13):
        month_days = [d for d in calendar.days if d.day.month == month_num]
        month_rev = rev_by_month.get(
            month_num,
            MonthRevenue(
                year=year,
                month=month_num,
                lunch_inc_tax=0,
                dinner_inc_tax=0,
                ala_inc_tax=0,
                course_inc_tax=0,
                appetizer_inc_tax=0,
                other_inc_tax=0,
                tax_10_inc=0,
                tax_8_inc=0,
            ),
        )
        pt_wages = part_time_wages(shift_specs, month_days, late_premium=late_premium)
        commute_pt_m = sum(
            (
                Decimal(s.heads)
                * commute_part_time
                * Decimal(pt_wages.counts_by_shift.get(s.id, 0))
                for s in shift_specs
            ),
            Decimal(0),
        )
        total_commute_m = commute_ft_m + commute_pt_m

        ft_m_yen = Decimal(round(ft_monthly))
        bonus_m_yen = Decimal(round(bonus_m))
        pt_m_yen = Decimal(round(pt_wages.total))
        commute_m_yen = Decimal(round(total_commute_m))

        statutory_base_m = ft_m_yen + bonus_m_yen + pt_m_yen
        statutory_m = statutory_cost(statutory_base_m, flat_rate=statutory_rate)
        statutory_m_yen = Decimal(round(statutory_m))

        labor_m_yen = (
            ft_m_yen + bonus_m_yen + pt_m_yen + statutory_m_yen + commute_m_yen
        )

        cogs_int = cost_of_goods(
            month_rev,
            cost_rate_ala=cost_rate_ala,
            cost_rate_course=cost_rate_course,
        )
        owner_m_yen = Decimal(round(owner_m))

        monthly_pnls.append(
            PnL(
                gross_inc_tax=month_rev.total_inc_tax,
                gross_ex_tax=month_rev.total_ex_tax,
                cogs=Decimal(cogs_int),
                labor=labor_m_yen,
                owner_labor=owner_m_yen,
                fixed=monthly_fixed,
                sales_tax=0,
                year=year,
                month=month_num,
            )
        )
        monthly_ft.append(ft_m_yen)
        monthly_bonus.append(bonus_m_yen)
        monthly_pt.append(pt_m_yen)
        monthly_commute.append(commute_m_yen)
        monthly_statutory.append(statutory_m_yen)

    pnl = build_monthly_pnl(monthly_pnls)

    annual_breakdown = LaborBreakdown(
        full_time=sum(monthly_ft, Decimal(0)),
        bonus=sum(monthly_bonus, Decimal(0)),
        part_time=sum(monthly_pt, Decimal(0)),
        statutory=sum(monthly_statutory, Decimal(0)),
        commute=sum(monthly_commute, Decimal(0)),
    )
    return pnl, annual_breakdown, warnings


def _evaluate_variant(
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
    choices: dict[str, str] | None = None,
) -> EvaluationResult:
    values = {key: _assumption_value(raw) for key, raw in assumptions.items()}

    lunch_guests_by_weekday: dict[str, int] = {}
    total_weighted_lost_lunch = 0.0
    first_weekday_occupied: tuple[float, ...] = ()
    weekday_used = "月曜"
    lunch_days = [day for day in calendar.open_days if day.lunch is not None]
    total_lunch_days_count = len(lunch_days)
    if lunch_days:
        for index, weekday in enumerate(WEEKDAYS):
            days_for_weekday = sum(1 for day in lunch_days if day.weekday == weekday)
            if days_for_weekday == 0:
                continue
            sample = next(day for day in lunch_days if day.weekday == weekday)
            assert sample.lunch is not None
            _, end = _hours_minutes(sample.lunch, sample.lunch.open)
            last_entry = end - 30
            lunch = simulate_lunch_day(
                tables=tables,
                floors=floors,
                total_groups=lunch_arrival_groups_for_weekday(
                    float(values["lunch_arrival_groups"]),
                    values.get("lunch_arrival_weekday_factor"),
                    weekday,
                ),
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
                stay_ratio_by_group_size=values.get("lunch_stay_ratio_by_group_size"),
            )
            lunch_guests_by_weekday[weekday] = round(lunch.guests)
            total_weighted_lost_lunch += lunch.lost * days_for_weekday
            if not first_weekday_occupied and lunch.occupied_people:
                first_weekday_occupied = lunch.occupied_people
                jp_names = {
                    "mon": "月曜",
                    "tue": "火曜",
                    "wed": "水曜",
                    "thu": "木曜",
                    "fri": "金曜",
                    "sat": "土曜",
                    "sun": "日曜",
                }
                weekday_used = jp_names.get(weekday, weekday)

    lost_lunch_daily_avg = (
        total_weighted_lost_lunch / total_lunch_days_count
        if total_lunch_days_count > 0
        else 0.0
    )
    cost_breakdown = build_cost_breakdown(assumptions)
    max_occ, avg_12_13 = lunch_occupancy_stats(first_weekday_occupied)

    holidays = set(
        load_holidays(
            data_dir / "calendars" / "jp_holidays_cao.csv",
            data_dir / "calendars" / "jp_holidays_cao.meta.yaml",
            request.year,
        ).keys()
    )
    (
        dinner_sales_by_date,
        dinner_guests,
        dinner_desired_guests,
        dinner_lost_guests,
        dinner_lost_by_reason,
        seasonality_targets,
    ) = _evaluate_dinner(
        calendar=calendar,
        tables=tables,
        floors=floors,
        course_specs=course_specs,
        dinner_ala_ticket=int(values["dinner_ala_ticket"]),
        appetizer_price=appetizer_price,
        appetizer_waived=appetizer_waived,
        values=values,
        holidays=holidays,
        request=request,
    )

    lunch_ticket = values["lunch_avg_ticket"]
    days: list[DaySales] = []
    for day in calendar.open_days:
        lunch_sales = ServiceSales()
        if day.lunch is not None:
            guests = lunch_guests_by_weekday.get(day.weekday, 0)
            lunch_sales = ServiceSales(ala_guests=guests, ala_ticket=lunch_ticket)
        dinner_sales = dinner_sales_by_date.get(day.day, ServiceSales())
        days.append(DaySales(day=day.day, lunch=lunch_sales, dinner=dinner_sales))

    revenue = aggregate(days)

    pnl, annual_breakdown, warnings = _compute_pnl_and_labor(
        year=request.year,
        revenue=revenue,
        calendar=calendar,
        values=values,
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
        lost_lunch_guests=lost_lunch_daily_avg,
        tables=tuple(tables),
        meta=meta,
        unresolved=unresolved,
        dinner_desired_guests=dinner_desired_guests,
        dinner_lost_guests=dinner_lost_guests,
        dinner_lost_by_reason=dinner_lost_by_reason,
        seasonality_targets=seasonality_targets,
        labor_breakdown=annual_breakdown,
        warnings=warnings,
        choices=dict(choices or {}),
        days=tuple(days),
        occupied_people=first_weekday_occupied,
        lunch_occupancy_max=max_occ,
        lunch_occupancy_12_13_avg=avg_12_13,
        lunch_weekday_used=weekday_used,
        cost_breakdown=cost_breakdown,
    )


@dataclass(frozen=True)
class RunDocument:
    store_id: str
    year: int
    calendar_mode: str
    assumption_set: str
    meta: EvaluationMeta
    overrides: dict[str, Any]
    unresolved: tuple[str, ...]
    variants: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
    results: list[EvaluationResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "year": self.year,
            "calendar_mode": self.calendar_mode,
            "assumption_set": self.assumption_set,
            "meta": self.meta.as_dict() if hasattr(self.meta, "as_dict") else self.meta,
            "overrides": [
                {
                    "key": key,
                    "status": "ASSUMPTION",
                    "source": f"scenario:{key}",
                }
                for key in self.meta.overrides
            ],
            "unresolved": list(self.unresolved),
            "variants": self.variants,
            "scenarios": self.scenarios,
        }


def evaluate_run(
    data_dir: Path,
    request: EvaluationRequest,
) -> RunDocument:
    """悲観・標準・楽観を同じ seed・replications・年・暦・上書きで回し、
    全 variant と scenarios をまとめる.
    """
    scenarios = ("pessimistic", "standard", "optimistic")
    all_scenario_results: dict[str, list[EvaluationResult]] = {}

    for aset in scenarios:
        req = replace(request, assumption_set=aset)
        all_scenario_results[aset] = evaluate(data_dir, req)

    base_results = all_scenario_results[request.assumption_set]
    if not base_results:
        raise EvaluationError("評価結果が空です")

    from restaurant_revenue_estimator.report.markdown import (  # noqa: PLC0415
        result_to_dict,
    )

    variants_dicts: list[dict[str, Any]] = []
    for res in base_results:
        v_dict = {
            "name": res.variant,
            "choices": dict(res.choices),
            **result_to_dict(res),
        }
        variants_dicts.append(v_dict)

    scenarios_list: list[dict[str, Any]] = []
    for aset in scenarios:
        for res in all_scenario_results[aset]:
            scenarios_list.append(
                {
                    "assumption_set": aset,
                    "variant": res.variant,
                    "gross_inc_tax": res.gross_inc_tax,
                    "gross_ex_tax": res.gross_ex_tax,
                    "business_income": res.pnl.yen(res.pnl.business_income),
                }
            )

    return RunDocument(
        store_id=request.store_id,
        year=request.year,
        calendar_mode=request.calendar_mode,
        assumption_set=request.assumption_set,
        meta=base_results[0].meta,
        overrides=dict(request.overrides or {}),
        unresolved=base_results[0].unresolved,
        variants=variants_dicts,
        scenarios=scenarios_list,
        results=base_results,
    )
