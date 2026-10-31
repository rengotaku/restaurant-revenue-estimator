"""1 分刻みの卓シミュレーション (ADR 0003)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

FloorPolicyName = Literal["always", "when_lower_full", "never"]


@dataclass(frozen=True)
class Table:
    id: str
    seats: int
    floor: str = "1f"


@dataclass(frozen=True)
class Floor:
    id: str
    policy: FloorPolicyName = "always"


@dataclass(frozen=True)
class Arrival:
    minute: int
    size: int
    stay_minutes: int


@dataclass(frozen=True)
class ReservationBlock:
    start_minute: int
    end_minute: int
    size: int
    table_ids: tuple[str, ...]


@dataclass(frozen=True)
class SimulationConfig:
    open_minute: int = 0
    close_minute: int = 150
    last_entry_minute: int = 120
    max_wait_minutes: int = 10
    cleanup_minutes: int = 5
    kitchen_capacity_per_5min: int | None = None
    allow_join: bool = False
    allow_share: bool = False
    floors: tuple[Floor, ...] = ()


@dataclass(frozen=True)
class SimulationResult:
    entered_guests: int
    entered_groups: int
    lost_guests: int
    lost_groups: int
    lost_by_reason: dict[str, int]
    lost_groups_by_reason: dict[str, int]
    queue_people: tuple[int, ...]
    queue_groups: tuple[int, ...]
    entered_by_floor: dict[str, int]
    occupied_people: tuple[int, ...]
    occupied_tables_by_floor: dict[str, tuple[int, ...]]
    seating_events: tuple[tuple[int, int, str], ...]
    seat_minutes: int
    seat_capacity_minutes: int

    @property
    def seat_utilization(self) -> float:
        if self.seat_capacity_minutes == 0:
            return 0.0
        return self.seat_minutes / self.seat_capacity_minutes

    def max_queue_people(self) -> int:
        return max(self.queue_people, default=0)


def expected_full_occupancy(
    tables: Sequence[Table], mean_guests_by_table_size: dict[int, float]
) -> float:
    """全卓が埋まったときの同時着席人数の期待値 (spec T03)."""
    return sum(
        mean_guests_by_table_size.get(table.seats, table.seats) for table in tables
    )


@dataclass
class _Waiting:
    arrival: int
    size: int
    stay: int


def simulate(  # noqa: PLR0912, PLR0915
    tables: Sequence[Table],
    config: SimulationConfig,
    *,
    arrivals: Sequence[Arrival] = (),
    reservations: Sequence[ReservationBlock] = (),
    seed: int | None = None,
    arrival_rate: Sequence[float] | None = None,
    group_sizes: Sequence[int] = (1, 2, 3, 4),
    group_probs: Sequence[float] = (0.25, 0.5, 0.15, 0.1),
    stay_minutes: int = 40,
    stay_spread: int = 5,
) -> SimulationResult:
    """1 日分を回す. arrivals を渡すと台本モード."""
    rng = np.random.default_rng(seed)
    scripted = list(arrivals)
    if arrival_rate is not None:
        scripted.extend(
            _sample_arrivals(
                rng, arrival_rate, group_sizes, group_probs, stay_minutes, stay_spread
            )
        )
    scripted.sort(key=lambda item: item.minute)

    floor_order = [floor.id for floor in config.floors] or sorted(
        {table.floor for table in tables}
    )
    floor_policy = {floor.id: floor.policy for floor in config.floors}
    free_at = {table.id: -1 for table in tables}
    reserved: dict[str, list[tuple[int, int]]] = {table.id: [] for table in tables}
    for block in reservations:
        for table_id in block.table_ids:
            if table_id in reserved:
                reserved[table_id].append(
                    (block.start_minute, block.end_minute + config.cleanup_minutes)
                )

    queue: list[_Waiting] = []
    lost_by_reason: dict[str, int] = {}
    lost_groups_by_reason: dict[str, int] = {}
    entered_by_floor: dict[str, int] = dict.fromkeys(floor_order, 0)
    entered_guests = entered_groups = lost_guests = lost_groups = seat_minutes = 0
    queue_people_series: list[int] = []
    queue_groups_series: list[int] = []
    occupied_series: list[int] = []
    occupied_tables_series: dict[str, list[int]] = {
        floor_id: [] for floor_id in floor_order
    }
    seating_events: list[tuple[int, int, str]] = []
    current_queue_people = 0
    current_occupied_people = 0
    current_occupied_tables: dict[str, int] = dict.fromkeys(floor_order, 0)
    departures: dict[int, list[tuple[int, str]]] = {}
    next_arrival = 0
    seatings_in_window = 0
    max_seats = max((table.seats for table in tables), default=0)

    def lose(group: _Waiting, reason: str) -> None:
        nonlocal lost_guests, lost_groups
        lost_guests += group.size
        lost_groups += 1
        lost_by_reason[reason] = lost_by_reason.get(reason, 0) + group.size
        lost_groups_by_reason[reason] = lost_groups_by_reason.get(reason, 0) + 1

    def find_table(size: int, minute: int, stay: int) -> Table | None:
        end = minute + stay + config.cleanup_minutes
        candidates: list[Table] = []
        for floor_id in floor_order:
            policy = floor_policy.get(floor_id, "always")
            if policy == "never":
                continue
            floor_tables = [
                table
                for table in tables
                if table.floor == floor_id
                and table.seats >= size
                and free_at[table.id] <= minute
                and not any(
                    start < end and minute < stop for start, stop in reserved[table.id]
                )
            ]
            if policy == "when_lower_full":
                lower_available = any(
                    table.seats >= size
                    and free_at[table.id] <= minute
                    and not any(
                        start < end and minute < stop
                        for start, stop in reserved[table.id]
                    )
                    for table in tables
                    if floor_order.index(table.floor) < floor_order.index(floor_id)
                    and floor_policy.get(table.floor, "always") != "never"
                )
                if lower_available:
                    continue
            candidates.extend(floor_tables)
            if candidates:
                break
        if not candidates:
            return None
        return min(candidates, key=lambda table: table.seats)

    for minute in range(config.open_minute, config.close_minute + 1):
        if minute % 5 == 0:
            seatings_in_window = 0

        while next_arrival < len(scripted) and scripted[next_arrival].minute <= minute:
            arrival = scripted[next_arrival]
            next_arrival += 1
            if minute >= config.last_entry_minute:
                lose(
                    _Waiting(arrival.minute, arrival.size, arrival.stay_minutes),
                    "last_entry",
                )
            elif arrival.size > max_seats and not config.allow_join:
                lose(
                    _Waiting(arrival.minute, arrival.size, arrival.stay_minutes),
                    "no_table",
                )
            else:
                queue.append(
                    _Waiting(arrival.minute, arrival.size, arrival.stay_minutes)
                )
                current_queue_people += arrival.size

        if minute >= config.last_entry_minute:
            for group in queue:
                lose(group, "last_entry")
            queue.clear()
            current_queue_people = 0

        remaining: list[_Waiting] = []
        for group in queue:
            if minute - group.arrival > config.max_wait_minutes:
                lose(group, "wait_timeout")
                current_queue_people -= group.size
            else:
                remaining.append(group)
        queue = remaining

        seated_any = True
        while seated_any and queue:
            seated_any = False
            for index, group in enumerate(queue):
                if (
                    config.kitchen_capacity_per_5min is not None
                    and seatings_in_window >= config.kitchen_capacity_per_5min
                ):
                    break
                table = find_table(group.size, minute, group.stay)
                if table is None:
                    continue
                free_at[table.id] = minute + group.stay + config.cleanup_minutes
                entered_guests += group.size
                entered_groups += 1
                entered_by_floor[table.floor] += group.size
                seat_minutes += group.size * group.stay
                seating_events.append((minute, group.size, table.floor))
                seatings_in_window += 1
                queue.pop(index)
                current_queue_people -= group.size
                current_occupied_people += group.size
                current_occupied_tables[table.floor] += 1
                leave_minute = minute + group.stay
                departures.setdefault(leave_minute, []).append(
                    (group.size, table.floor)
                )
                seated_any = True
                break

        if minute in departures:
            for size, floor_id in departures.pop(minute):
                current_occupied_people -= size
                current_occupied_tables[floor_id] -= 1

        occupied_series.append(current_occupied_people)
        for floor_id in floor_order:
            occupied_tables_series[floor_id].append(
                current_occupied_tables[floor_id]
            )
        queue_people_series.append(current_queue_people)
        queue_groups_series.append(len(queue))

    for group in queue:
        lose(group, "closing")

    open_minutes = config.close_minute - config.open_minute
    capacity = sum(table.seats for table in tables) * max(open_minutes, 0)
    return SimulationResult(
        entered_guests=entered_guests,
        entered_groups=entered_groups,
        lost_guests=lost_guests,
        lost_groups=lost_groups,
        lost_by_reason=lost_by_reason,
        lost_groups_by_reason=lost_groups_by_reason,
        queue_people=tuple(queue_people_series),
        queue_groups=tuple(queue_groups_series),
        entered_by_floor=entered_by_floor,
        occupied_people=tuple(occupied_series),
        occupied_tables_by_floor={
            floor_id: tuple(series)
            for floor_id, series in occupied_tables_series.items()
        },
        seating_events=tuple(seating_events),
        seat_minutes=seat_minutes,
        seat_capacity_minutes=capacity,
    )


def _sample_arrivals(
    rng: np.random.Generator,
    arrival_rate: Sequence[float],
    group_sizes: Sequence[int],
    group_probs: Sequence[float],
    stay_minutes: int,
    stay_spread: int,
) -> list[Arrival]:
    arrivals: list[Arrival] = []
    for window_index, rate in enumerate(arrival_rate):
        count = int(rng.poisson(rate))
        for _ in range(count):
            minute = window_index * 5 + int(rng.integers(0, 5))
            size = int(rng.choice(group_sizes, p=group_probs))
            stay = max(1, round(stay_minutes + rng.normal(0, stay_spread)))
            arrivals.append(Arrival(minute=minute, size=size, stay_minutes=stay))
    return arrivals


def arrivals_from_script(items: Iterable[tuple[int, int, int]]) -> list[Arrival]:
    return [
        Arrival(minute=minute, size=size, stay_minutes=stay)
        for minute, size, stay in items
    ]
