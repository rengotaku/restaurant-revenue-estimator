"""夜の需要・コース比率・団体予約."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from restaurant_revenue_estimator.engine.tables import (
    Arrival,
    Floor,
    ReservationBlock,
    SimulationConfig,
    Table,
    simulate,
)

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class CourseSpec:
    id: str
    name: str
    price: int
    duration_minutes: int
    min_guests: int = 2
    max_guests: int = 54
    includes_drink: bool = True


@dataclass(frozen=True)
class DinnerPlan:
    day: date
    total_guests: int
    ala_guests: int
    course_guests: dict[str, int]
    reservations: tuple[ReservationBlock, ...]

    @property
    def course_total(self) -> int:
        return sum(self.course_guests.values())


def demand_for_day(
    base_guests: float, seasonality: float, day_effect: float = 1.0
) -> int:
    """曜日ベース × 月の季節係数 × 特定日の効果."""
    return max(0, round(base_guests * seasonality * day_effect))


def course_ratio(base_ratio: float, b_month: float = 0.0) -> float:
    """ロジスティック. b_month を上げると比率が上がる（0〜1 のまま）."""
    if not 0 <= base_ratio <= 1:
        raise ValueError("base_ratio は 0〜1")
    if base_ratio in (0, 1):
        return float(base_ratio)
    logit = math.log(base_ratio / (1 - base_ratio)) + b_month
    return 1 / (1 + math.exp(-logit))


def month_factor(seasonality: list[float], month: int) -> float:
    return seasonality[month - 1]


def holiday_eve(day: date, holidays: set[date]) -> bool:
    """祝前日か. 金曜も延長不可（仕様 T16 の運用）."""
    return (day + timedelta(days=1)) in holidays


def can_extend(day: date, holidays: set[date]) -> bool:
    """2.5 時間延長を付けられるか. 金曜・祝前日は不可."""
    return day.weekday() != 4 and not holiday_eve(day, holidays)


def split_courses(course_guests: int, courses: dict[str, CourseSpec]) -> dict[str, int]:
    """コース客を均等に分ける. 端数は先頭のコースへ."""
    if not courses or course_guests <= 0:
        return {}
    share, remainder = divmod(course_guests, len(courses))
    result: dict[str, int] = {}
    for index, course_id in enumerate(courses):
        result[course_id] = share + (remainder if index == 0 else 0)
    return result


def assign_tables_for_party(tables: list[Table], guests: int) -> tuple[str, ...]:
    """人数を収めるのに必要な卓を大きい順に選ぶ."""
    ordered = sorted(tables, key=lambda table: table.seats, reverse=True)
    chosen: list[str] = []
    remaining = guests
    for table in ordered:
        if remaining <= 0:
            break
        chosen.append(table.id)
        remaining -= table.seats
    if remaining > 0:
        raise ValueError("人数を収められる卓の組合せが無い")
    return tuple(chosen)


def plan_dinner(
    *,
    day: date,
    guests: int,
    courses: dict[str, CourseSpec],
    tables: list[Table],
    base_ratio: float,
    b_month: float = 0.0,
    start_minute: int = 90,
    extend: bool = False,
    can_extend_today: bool = True,
) -> DinnerPlan:
    """夜の客数をアラカルトとコースに分け、団体予約のブロックを作る."""
    ratio = course_ratio(base_ratio, b_month)
    course_guests = round(guests * ratio)
    ala_guests = max(0, guests - course_guests)
    split = split_courses(course_guests, courses)

    blocks: list[ReservationBlock] = []
    for course_id, count in split.items():
        if count <= 0:
            continue
        course = courses[course_id]
        duration = course.duration_minutes
        if extend and can_extend_today:
            duration += 30
        table_ids = assign_tables_for_party(tables, count)
        blocks.append(
            ReservationBlock(
                start_minute=start_minute,
                end_minute=start_minute + duration,
                size=count,
                table_ids=table_ids,
            )
        )
    return DinnerPlan(
        day=day,
        total_guests=guests,
        ala_guests=ala_guests,
        course_guests=split,
        reservations=tuple(blocks),
    )


@dataclass(frozen=True)
class CourseParty:
    size: int
    course_id: str
    start_minute: int
    duration_minutes: int


@dataclass(frozen=True)
class DinnerDayReplicationResult:
    walk_in_desired: int
    walk_in_entered: int
    walk_in_lost: int
    walk_in_lost_by_reason: dict[str, int]
    course_desired: int
    course_entered: int
    course_lost: int
    course_entered_by_course: dict[str, int]
    course_lost_by_reason: dict[str, int]
    entered_guests: int
    lost_guests: int
    lost_by_reason: dict[str, int]
    reservations: tuple[ReservationBlock, ...]
    unallocated_parties: tuple[CourseParty, ...]


@dataclass(frozen=True)
class DinnerDayResult:
    replications: tuple[DinnerDayReplicationResult, ...]
    entered_guests: float
    lost_guests: float
    walk_in_desired: float
    walk_in_entered: float
    walk_in_lost: float
    course_desired: float
    course_entered: float
    course_lost: float
    course_entered_by_course: dict[str, float]
    lost_by_reason: dict[str, float]
    reservations: tuple[ReservationBlock, ...]

    @property
    def walk_in_lost_by_reason(self) -> dict[str, float]:
        reasons: set[str] = set()
        for rep in self.replications:
            reasons.update(rep.walk_in_lost_by_reason.keys())
        n = len(self.replications) or 1
        return {
            r: sum(rep.walk_in_lost_by_reason.get(r, 0) for rep in self.replications)
            / n
            for r in reasons
        }

    @property
    def course_lost_by_reason(self) -> dict[str, float]:
        reasons: set[str] = set()
        for rep in self.replications:
            reasons.update(rep.course_lost_by_reason.keys())
        n = len(self.replications) or 1
        return {
            r: sum(rep.course_lost_by_reason.get(r, 0) for rep in self.replications)
            / n
            for r in reasons
        }


def _validate_size_distribution(dist: dict[int, float]) -> None:
    """人数分布の検証."""
    if not dist:
        raise ValueError("人数分布が空")
    for k, v in dist.items():
        if not isinstance(k, int) or k <= 0:
            raise ValueError("人数は正の整数")
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError("確率は有限の非負")
    prob_sum = sum(dist.values())
    if not math.isfinite(prob_sum) or prob_sum <= 0:
        raise ValueError("確率の和が 0")


def build_course_parties(
    course_guests: int,
    size_distribution: dict[int, float],
    rng: np.random.Generator,
) -> list[int]:
    """コースの組を作る (手順 4)."""
    _validate_size_distribution(size_distribution)
    if course_guests <= 0:
        return []
    sizes = list(size_distribution.keys())
    probs = np.array(list(size_distribution.values()), dtype=float)
    probs /= probs.sum()

    parties: list[int] = []
    remaining = course_guests
    while remaining > 0:
        size = int(rng.choice(sizes, p=probs))
        if remaining <= size:
            if remaining < 3 and len(parties) > 0:
                parties[-1] += remaining
            else:
                parties.append(remaining)
            break
        parties.append(size)
        remaining -= size
        if remaining < 3:
            if len(parties) > 0:
                parties[-1] += remaining
                break
            else:
                parties.append(remaining)
                break
    return parties


def _allocate_reservations_internal(
    parties: Sequence[CourseParty],
    tables: Sequence[Table],
    cleanup_minutes: int,
    floors: Sequence[Floor] = (),
) -> tuple[list[ReservationBlock], list[CourseParty], list[CourseParty]]:
    never_floors = {f.id for f in floors if f.policy == "never"}
    sorted_parties = sorted(enumerate(parties), key=lambda x: (x[1].start_minute, x[0]))
    table_reserved: dict[str, list[tuple[int, int]]] = {
        table.id: [] for table in tables
    }
    blocks: list[ReservationBlock] = []
    allocated_parties: list[CourseParty] = []
    unallocated: list[CourseParty] = []

    for _, party in sorted_parties:
        p_start = party.start_minute
        p_end = party.start_minute + party.duration_minutes + cleanup_minutes

        available = [
            table
            for table in tables
            if table.floor not in never_floors
            and not any(
                p_start < r_end and r_start < p_end
                for r_start, r_end in table_reserved[table.id]
            )
        ]
        available_sorted = sorted(available, key=lambda t: (-t.seats, t.id))

        chosen_tables: list[Table] = []
        seats_accum = 0
        for table in available_sorted:
            if seats_accum >= party.size:
                break
            chosen_tables.append(table)
            seats_accum += table.seats

        if seats_accum >= party.size:
            for table in chosen_tables:
                table_reserved[table.id].append((p_start, p_end))
            blocks.append(
                ReservationBlock(
                    start_minute=party.start_minute,
                    end_minute=party.start_minute + party.duration_minutes,
                    size=party.size,
                    table_ids=tuple(t.id for t in chosen_tables),
                )
            )
            allocated_parties.append(party)
        else:
            unallocated.append(party)

    return blocks, allocated_parties, unallocated


def allocate_reservations(
    parties: Sequence[CourseParty],
    tables: Sequence[Table],
    cleanup_minutes: int,
    floors: Sequence[Floor] = (),
) -> tuple[list[ReservationBlock], list[CourseParty]]:
    """団体予約の卓を確保する (手順 7)."""
    blocks, _, unallocated = _allocate_reservations_internal(
        parties, tables, cleanup_minutes, floors
    )
    return blocks, unallocated


def _build_walk_in_parties(
    walk_in_guests: int,
    size_distribution: dict[int, float],
    rng: np.random.Generator,
) -> list[int]:
    """ウォークインの組の人数を作る."""
    _validate_size_distribution(size_distribution)
    if walk_in_guests <= 0:
        return []
    sizes = list(size_distribution.keys())
    probs = np.array(list(size_distribution.values()), dtype=float)
    probs /= probs.sum()

    parties: list[int] = []
    remaining = walk_in_guests
    while remaining > 0:
        size = int(rng.choice(sizes, p=probs))
        if remaining <= size:
            parties.append(remaining)
            break
        parties.append(size)
        remaining -= size
    return parties


def _schedule_walk_in_arrivals(
    *,
    parties: Sequence[int],
    open_minute: int,
    last_entry_minute: int,
    open_total_min: int,
    dinner_peak_share: float,
    dinner_stay_minutes: int,
    rng: np.random.Generator,
) -> list[Arrival]:
    """ウォークインの到着スケジュールを作成する (手順 8)."""
    if not parties or last_entry_minute <= open_minute:
        return []

    num_slots = max(1, (last_entry_minute - open_minute) // 5)
    # 18:30〜19:30 は営業開始 (open_clock) からの相対分
    peak_start = (18 * 60 + 30) - open_total_min
    peak_end = (19 * 60 + 30) - open_total_min

    peak_slots: list[int] = []
    off_slots: list[int] = []
    for slot_idx in range(num_slots):
        slot_min = open_minute + slot_idx * 5
        if peak_start <= slot_min < peak_end:
            peak_slots.append(slot_idx)
        else:
            off_slots.append(slot_idx)

    weights = np.zeros(num_slots, dtype=float)
    if peak_slots and off_slots:
        weights[peak_slots] = dinner_peak_share / len(peak_slots)
        weights[off_slots] = (1.0 - dinner_peak_share) / len(off_slots)
    else:
        weights[:] = 1.0 / num_slots
    probs = weights / weights.sum()

    arrivals: list[Arrival] = []
    for size in parties:
        slot_idx = int(rng.choice(num_slots, p=probs))
        offset = int(rng.integers(0, 5))
        minute = open_minute + slot_idx * 5 + offset
        if minute >= last_entry_minute:
            minute = max(open_minute, last_entry_minute - 1)
        arrivals.append(
            Arrival(
                minute=minute,
                size=size,
                stay_minutes=dinner_stay_minutes,
            )
        )
    arrivals.sort(key=lambda a: a.minute)
    return arrivals


def _parse_clock_to_minute(clock_str: str, open_total: int) -> int:
    h, m = map(int, clock_str.split(":"))
    return (h * 60 + m) - open_total


def _simulate_one_replication(
    *,
    tables: Sequence[Table],
    floors: Sequence[Floor],
    courses: dict[str, CourseSpec],
    c_desired: int,
    w_desired: int,
    course_party_dist: dict[int, float],
    walk_in_group_dist: dict[int, float],
    time_options: list[int],
    can_extend_today: bool,
    course_extension_rate: float,
    cleanup_minutes: int,
    walk_in_arrivals: Sequence[Arrival] | None,
    open_minute: int,
    close_minute: int,
    last_entry_minute: int,
    max_wait_minutes: int,
    open_total_min: int,
    dinner_peak_share: float,
    dinner_stay_minutes: int,
    rng: np.random.Generator,
) -> DinnerDayReplicationResult:
    # 4. コースの組
    party_sizes = build_course_parties(c_desired, course_party_dist, rng)

    # 5. 各組のコース & 6. 開始時刻・占有時間
    course_lost_by_reason: dict[str, int] = {}
    valid_parties: list[CourseParty] = []

    for size in party_sizes:
        available_courses = [
            c for c in courses.values() if c.min_guests <= size <= c.max_guests
        ]
        if not available_courses:
            course_lost_by_reason["course_size"] = (
                course_lost_by_reason.get("course_size", 0) + size
            )
            continue

        sorted_courses = sorted(available_courses, key=lambda c: c.id)
        chosen_course = sorted_courses[int(rng.choice(len(sorted_courses)))]
        start_minute = int(rng.choice(time_options))
        duration = chosen_course.duration_minutes
        if can_extend_today and rng.random() < course_extension_rate:
            duration += 30

        valid_parties.append(
            CourseParty(
                size=size,
                course_id=chosen_course.id,
                start_minute=start_minute,
                duration_minutes=duration,
            )
        )

    # 7. 卓の確保
    allocated_blocks, allocated_parties, unallocated = (
        _allocate_reservations_internal(
            valid_parties, tables, cleanup_minutes, floors
        )
    )
    for party in unallocated:
        course_lost_by_reason["no_table_for_reservation"] = (
            course_lost_by_reason.get("no_table_for_reservation", 0) + party.size
        )

    course_entered_by_course: dict[str, int] = dict.fromkeys(courses, 0)
    course_entered = 0
    for party in allocated_parties:
        course_entered += party.size
        course_entered_by_course[party.course_id] = (
            course_entered_by_course.get(party.course_id, 0) + party.size
        )
    course_lost = sum(course_lost_by_reason.values())

    # 8. ウォークイン
    if walk_in_arrivals is not None:
        rep_walk_in_arrivals = list(walk_in_arrivals)
    else:
        walk_in_parties = _build_walk_in_parties(w_desired, walk_in_group_dist, rng)
        rep_walk_in_arrivals = _schedule_walk_in_arrivals(
            parties=walk_in_parties,
            open_minute=open_minute,
            last_entry_minute=last_entry_minute,
            open_total_min=open_total_min,
            dinner_peak_share=dinner_peak_share,
            dinner_stay_minutes=dinner_stay_minutes,
            rng=rng,
        )

    sim_config = SimulationConfig(
        open_minute=open_minute,
        close_minute=close_minute,
        last_entry_minute=last_entry_minute,
        max_wait_minutes=max_wait_minutes,
        cleanup_minutes=cleanup_minutes,
        floors=tuple(floors),
    )

    sim_result = simulate(
        tables=tables,
        config=sim_config,
        arrivals=rep_walk_in_arrivals,
        reservations=allocated_blocks,
    )
    walk_in_entered = sim_result.entered_guests
    walk_in_lost = sim_result.lost_guests
    walk_in_lost_by_reason = sim_result.lost_by_reason

    total_entered = walk_in_entered + course_entered
    total_lost = walk_in_lost + course_lost
    total_lost_by_reason = dict(walk_in_lost_by_reason)
    for reason, count in course_lost_by_reason.items():
        total_lost_by_reason[reason] = total_lost_by_reason.get(reason, 0) + count

    return DinnerDayReplicationResult(
        walk_in_desired=w_desired,
        walk_in_entered=walk_in_entered,
        walk_in_lost=walk_in_lost,
        walk_in_lost_by_reason=walk_in_lost_by_reason,
        course_desired=c_desired,
        course_entered=course_entered,
        course_lost=course_lost,
        course_entered_by_course=course_entered_by_course,
        course_lost_by_reason=course_lost_by_reason,
        entered_guests=total_entered,
        lost_guests=total_lost,
        lost_by_reason=total_lost_by_reason,
        reservations=tuple(allocated_blocks),
        unallocated_parties=tuple(unallocated),
    )


def simulate_dinner_day(
    *,
    tables: Sequence[Table],
    floors: Sequence[Floor] = (),
    desired_guests: int,
    course_ratio: float,
    courses: dict[str, CourseSpec],
    dinner_stay_minutes: int = 120,
    dinner_peak_share: float = 0.5,
    dinner_group_size_distribution: dict[int, float] | None = None,
    course_party_size_distribution: dict[int, float] | None = None,
    course_start_window: dict[str, str] | tuple[int, int] | None = None,
    course_extension_rate: float = 0.0,
    can_extend_today: bool = True,
    cleanup_minutes: int = 5,
    max_wait_minutes: int = 10,
    open_minute: int = 0,
    last_entry_minute: int = 240,
    close_minute: int = 300,
    open_clock: str = "17:00",
    replications: int = 1,
    seed: int | None = None,
    walk_in_arrivals: Sequence[Arrival] | None = None,
) -> DinnerDayResult:
    """1 日の夜の卓・予約シミュレーション (手順 1〜9)."""
    if desired_guests < 0:
        raise ValueError("desired_guests は 0 以上")
    if replications < 1:
        raise ValueError("replications は 1 以上")
    if not (0.0 <= course_ratio <= 1.0) or not math.isfinite(course_ratio):
        raise ValueError("course_ratio は 0〜1")

    walk_in_group_dist = dinner_group_size_distribution or {
        1: 0.15,
        2: 0.45,
        3: 0.2,
        4: 0.2,
    }
    course_party_dist = course_party_size_distribution or {
        4: 0.5,
        6: 0.3,
        10: 0.2,
    }

    open_h, open_m = map(int, open_clock.split(":"))
    open_total_min = open_h * 60 + open_m

    if isinstance(course_start_window, dict):
        start_min = _parse_clock_to_minute(
            course_start_window["start"], open_total_min
        )
        end_min = _parse_clock_to_minute(course_start_window["end"], open_total_min)
    elif isinstance(course_start_window, (tuple, list)):
        start_min, end_min = course_start_window
    else:
        start_min = (18 * 60) - open_total_min
        end_min = (19 * 60 + 30) - open_total_min

    d_desired = desired_guests
    r_ratio = course_ratio
    c_desired = round(d_desired * r_ratio)
    w_desired = d_desired - c_desired

    time_options = list(range(start_min, end_min + 1, 5)) or [start_min]
    master_rng = np.random.default_rng(seed)
    rep_results: list[DinnerDayReplicationResult] = []

    for _ in range(replications):
        rep_seed = int(master_rng.integers(0, 2**31 - 1)) if seed is not None else None
        rng = np.random.default_rng(rep_seed)

        rep_result = _simulate_one_replication(
            tables=tables,
            floors=floors,
            courses=courses,
            c_desired=c_desired,
            w_desired=w_desired,
            course_party_dist=course_party_dist,
            walk_in_group_dist=walk_in_group_dist,
            time_options=time_options,
            can_extend_today=can_extend_today,
            course_extension_rate=course_extension_rate,
            cleanup_minutes=cleanup_minutes,
            walk_in_arrivals=walk_in_arrivals,
            open_minute=open_minute,
            close_minute=close_minute,
            last_entry_minute=last_entry_minute,
            max_wait_minutes=max_wait_minutes,
            open_total_min=open_total_min,
            dinner_peak_share=dinner_peak_share,
            dinner_stay_minutes=dinner_stay_minutes,
            rng=rng,
        )
        rep_results.append(rep_result)

    n_reps = len(rep_results) or 1
    avg_entered = sum(r.entered_guests for r in rep_results) / n_reps
    avg_lost = sum(r.lost_guests for r in rep_results) / n_reps
    avg_w_desired = sum(r.walk_in_desired for r in rep_results) / n_reps
    avg_w_entered = sum(r.walk_in_entered for r in rep_results) / n_reps
    avg_w_lost = sum(r.walk_in_lost for r in rep_results) / n_reps
    avg_c_desired = sum(r.course_desired for r in rep_results) / n_reps
    avg_c_entered = sum(r.course_entered for r in rep_results) / n_reps
    avg_c_lost = sum(r.course_lost for r in rep_results) / n_reps

    all_course_ids = set(courses.keys())
    avg_course_entered = {
        c_id: sum(r.course_entered_by_course.get(c_id, 0) for r in rep_results) / n_reps
        for c_id in all_course_ids
    }

    all_reasons: set[str] = set()
    for r in rep_results:
        all_reasons.update(r.lost_by_reason.keys())
    avg_lost_by_reason = {
        reason: sum(r.lost_by_reason.get(reason, 0) for r in rep_results) / n_reps
        for reason in all_reasons
    }
    first_reservations = rep_results[0].reservations if rep_results else ()

    return DinnerDayResult(
        replications=tuple(rep_results),
        entered_guests=avg_entered,
        lost_guests=avg_lost,
        walk_in_desired=avg_w_desired,
        walk_in_entered=avg_w_entered,
        walk_in_lost=avg_w_lost,
        course_desired=avg_c_desired,
        course_entered=avg_c_entered,
        course_lost=avg_c_lost,
        course_entered_by_course=avg_course_entered,
        lost_by_reason=avg_lost_by_reason,
        reservations=first_reservations,
    )
