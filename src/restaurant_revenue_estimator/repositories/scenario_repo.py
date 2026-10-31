"""SQLite のシナリオと実行履歴."""

import json

from sqlmodel import Session, select

from restaurant_revenue_estimator import code_version
from restaurant_revenue_estimator.models import Run, Scenario


def is_default_run(run: Run) -> bool:
    """実行が標準の評価（上書きなし・standard・steady・2026）か判定する."""
    if (
        run.assumption_set != "standard"
        or run.scenario_id is not None
        or run.calendar_mode != "steady"
        or run.year != 2026
    ):
        return False
    try:
        payload = json.loads(run.result_json)
        return not bool(payload.get("meta", {}).get("overrides"))
    except Exception:
        return False


class ScenarioRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, store_id: str | None = None) -> list[Scenario]:
        statement = select(Scenario)
        if store_id:
            statement = statement.where(Scenario.store_id == store_id)
        statement = statement.order_by(Scenario.created_at.desc())  # type: ignore[attr-defined]
        return list(self.session.exec(statement).all())

    def get(self, scenario_id: int) -> Scenario | None:
        return self.session.get(Scenario, scenario_id)

    def add(self, scenario: Scenario) -> Scenario:
        self.session.add(scenario)
        self.session.commit()
        self.session.refresh(scenario)
        return scenario

    def delete(self, scenario: Scenario) -> None:
        self.session.delete(scenario)
        self.session.commit()


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find(
        self,
        *,
        store_id: str,
        input_hash: str,
        seed: int,
        replications: int,
        assumption_set: str,
        calendar_mode: str,
        year: int,
        scenario_id: int | None = None,
        code_sha256: str | None = None,
    ) -> Run | None:
        target_code_sha256 = (
            code_sha256
            if code_sha256 is not None
            else code_version.current_code_sha256()
        )
        statement = select(Run).where(
            Run.store_id == store_id,
            Run.input_hash == input_hash,
            Run.seed == seed,
            Run.replications == replications,
            Run.assumption_set == assumption_set,
            Run.calendar_mode == calendar_mode,
            Run.year == year,
            Run.scenario_id == scenario_id,
            Run.code_sha256 == target_code_sha256,
        )
        return self.session.exec(statement).first()

    def add(self, run: Run) -> Run:
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def get(self, run_id: int) -> Run | None:
        return self.session.get(Run, run_id)

    def list_recent(self, limit: int = 20) -> list[Run]:
        statement = select(Run)
        statement = statement.order_by(Run.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
        return list(self.session.exec(statement).all())

    def latest(self, store_id: str) -> Run | None:
        statement = select(Run).where(Run.store_id == store_id)
        statement = statement.order_by(Run.created_at.desc())  # type: ignore[attr-defined]
        return self.session.exec(statement).first()

    def latest_default(
        self, store_id: str, code_sha256: str | None = None
    ) -> Run | None:
        target_code_sha256 = (
            code_sha256
            if code_sha256 is not None
            else code_version.current_code_sha256()
        )
        statement = (
            select(Run)
            .where(
                Run.store_id == store_id,
                Run.assumption_set == "standard",
                Run.scenario_id.is_(None),  # type: ignore[union-attr]
                Run.calendar_mode == "steady",
                Run.year == 2026,
                Run.code_sha256 == target_code_sha256,
            )
            .order_by(Run.created_at.desc())  # type: ignore[attr-defined]
        )
        for run in self.session.exec(statement):
            if is_default_run(run):
                return run
        return None
