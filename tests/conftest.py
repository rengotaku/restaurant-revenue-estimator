"""Test configuration and fixtures."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from restaurant_revenue_estimator import database
from restaurant_revenue_estimator.app import app
from restaurant_revenue_estimator.database import get_session
from tests.eval_cache import EXCLUDED_TESTS, TARGET_FILES, CachedEvaluate

_session_cached_evaluate = CachedEvaluate()

PATCH_TARGETS: tuple[str, ...] = (
    "restaurant_revenue_estimator.cli.evaluate",
    "restaurant_revenue_estimator.engine.scenario.scenario.evaluate",
    "restaurant_revenue_estimator.services.evaluation.evaluate",
    "restaurant_revenue_estimator.services.evaluation_service.evaluate",
    "restaurant_revenue_estimator.services.reporting.evaluate",
)


@pytest.fixture(autouse=True)
def patch_evaluation_cache(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TARGET_FILES かつ EXCLUDED_TESTS 以外のテストで
    evaluate をキャッシュ版に差し替える.
    """
    path = getattr(request, "path", None)
    filename = path.name if path is not None else Path(request.node.fspath).name
    test_name = getattr(request.node, "originalname", None) or request.node.name
    is_excluded = test_name in EXCLUDED_TESTS or request.node.name in EXCLUDED_TESTS
    if filename in TARGET_FILES and not is_excluded:
        for target in PATCH_TARGETS:
            monkeypatch.setattr(target, _session_cached_evaluate)


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    """テスト用のインメモリ SQLite エンジン (スレッド共有)."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    database.set_engine(eng)
    try:
        yield eng
    finally:
        database.reset_engine()
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Generator[Session, None, None]:
    """各テスト用の SQLModel セッション."""
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine: Engine) -> Generator[TestClient, None, None]:
    """FastAPI テストクライアント (in-memory DB を共有)."""

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
