"""観察との整合: 観察を述語にして、再現できた組合せを全部出す."""

from dataclasses import dataclass

from restaurant_revenue_estimator.engine.lunch import simulate_lunch_day
from restaurant_revenue_estimator.engine.tables import Floor, SimulationResult, Table


@dataclass(frozen=True)
class ObservationMatch:
    full_house: bool
    queue_in_range: bool
    upstairs: bool

    @property
    def ok(self) -> bool:
        return self.full_house and self.queue_in_range and self.upstairs

    def failed(self) -> list[str]:
        failures = []
        if not self.full_house:
            failures.append("1階6卓が埋まらない")
        if not self.queue_in_range:
            failures.append("行列が5〜10人にならない")
        if not self.upstairs:
            failures.append("2階への入店が無い")
        return failures


@dataclass(frozen=True)
class FitRow:
    candidate: str
    groups: float
    stay_minutes: int
    reproduction_rate: float
    failed_conditions: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate,
            "groups": self.groups,
            "stay_minutes": self.stay_minutes,
            "reproduction_rate": self.reproduction_rate,
            "failed_conditions": list(self.failed_conditions),
        }


def check_observation(
    result: SimulationResult,
    *,
    full_tables: int,
    floor1_id: str = "1f",
    floor_upstairs_id: str = "2f",
    window: tuple[int, int] = (30, 60),
    queue_min: int = 5,
    queue_max: int = 10,
) -> ObservationMatch:
    """1 回のシミュレーション結果が観察を満たすか."""
    start, end = window
    floor1 = result.occupied_tables_by_floor.get(floor1_id, ())
    full_house = any(
        floor1[index] == full_tables for index in range(start, min(end, len(floor1)))
    )
    queue_in_range = any(
        queue_min <= result.queue_people[index] <= queue_max
        for index in range(start, min(end, len(result.queue_people)))
    )
    upstairs = any(
        floor == floor_upstairs_id and start <= minute < end
        for minute, _, floor in result.seating_events
    )
    return ObservationMatch(
        full_house=full_house, queue_in_range=queue_in_range, upstairs=upstairs
    )


def fit_grid(
    *,
    floor1_tables: list[Table],
    candidates: dict[str, list[Table]],
    group_levels: dict[str, float],
    stay_levels: dict[str, int],
    group_sizes: list[int],
    group_probs: list[float],
    cleanup_minutes: int,
    max_wait_minutes: int,
    replications: int,
    seed: int = 42,
) -> list[FitRow]:
    """2 階の候補 × 到着 × 滞在 の全組合せを回す. 単一の最良値は出さない."""
    rows: list[FitRow] = []
    for candidate, upstairs_tables in candidates.items():
        tables = [*floor1_tables, *upstairs_tables]
        floors = [Floor("1f", "always"), Floor("2f", "when_lower_full")]
        for group_name, groups in group_levels.items():
            for stay_name, stay in stay_levels.items():
                lunch = simulate_lunch_day(
                    tables=tables,
                    floors=floors,
                    total_groups=groups,
                    group_sizes=group_sizes,
                    group_probs=group_probs,
                    stay_minutes=stay,
                    cleanup_minutes=cleanup_minutes,
                    max_wait_minutes=max_wait_minutes,
                    replications=replications,
                    seed=seed,
                )
                matches = [
                    check_observation(result, full_tables=len(floor1_tables))
                    for result in lunch.results
                ]
                hits = sum(1 for match in matches if match.ok)
                failed: list[str] = []
                for match in matches:
                    for failure in match.failed():
                        if failure not in failed:
                            failed.append(failure)
                rows.append(
                    FitRow(
                        candidate=f"{candidate}/{group_name}/{stay_name}",
                        groups=groups,
                        stay_minutes=stay,
                        reproduction_rate=hits / (len(matches) or 1),
                        failed_conditions=tuple(failed),
                    )
                )
    return rows
