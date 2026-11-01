"""1 分刻みの卓シミュレーション (TC-07-xx)."""

import pytest

from restaurant_revenue_estimator.engine.tables import (
    Arrival,
    Floor,
    SimulationConfig,
    Table,
    expected_full_occupancy,
    simulate,
)

TABLES = [
    Table("1f-1", 4, "1f"),
    Table("1f-2", 4, "1f"),
    Table("1f-3", 4, "1f"),
    Table("1f-4", 4, "1f"),
    Table("1f-5", 4, "1f"),
    Table("1f-6", 2, "1f"),
]


def test_six_tables_twenty_two_seats() -> None:
    assert len(TABLES) == 6
    assert sum(table.seats for table in TABLES) == 22


def test_only_two_person_groups_use_twelve_seats() -> None:
    arrivals = [Arrival(minute=minute, size=2, stay_minutes=60) for minute in range(60)]
    result = simulate(
        TABLES,
        SimulationConfig(
            close_minute=80,
            last_entry_minute=60,
            max_wait_minutes=1000,
            cleanup_minutes=0,
        ),
        arrivals=arrivals,
    )
    assert max(result.occupied_people) == 12


def test_expected_full_occupancy() -> None:
    assert expected_full_occupancy(TABLES, {4: 2.4, 2: 1.7}) == pytest.approx(13.7)


def test_same_seed_is_identical_and_different_seed_differs() -> None:
    rate = [1.0] * 18
    first = simulate(
        TABLES,
        SimulationConfig(close_minute=150, last_entry_minute=120),
        arrival_rate=rate,
        seed=42,
    )
    second = simulate(
        TABLES,
        SimulationConfig(close_minute=150, last_entry_minute=120),
        arrival_rate=rate,
        seed=42,
    )
    other = simulate(
        TABLES,
        SimulationConfig(close_minute=150, last_entry_minute=120),
        arrival_rate=rate,
        seed=43,
    )
    assert first == second
    assert first.queue_people != other.queue_people


def test_no_arrivals() -> None:
    result = simulate(
        TABLES, SimulationConfig(close_minute=120, last_entry_minute=100), arrivals=[]
    )
    assert result.entered_guests == 0
    assert result.max_queue_people() == 0
    assert result.lost_guests == 0


def test_over_capacity_creates_queue_and_lost_demand() -> None:
    arrivals = [Arrival(minute=0, size=4, stay_minutes=60) for _ in range(40)]
    result = simulate(
        TABLES,
        SimulationConfig(
            close_minute=120,
            last_entry_minute=100,
            max_wait_minutes=200,
            cleanup_minutes=5,
        ),
        arrivals=arrivals,
    )
    assert result.max_queue_people() > 0
    assert result.lost_guests > 0


def test_more_arrivals_never_reduce_entered_or_lost() -> None:
    results = []
    for per_minute in (1, 2, 3):
        arrivals = [
            Arrival(minute=minute, size=2, stay_minutes=40)
            for minute in range(0, 120, 5)
            for _ in range(per_minute)
        ]
        results.append(
            simulate(
                TABLES,
                SimulationConfig(
                    close_minute=150,
                    last_entry_minute=120,
                    max_wait_minutes=200,
                    cleanup_minutes=5,
                ),
                arrivals=arrivals,
            )
        )
    assert (
        results[0].entered_guests
        <= results[1].entered_guests
        <= results[2].entered_guests
    )
    assert results[0].lost_guests <= results[1].lost_guests <= results[2].lost_guests


def test_zero_kitchen_capacity_seats_nobody() -> None:
    arrivals = [Arrival(minute=0, size=2, stay_minutes=30) for _ in range(3)]
    result = simulate(
        TABLES,
        SimulationConfig(
            close_minute=60, last_entry_minute=50, kitchen_capacity_per_5min=0
        ),
        arrivals=arrivals,
    )
    assert result.entered_guests == 0


def test_second_floor_only_when_lower_is_full() -> None:
    tables = [Table("1f-1", 2, "1f"), Table("1f-2", 2, "1f"), Table("2f-1", 2, "2f")]
    arrivals = [Arrival(minute=0, size=2, stay_minutes=30) for _ in range(2)]
    result = simulate(
        tables,
        SimulationConfig(
            close_minute=60,
            last_entry_minute=50,
            cleanup_minutes=0,
            floors=(Floor("1f", "always"), Floor("2f", "when_lower_full")),
        ),
        arrivals=arrivals,
    )
    assert result.entered_by_floor["2f"] == 0


def test_group_larger_than_biggest_table_is_no_table() -> None:
    result = simulate(
        TABLES,
        SimulationConfig(close_minute=60, last_entry_minute=50),
        arrivals=[Arrival(minute=0, size=6, stay_minutes=30)],
    )
    assert result.lost_by_reason.get("no_table") == 6


def test_second_group_is_seated_after_cleanup() -> None:
    tables = [Table("t1", 2, "1f")]
    result = simulate(
        tables,
        SimulationConfig(
            close_minute=120,
            last_entry_minute=110,
            max_wait_minutes=40,
            cleanup_minutes=5,
        ),
        arrivals=[
            Arrival(minute=0, size=2, stay_minutes=30),
            Arrival(minute=10, size=2, stay_minutes=30),
        ],
    )
    assert result.seating_events == ((0, 2, "1f"), (35, 2, "1f"))


def test_last_entry_turns_queue_into_lost_demand() -> None:
    tables = [Table("t1", 2, "1f")]
    arrivals = [Arrival(minute=0, size=2, stay_minutes=100)]
    arrivals += [Arrival(minute=59, size=2, stay_minutes=30) for _ in range(3)]
    result = simulate(
        tables,
        SimulationConfig(
            close_minute=130,
            last_entry_minute=60,
            max_wait_minutes=200,
            cleanup_minutes=0,
        ),
        arrivals=arrivals,
    )
    assert result.lost_groups_by_reason.get("last_entry") == 3


def test_wait_limit_makes_group_leave() -> None:
    tables = [Table("t1", 2, "1f")]
    result = simulate(
        tables,
        SimulationConfig(
            close_minute=120,
            last_entry_minute=110,
            max_wait_minutes=10,
            cleanup_minutes=0,
        ),
        arrivals=[
            Arrival(minute=0, size=2, stay_minutes=100),
            Arrival(minute=0, size=2, stay_minutes=30),
        ],
    )
    assert result.lost_by_reason.get("wait_timeout") == 2
    assert (11, 2, "1f") not in result.seating_events


def test_tc_42_10_record_series_controls_series_recording() -> None:
    rate = [1.0] * 18
    res_true = simulate(
        TABLES,
        SimulationConfig(close_minute=150, last_entry_minute=120),
        arrival_rate=rate,
        seed=42,
        record_series=True,
    )
    res_false = simulate(
        TABLES,
        SimulationConfig(close_minute=150, last_entry_minute=120),
        arrival_rate=rate,
        seed=42,
        record_series=False,
    )

    # 系列以外の項目がすべて一致
    assert res_false.entered_guests == res_true.entered_guests
    assert res_false.entered_groups == res_true.entered_groups
    assert res_false.lost_guests == res_true.lost_guests
    assert res_false.lost_groups == res_true.lost_groups
    assert res_false.lost_by_reason == res_true.lost_by_reason
    assert res_false.lost_groups_by_reason == res_true.lost_groups_by_reason
    assert res_false.entered_by_floor == res_true.entered_by_floor
    assert res_false.seating_events == res_true.seating_events
    assert res_false.seat_minutes == res_true.seat_minutes
    assert res_false.seat_capacity_minutes == res_true.seat_capacity_minutes
    assert res_false.seat_utilization == res_true.seat_utilization

    # False のときは 4 つの系列が空であること
    assert res_false.queue_people == ()
    assert res_false.queue_groups == ()
    assert res_false.occupied_people == ()
    assert all(series == () for series in res_false.occupied_tables_by_floor.values())

    # True のときは系列が記録されていること
    assert len(res_true.queue_people) > 0
    assert len(res_true.queue_groups) > 0
    assert len(res_true.occupied_people) > 0
    assert any(len(series) > 0 for series in res_true.occupied_tables_by_floor.values())


def test_tc_42_15_tables_on_unconfigured_floors_do_not_raise() -> None:
    tables = [Table("1f-1", 4, "1f"), Table("2f-1", 4, "2f")]
    config = SimulationConfig(
        floors=(Floor("1f", "always"),),
        close_minute=60,
        last_entry_minute=50,
    )
    result = simulate(tables, config)
    assert result.entered_guests == 0


