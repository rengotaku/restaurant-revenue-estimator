"""SQLModel エンジンとセッション管理."""

from collections.abc import Generator

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

import restaurant_revenue_estimator.models  # noqa: F401  side-effect import: SQLModel.metadata 登録
from restaurant_revenue_estimator.config import get_settings

_engine: Engine | None = None


def _make_engine(database_url: str) -> Engine:
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, echo=False)


def get_engine() -> Engine:
    """SQLModel エンジンをシングルトンで返す."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
    return _engine


def set_engine(engine: Engine) -> None:
    """テスト用: エンジンを差し替える."""
    global _engine  # noqa: PLW0603
    _engine = engine


def reset_engine() -> None:
    """テスト用: エンジンキャッシュをリセットする."""
    global _engine  # noqa: PLW0603
    _engine = None


def init_db(engine: Engine | None = None) -> None:
    """テーブルが存在しない場合のみ作成する (開発用)."""
    target_engine = engine or get_engine()
    SQLModel.metadata.create_all(target_engine)
    with target_engine.connect() as conn:
        inspector = inspect(conn)
        if "runs" in inspector.get_table_names():
            columns = [c["name"] for c in inspector.get_columns("runs")]
            if "code_sha256" not in columns:
                conn.execute(
                    text(
                        "ALTER TABLE runs ADD COLUMN code_sha256"
                        " VARCHAR(64) NOT NULL DEFAULT ''"
                    )
                )
                conn.commit()


def get_session() -> Generator[Session, None, None]:
    """依存性注入用: SQLModel セッションを返す."""
    with Session(get_engine()) as session:
        yield session
