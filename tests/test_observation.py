"""観察との整合 (TC-08-xx)."""

from restaurant_revenue_estimator.engine.observation import check_observation, fit_grid
from restaurant_revenue_estimator.engine.tables import (
    Arrival,
    Floor,
    SimulationConfig,
    Table,
    simulate,
)

FLOOR1 = [
    Table("1f-1", 4, "1f"),
    Table("1f-2", 4, "1f"),
    Table("1f-3", 4, "1f"),
    Table("1f-4", 4, "1f"),
    Table("1f-5", 4, "1f"),
    Table("1f-6", 2, "1f"),
]
UPSTAIRS = [Table("2f-1", 4, "2f")]


def _run(
    tables: list[Table],
    arrivals: list[Arrival],
    *,
    floors: list[Floor] | None = None,
    max_wait: int = 200,
) -> object:
    return simulate(
        tables,
        SimulationConfig(
            close_minute=150,
            last_entry_minute=140,
            max_wait_minutes=max_wait,
            cleanup_minutes=5,
            floors=tuple(
                floors or [Floor("1f", "always"), Floor("2f", "when_lower_full")]
            ),
        ),
        arrivals=arrivals,
    )


def test_all_three_conditions_met() -> None:
    arrivals = [Arrival(minute=0, size=2, stay_minutes=100) for _ in range(6)]
    arrivals += [Arrival(minute=30, size=2, stay_minutes=30) for _ in range(4)]
    result = _run([*FLOOR1, *UPSTAIRS], arrivals, max_wait=10)
    match = check_observation(result, full_tables=6)  # type: ignore[arg-type]
    assert match.ok


def test_too_few_arrivals_do_not_fill_floor() -> None:
    arrivals = [Arrival(minute=0, size=2, stay_minutes=100) for _ in range(2)]
    result = _run([*FLOOR1, *UPSTAIRS], arrivals)
    match = check_observation(result, full_tables=6)  # type: ignore[arg-type]
    assert not match.full_house
    assert "1階6卓が埋まらない" in match.failed()


def test_too_many_arrivals_break_queue_condition() -> None:
    arrivals = [Arrival(minute=0, size=2, stay_minutes=100) for _ in range(60)]
    result = _run([*FLOOR1, *UPSTAIRS], arrivals, max_wait=200)
    match = check_observation(result, full_tables=6)  # type: ignore[arg-type]
    assert not match.queue_in_range


def test_upstairs_never_used_breaks_condition() -> None:
    arrivals = [Arrival(minute=0, size=2, stay_minutes=100) for _ in range(9)]
    result = _run(
        [*FLOOR1, *UPSTAIRS],
        arrivals,
        floors=[Floor("1f", "always"), Floor("2f", "never")],
        max_wait=200,
    )
    match = check_observation(result, full_tables=6)  # type: ignore[arg-type]
    assert not match.upstairs


def test_queue_is_counted_in_people_not_groups() -> None:
    arrivals = [Arrival(minute=0, size=2, stay_minutes=100) for _ in range(6)]
    arrivals += [Arrival(minute=30, size=2, stay_minutes=100) for _ in range(3)]
    result = _run([*FLOOR1], arrivals, max_wait=200)
    assert result.queue_groups[30] == 3  # type: ignore[attr-defined]
    assert result.queue_people[30] == 6  # type: ignore[attr-defined]
    match = check_observation(result, full_tables=6)  # type: ignore[arg-type]
    assert match.queue_in_range


def test_fit_grid_returns_every_combination_without_single_best() -> None:
    rows = fit_grid(
        floor1_tables=FLOOR1,
        candidates={
            "f2_a": UPSTAIRS,
            "f2_b": [Table("2f-1", 4, "2f"), Table("2f-2", 4, "2f")],
        },
        group_levels={"low": 30.0, "base": 45.0},
        stay_levels={"low": 30, "base": 40},
        group_sizes=[1, 2, 3, 4],
        group_probs=[0.25, 0.5, 0.15, 0.1],
        cleanup_minutes=5,
        max_wait_minutes=10,
        replications=3,
    )
    assert len(rows) == 8
    assert all(not hasattr(row, "best") for row in rows)
    assert "estimate" not in rows[0].as_dict()
