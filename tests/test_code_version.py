import json
import re
from datetime import UTC, datetime
from pathlib import Path

import alembic.command
import alembic.config
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine, inspect

import restaurant_revenue_estimator
import restaurant_revenue_estimator.routes.pages
from restaurant_revenue_estimator import code_version, database
from restaurant_revenue_estimator.config import data_directory, reset_settings
from restaurant_revenue_estimator.models import Run
from restaurant_revenue_estimator.repositories.scenario_repo import RunRepository
from restaurant_revenue_estimator.services.evaluation_service import (
    RunRequest,
    execute_run,
)


def test_tc_36_01_hash_changes_for_target_files_and_not_for_excluded_files(
    tmp_path: Path,
) -> None:
    """TC-36-01: 計算に関わるファイルを変えるとハッシュが変わり、
    外したファイルでは変わらない
    """
    pkg = tmp_path / "fake_pkg"
    pkg.mkdir()
    (pkg / "engine").mkdir()
    (pkg / "services").mkdir()
    (pkg / "inputs").mkdir()
    (pkg / "routes").mkdir()
    (pkg / "alembic").mkdir()

    (pkg / "engine" / "a.py").write_text("print('a')", encoding="utf-8")
    (pkg / "services" / "b.py").write_text("print('b')", encoding="utf-8")
    (pkg / "inputs" / "c.py").write_text("print('c')", encoding="utf-8")
    (pkg / "routes" / "r.py").write_text("print('r')", encoding="utf-8")
    (pkg / "alembic" / "env.py").write_text("print('env')", encoding="utf-8")
    (pkg / "app.py").write_text("print('app')", encoding="utf-8")
    (pkg / "models.py").write_text("print('models')", encoding="utf-8")
    (pkg / "engine" / "notes.txt").write_text("notes", encoding="utf-8")

    # 手順と期待: 最初に compute_code_sha256(tmp_path) を取る。
    # 何も変えずに 2 回計算すると同じ値になる。値は 64 文字の 16 進
    h0 = code_version.compute_code_sha256(pkg)
    assert len(h0) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", h0) is not None
    assert code_version.compute_code_sha256(pkg) == h0

    # - engine/a.py の中身を変える → 変わる
    (pkg / "engine" / "a.py").write_text("print('a modified')", encoding="utf-8")
    h1 = code_version.compute_code_sha256(pkg)
    assert h1 != h0

    # - inputs/c.py の中身を変える → 変わる
    (pkg / "inputs" / "c.py").write_text("print('c modified')", encoding="utf-8")
    h2 = code_version.compute_code_sha256(pkg)
    assert h2 != h1

    # - 新しいディレクトリ newcalc/d.py を足す → 変わる
    (pkg / "newcalc").mkdir()
    (pkg / "newcalc" / "d.py").write_text("print('d')", encoding="utf-8")
    h3 = code_version.compute_code_sha256(pkg)
    assert h3 != h2

    # - engine/a.py を同じ中身のまま engine/a2.py に名前を変える → 変わる
    a_content = (pkg / "engine" / "a.py").read_text(encoding="utf-8")
    (pkg / "engine" / "a.py").unlink()
    (pkg / "engine" / "a2.py").write_text(a_content, encoding="utf-8")
    h4 = code_version.compute_code_sha256(pkg)
    assert h4 != h3

    # - routes/r.py の中身を変える → 変わらない
    (pkg / "routes" / "r.py").write_text("print('r modified')", encoding="utf-8")
    h5 = code_version.compute_code_sha256(pkg)
    assert h5 == h4

    # - alembic/env.py の中身を変える → 変わらない
    (pkg / "alembic" / "env.py").write_text("print('env modified')", encoding="utf-8")
    h6 = code_version.compute_code_sha256(pkg)
    assert h6 == h5

    # - app.py・models.py の中身を変える → 変わらない
    (pkg / "app.py").write_text("print('app modified')", encoding="utf-8")
    (pkg / "models.py").write_text("print('models modified')", encoding="utf-8")
    h7 = code_version.compute_code_sha256(pkg)
    assert h7 == h6

    # - engine/notes.txt の中身を変える → 変わらない
    (pkg / "engine" / "notes.txt").write_text("notes modified", encoding="utf-8")
    h8 = code_version.compute_code_sha256(pkg)
    assert h8 == h7


def test_tc_36_02_current_code_sha256_matches_package_dir() -> None:
    """TC-36-02: current_code_sha256 は実際のパッケージのハッシュを返す"""
    pkg_dir = Path(restaurant_revenue_estimator.__file__).parent
    expected = code_version.compute_code_sha256(pkg_dir)
    actual = code_version.current_code_sha256()
    assert actual == expected


def test_additional_excluded_files_and_subdirectories(tmp_path: Path) -> None:
    """追加テスト: EXCLUDED_FILES の残りと
    EXCLUDED_DIRS 配下のサブディレクトリが除外されることの検証
    """
    pkg = tmp_path / "fake_pkg2"
    pkg.mkdir()
    (pkg / "engine").mkdir()
    (pkg / "routes" / "sub").mkdir(parents=True)
    (pkg / "alembic" / "versions").mkdir(parents=True)

    (pkg / "engine" / "core.py").write_text("print('core')", encoding="utf-8")
    base_hash = code_version.compute_code_sha256(pkg)

    # EXCLUDED_FILES: cli.py, database.py, config.py, logging_config.py
    for fname in ("cli.py", "database.py", "config.py", "logging_config.py"):
        (pkg / fname).write_text(f"# {fname}", encoding="utf-8")
        assert code_version.compute_code_sha256(pkg) == base_hash

    # EXCLUDED_DIRS のサブディレクトリ内 .py
    (pkg / "routes" / "sub" / "deep.py").write_text(
        "print('deep route')", encoding="utf-8"
    )
    assert code_version.compute_code_sha256(pkg) == base_hash

    (pkg / "alembic" / "versions" / "rev.py").write_text(
        "print('rev')", encoding="utf-8"
    )
    assert code_version.compute_code_sha256(pkg) == base_hash


def test_tc_36_08_init_db_adds_column_to_existing_db(tmp_path: Path) -> None:
    """TC-36-08: 列の無い既存の DB を起動時に直す"""
    db_file = tmp_path / "existing.db"
    db_url = f"sqlite:///{db_file}"
    eng = create_engine(db_url)

    with eng.connect() as conn:
        conn.execute(
            sa.text("""
            CREATE TABLE scenarios (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                store_id VARCHAR(100) NOT NULL,
                name VARCHAR(200) NOT NULL,
                base_assumption_set VARCHAR(20) NOT NULL,
                overrides_json VARCHAR NOT NULL,
                note VARCHAR(1000) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
        """)
        )
        conn.execute(
            sa.text("""
            CREATE TABLE runs (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                store_id VARCHAR(100) NOT NULL,
                scenario_id INTEGER,
                assumption_set VARCHAR(20) NOT NULL,
                calendar_mode VARCHAR(20) NOT NULL,
                year INTEGER NOT NULL,
                seed INTEGER NOT NULL,
                replications INTEGER NOT NULL,
                input_hash VARCHAR(64) NOT NULL,
                model_version VARCHAR(20) NOT NULL,
                calendar_sha256 VARCHAR(64) NOT NULL,
                result_json VARCHAR NOT NULL,
                report_md VARCHAR NOT NULL,
                created_at DATETIME NOT NULL
            );
        """)
        )
        conn.execute(
            sa.text("""
            INSERT INTO runs (
                store_id, scenario_id, assumption_set, calendar_mode, year, seed,
                replications, input_hash, model_version, calendar_sha256,
                result_json, report_md, created_at
            ) VALUES (
                'birdman_tamachi', NULL, 'standard', 'steady', 2026, 42,
                20, 'hash123', '0.1.0', 'calhash',
                '{}', '', '2026-10-31 00:00:00'
            );
        """)
        )
        conn.commit()

    # 手順: その engine で init_db(engine) を 2 回呼ぶ
    database.init_db(eng)
    database.init_db(eng)

    # 期待: エラーが出ない。runs に code_sha256 列が 1 つだけある。
    # 入れておいた行は残っていて、code_sha256 は空文字
    inspector = inspect(eng)
    columns = [c["name"] for c in inspector.get_columns("runs")]
    assert columns.count("code_sha256") == 1

    with eng.connect() as conn:
        rows = conn.execute(sa.text("SELECT id, code_sha256 FROM runs")).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == ""

    eng.dispose()


def test_tc_36_09_alembic_upgrade_and_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-36-09: alembic でも同じ列ができる"""
    db_file = tmp_path / "alembic_test.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    reset_settings()

    try:
        cfg = alembic.config.Config("alembic.ini")
        alembic.command.upgrade(cfg, "head")

        eng = create_engine(db_url)
        cols_after_upgrade = [c["name"] for c in inspect(eng).get_columns("runs")]
        assert "code_sha256" in cols_after_upgrade
        eng.dispose()

        alembic.command.downgrade(cfg, "-1")

        eng2 = create_engine(db_url)
        cols_after_downgrade = [c["name"] for c in inspect(eng2).get_columns("runs")]
        assert "code_sha256" not in cols_after_downgrade
        eng2.dispose()
    finally:
        reset_settings()


def test_tc_36_03_find_filters_by_code_sha256(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-36-03: find は code_sha256 が一致する run だけを返す"""
    run = Run(
        store_id="birdman_tamachi",
        scenario_id=None,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        seed=42,
        replications=20,
        input_hash="hash_tc03",
        code_sha256="x" * 64,
        result_json="{}",
    )
    repo = RunRepository(session)
    repo.add(run)

    found_x = repo.find(
        store_id="birdman_tamachi",
        input_hash="hash_tc03",
        seed=42,
        replications=20,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        scenario_id=None,
        code_sha256="x" * 64,
    )
    assert found_x is not None
    assert found_x.id == run.id

    found_y = repo.find(
        store_id="birdman_tamachi",
        input_hash="hash_tc03",
        seed=42,
        replications=20,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        scenario_id=None,
        code_sha256="y" * 64,
    )
    assert found_y is None

    monkeypatch.setattr(code_version, "current_code_sha256", lambda: "x" * 64)
    found_default_x = repo.find(
        store_id="birdman_tamachi",
        input_hash="hash_tc03",
        seed=42,
        replications=20,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        scenario_id=None,
    )
    assert found_default_x is not None
    assert found_default_x.id == run.id

    monkeypatch.setattr(code_version, "current_code_sha256", lambda: "y" * 64)
    found_default_y = repo.find(
        store_id="birdman_tamachi",
        input_hash="hash_tc03",
        seed=42,
        replications=20,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        scenario_id=None,
    )
    assert found_default_y is None


def test_tc_36_04_latest_default_filters_by_code_sha256(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-36-04: latest_default は現在のハッシュの run だけを返す"""
    repo = RunRepository(session)
    old_run = Run(
        store_id="birdman_tamachi",
        scenario_id=None,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        seed=42,
        replications=20,
        input_hash="hash_old",
        code_sha256="y" * 64,
        result_json=json.dumps({"meta": {"overrides": {}}}),
        created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    new_run = Run(
        store_id="birdman_tamachi",
        scenario_id=None,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        seed=42,
        replications=20,
        input_hash="hash_new",
        code_sha256="x" * 64,
        result_json=json.dumps({"meta": {"overrides": {}}}),
        created_at=datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC),
    )
    repo.add(old_run)
    repo.add(new_run)

    assert repo.latest_default("birdman_tamachi", code_sha256="y" * 64).id == old_run.id
    assert repo.latest_default("birdman_tamachi", code_sha256="z" * 64) is None

    monkeypatch.setattr(code_version, "current_code_sha256", lambda: "y" * 64)
    assert repo.latest_default("birdman_tamachi").id == old_run.id

    monkeypatch.setattr(code_version, "current_code_sha256", lambda: "x" * 64)
    assert repo.latest_default("birdman_tamachi").id == new_run.id

    monkeypatch.setattr(code_version, "current_code_sha256", lambda: "z" * 64)
    assert repo.latest_default("birdman_tamachi") is None


def test_tc_36_05_execute_run_reuses_if_hash_matches_and_recreates_if_changed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-36-05: execute_run はハッシュが同じなら再利用し、変わったら作り直す"""
    current_hash = "a" * 64
    monkeypatch.setattr(code_version, "current_code_sha256", lambda: current_hash)
    data_dir = data_directory()
    req = RunRequest(replications=2, seed=42)

    # 1 回目の execute_run → 新しい run（code_sha256 == "a"*64）
    run1 = execute_run(session, data_dir, "birdman_tamachi", req)
    assert run1.code_sha256 == "a" * 64

    # 同じ条件で 2 回目 → 1 回目と同じ id
    run2 = execute_run(session, data_dir, "birdman_tamachi", req)
    assert run2.id == run1.id

    # current_code_sha256 を "b"*64 に変えて 3 回目 → 新しい id、code_sha256 == "b"*64
    current_hash = "b" * 64
    run3 = execute_run(session, data_dir, "birdman_tamachi", req)
    assert run3.id != run1.id
    assert run3.code_sha256 == "b" * 64

    # "a"*64 に戻して 4 回目 → 1 回目と同じ id
    current_hash = "a" * 64
    run4 = execute_run(session, data_dir, "birdman_tamachi", req)
    assert run4.id == run1.id


def test_tc_36_06_store_page_recreates_if_only_old_hash_exists(
    engine: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-36-06: 店舗画面は、古いハッシュの run しか無ければ作り直す"""
    fake_result_json = json.dumps(
        {
            "store_id": "birdman_tamachi",
            "variant": "f2_42",
            "year": 2026,
            "calendar_mode": "steady",
            "assumption_set": "standard",
            "open_days": 240,
            "revenue": {
                "total_inc_tax": 78493670,
                "total_ex_tax": 71357881,
                "lunch_inc_tax": 19745620,
                "dinner_inc_tax": 58748050,
                "ala_inc_tax": 57289120,
                "course_inc_tax": 17450200,
                "months": [],
            },
            "simulation": {
                "lunch_guests_by_weekday": {},
                "lost_lunch_guests": 0.0,
                "dinner_guests": 0,
                "tables": [],
            },
            "pnl": {
                "gross_inc_tax": 78493670,
                "gross_ex_tax": 71357881,
                "cogs": 23916582,
                "gross_profit": 47441299,
                "labor": 7810800,
                "store_operating_profit": 24390499,
                "business_income": 24270499,
                "owner_labor": 5760000,
                "cash_flow": 23670499,
                "rent": 9600000,
                "other_expenses": 4080000,
                "depreciation": 600000,
                "interest": 120000,
                "principal": 1200000,
                "capex": 0,
            },
            "meta": {
                "input_hash": (
                    "cb1b6a7c913606a972790ce6d9031928"
                    "5fb4816185314c7d00299cca2237c4a2"
                ),
                "model_version": "0.1.0",
                "calendar_sha256": (
                    "e60c826802ac84e255ac047a2614e83b"
                    "4d7f1572ed26d4c0f5d7cdd58cabd31e"
                ),
                "seed": 42,
                "replications": 1,
                "overrides": {},
            },
            "unresolved": [],
            "overrides": [],
        }
    )

    with Session(engine) as session:
        old_run = Run(
            store_id="birdman_tamachi",
            scenario_id=None,
            assumption_set="standard",
            calendar_mode="steady",
            year=2026,
            seed=42,
            replications=20,
            input_hash="hash_old_store",
            code_sha256="old",
            result_json=fake_result_json,
        )
        session.add(old_run)
        session.commit()

    monkeypatch.setattr(code_version, "current_code_sha256", lambda: "new")

    called_args: list[tuple[str, RunRequest]] = []

    def fake_execute_run(
        session: Session, data_dir: Path, store_id: str, req: RunRequest
    ) -> Run:
        called_args.append((store_id, req))
        new_run = Run(
            store_id=store_id,
            scenario_id=None,
            assumption_set="standard",
            calendar_mode="steady",
            year=2026,
            seed=req.seed,
            replications=req.replications,
            input_hash="fake_hash",
            code_sha256="new",
            result_json=fake_result_json,
        )
        session.add(new_run)
        session.commit()
        session.refresh(new_run)
        return new_run

    monkeypatch.setattr(
        restaurant_revenue_estimator.routes.pages, "execute_run", fake_execute_run
    )

    response = client.get("/stores/birdman_tamachi")
    assert response.status_code == 200

    assert len(called_args) == 1
    assert called_args[0][0] == "birdman_tamachi"


def test_tc_36_07_index_page_does_not_display_old_hash_numbers(
    engine: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-36-07: トップ画面は、古いハッシュの run の数字を出さない"""
    with Session(engine) as session:
        old_run = Run(
            store_id="birdman_tamachi",
            scenario_id=None,
            assumption_set="standard",
            calendar_mode="steady",
            year=2026,
            seed=42,
            replications=20,
            input_hash="hash_old_index",
            code_sha256="old",
            result_json=json.dumps(
                {
                    "meta": {"overrides": {}},
                    "revenue": {"total_inc_tax": 987654321},
                    "pnl": {"business_income": 987654321},
                }
            ),
        )
        session.add(old_run)
        session.commit()

    monkeypatch.setattr(code_version, "current_code_sha256", lambda: "new")

    response = client.get("/")

    assert response.status_code == 200
    assert "987,654,321" not in response.text


def test_alembic_upgrade_idempotent_after_init_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init_db 済みの DB で alembic upgrade head が重複列エラーにならず通る"""
    db_file = tmp_path / "alembic_init_db.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    reset_settings()

    try:
        cfg = alembic.config.Config("alembic.ini")
        alembic.command.upgrade(cfg, "20261031_0001")

        eng = create_engine(db_url)
        database.init_db(eng)

        alembic.command.upgrade(cfg, "head")

        inspector = inspect(eng)
        columns = [c["name"] for c in inspector.get_columns("runs")]
        assert columns.count("code_sha256") == 1

        alembic.command.downgrade(cfg, "-1")

        inspector2 = inspect(eng)
        cols_after = [c["name"] for c in inspector2.get_columns("runs")]
        assert "code_sha256" not in cols_after
        eng.dispose()
    finally:
        reset_settings()
