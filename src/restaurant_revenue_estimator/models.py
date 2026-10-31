"""SQLModel エンティティ: 画面で作った試算だけを DB に置く (ADR 0002)."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Scenario(SQLModel, table=True):
    """画面で変えた仮定のまとまり. 事実は YAML のまま."""

    __tablename__ = "scenarios"

    id: int | None = Field(default=None, primary_key=True)
    store_id: str = Field(index=True, max_length=100)
    name: str = Field(max_length=200)
    base_assumption_set: str = Field(default="standard", max_length=20)
    overrides_json: str = Field(default="{}")
    note: str = Field(default="", max_length=1000)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Run(SQLModel, table=True):
    """1 回の評価の記録. 同じ入力・seed・replications なら再利用する."""

    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    store_id: str = Field(index=True, max_length=100)
    scenario_id: int | None = Field(default=None)
    assumption_set: str = Field(default="standard", max_length=20)
    calendar_mode: str = Field(default="steady", max_length=20)
    year: int = Field(default=2026)
    seed: int = Field(default=42)
    replications: int = Field(default=20)
    input_hash: str = Field(index=True, max_length=64)
    model_version: str = Field(default="", max_length=20)
    calendar_sha256: str = Field(default="", max_length=64)
    result_json: str = Field(default="{}")
    report_md: str = Field(default="")
    created_at: datetime = Field(default_factory=_utcnow)
