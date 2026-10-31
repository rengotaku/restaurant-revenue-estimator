"""SQLite のシナリオと実行履歴."""

from sqlmodel import Session, select

from restaurant_revenue_estimator.models import Run, Scenario


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
    ) -> Run | None:
        statement = select(Run).where(
            Run.store_id == store_id,
            Run.input_hash == input_hash,
            Run.seed == seed,
            Run.replications == replications,
            Run.assumption_set == assumption_set,
            Run.calendar_mode == calendar_mode,
            Run.year == year,
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
