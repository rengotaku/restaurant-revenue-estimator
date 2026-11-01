"""昼のモデル: 到着率を作り、卓シミュレーションを replications 回す."""

from collections.abc import Sequence
from dataclasses import dataclass

from restaurant_revenue_estimator.engine.tables import (
    Floor,
    SimulationConfig,
    SimulationResult,
    Table,
    simulate,
)


@dataclass(frozen=True)
class LunchDayResult:
    guests: float
    lost: float
    peak_queue: float
    results: tuple[SimulationResult, ...]
    occupied_people: tuple[float, ...] = ()


def lunch_occupancy_stats(
    occupied_people: Sequence[float],
) -> tuple[float, float]:
    """Return (max_occupancy, avg_12_13).

    Assuming lunch starts at 11:30 (minute 0).
    12:00 is minute 30, 13:00 is minute 90.
    """
    if not occupied_people:
        return 0.0, 0.0
    max_val = max(occupied_people)
    start_idx = 30
    end_idx = min(91, len(occupied_people))
    slice_12_13 = occupied_people[start_idx:end_idx]
    avg_12_13 = sum(slice_12_13) / len(slice_12_13) if slice_12_13 else 0.0
    return max_val, avg_12_13


def arrival_profile(
    total_groups: float,
    *,
    windows: int = 18,
    peak_start: int = 6,
    peak_end: int = 12,
    peak_share: float = 0.4,
) -> list[float]:
    """11:30-13:00 の 5 分ごとの到着グループ数 (ポアソンの平均)."""
    weights = [0.0] * windows
    peak_slots = peak_end - peak_start
    rest_slots = windows - peak_slots
    for index in range(windows):
        if peak_start <= index < peak_end:
            weights[index] = peak_share / peak_slots
        elif rest_slots > 0:
            weights[index] = (1 - peak_share) / rest_slots
    return [total_groups * weight for weight in weights]


def simulate_lunch_day(
    *,
    tables: list[Table],
    floors: list[Floor],
    total_groups: float,
    group_sizes: list[int],
    group_probs: list[float],
    stay_minutes: int,
    cleanup_minutes: int,
    max_wait_minutes: int,
    last_entry_minute: int = 120,
    close_minute: int = 150,
    replications: int = 20,
    seed: int = 42,
) -> LunchDayResult:
    """1 日分を replications 回まわして平均を出す."""
    rates = arrival_profile(total_groups)
    results = [
        simulate(
            tables,
            SimulationConfig(
                close_minute=close_minute,
                last_entry_minute=last_entry_minute,
                max_wait_minutes=max_wait_minutes,
                cleanup_minutes=cleanup_minutes,
                floors=tuple(floors),
            ),
            arrival_rate=rates,
            group_sizes=group_sizes,
            group_probs=group_probs,
            stay_minutes=stay_minutes,
            stay_spread=max(1, stay_minutes // 8),
            seed=seed + index,
        )
        for index in range(replications)
    ]
    count = len(results) or 1
    series_len = len(results[0].occupied_people) if results else 0
    occupied_avg = tuple(
        sum(r.occupied_people[m] for r in results) / count
        for m in range(series_len)
    )
    return LunchDayResult(
        guests=sum(result.entered_guests for result in results) / count,
        lost=sum(result.lost_guests for result in results) / count,
        peak_queue=sum(result.max_queue_people() for result in results) / count,
        results=tuple(results),
        occupied_people=occupied_avg,
    )
