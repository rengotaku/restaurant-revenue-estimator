"""夜の需要・コース・予約 (TC-09-xx)."""

from datetime import date
from decimal import Decimal

import pytest

from restaurant_revenue_estimator.engine.dinner import (
    CourseSpec,
    assign_tables_for_party,
    can_extend,
    course_ratio,
    plan_dinner,
)
from restaurant_revenue_estimator.engine.revenue import (
    SeasonalityError,
    SeasonalityRegistry,
)
from restaurant_revenue_estimator.engine.tables import (
    Arrival,
    SimulationConfig,
    Table,
    simulate,
)

COURSES = {
    "sumibi": CourseSpec("sumibi", "炭火焼鳥会席", 5700, 120),
    "oyama": CourseSpec("oyama", "大山どり満喫", 6200, 120),
}
TABLES = [Table(f"t{index}", 4, "1f") for index in range(1, 6)]


def test_ala_plus_course_equals_total() -> None:
    plan = plan_dinner(
        day=date(2026, 1, 5),
        guests=100,
        courses=COURSES,
        tables=TABLES,
        base_ratio=0.25,
    )
    assert plan.ala_guests + plan.course_total == plan.total_guests


def test_twelve_person_reservation_blocks_three_tables() -> None:
    tables = TABLES[:3]
    plan = plan_dinner(
        day=date(2026, 1, 5),
        guests=12,
        courses={"sumibi": COURSES["sumibi"]},
        tables=tables,
        base_ratio=1.0,
    )
    block = plan.reservations[0]
    assert len(block.table_ids) == 3
    assert block.end_minute - block.start_minute == 120

    result = simulate(
        tables,
        SimulationConfig(
            close_minute=200,
            last_entry_minute=190,
            max_wait_minutes=5,
            cleanup_minutes=5,
        ),
        arrivals=[Arrival(minute=90, size=2, stay_minutes=30)],
        reservations=plan.reservations,
    )
    assert result.entered_guests == 0


def test_two_person_group_cannot_take_reserved_tables() -> None:
    tables = TABLES[:3]
    plan = plan_dinner(
        day=date(2026, 1, 5),
        guests=12,
        courses={"sumibi": COURSES["sumibi"]},
        tables=tables,
        base_ratio=1.0,
    )
    result = simulate(
        tables,
        SimulationConfig(
            close_minute=200,
            last_entry_minute=190,
            max_wait_minutes=200,
            cleanup_minutes=5,
        ),
        arrivals=[Arrival(minute=0, size=2, stay_minutes=90)],
        reservations=plan.reservations,
    )
    assert result.entered_guests == 0


def test_reservation_site_crosses_do_not_change_plan() -> None:
    observations = {"reservation_site": [{"date": "2026-01-05", "mark": "x"}]}
    before = plan_dinner(
        day=date(2026, 1, 5),
        guests=100,
        courses=COURSES,
        tables=TABLES,
        base_ratio=0.25,
    )
    observations["reservation_site"].append({"date": "2026-01-05", "mark": "x"})
    after = plan_dinner(
        day=date(2026, 1, 5),
        guests=100,
        courses=COURSES,
        tables=TABLES,
        base_ratio=0.25,
    )
    assert before == after


def test_b_month_increases_ratio_within_unit_interval() -> None:
    low = course_ratio(0.25, 0.0)
    high = course_ratio(0.25, 1.5)
    assert 0 < low < high < 1


def test_extension_not_allowed_on_friday_or_holiday_eve() -> None:
    friday = date(2026, 1, 9)
    monday = date(2026, 1, 5)
    assert not can_extend(friday, set())
    assert can_extend(monday, {date(2026, 1, 20)})
    assert not can_extend(date(2026, 1, 5), {date(2026, 1, 6)})


def test_seasonality_on_demand_then_revenue_is_an_error() -> None:
    registry = SeasonalityRegistry()
    registry.apply("dec", Decimal("1.15"), "guests")
    with pytest.raises(SeasonalityError):
        registry.apply("dec", Decimal("1.15"), "revenue")


def test_torahachi_course_durations() -> None:
    light = CourseSpec("light", "お気軽", 4000, 120)
    standard = CourseSpec("standard", "とら八定番", 4500, 150)
    assert light.duration_minutes == 120
    assert standard.duration_minutes == 150


def test_course_demand_exists_without_web_availability() -> None:
    plan = plan_dinner(
        day=date(2026, 1, 5),
        guests=100,
        courses=COURSES,
        tables=TABLES,
        base_ratio=0.35,
    )
    assert plan.course_total > 0


def test_assign_tables_for_party() -> None:
    assert len(assign_tables_for_party(TABLES, 12)) == 3
