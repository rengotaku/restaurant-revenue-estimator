"""夜の需要・コース比率・団体予約."""

import math
from dataclasses import dataclass
from datetime import date, timedelta

from restaurant_revenue_estimator.engine.tables import ReservationBlock, Table

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
