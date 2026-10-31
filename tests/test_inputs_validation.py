"""`rre validate` と入力検査 (TC-02-xx)."""

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from restaurant_revenue_estimator.cli import app
from restaurant_revenue_estimator.inputs import (
    input_hash,
    load_store,
    validate_all,
    validate_store,
)

runner = CliRunner()

SOURCE = {
    "id": "S01",
    "url": "https://example.com/store/111",
    "accessed_at": "2026-10-30",
    "published_at": "2026-10-01",
    "facts": ["席数 20"],
    "license_status": "unknown",
    "body_stored": False,
}


def _value(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "value": 20,
        "unit": "seats",
        "status": "PUBLIC_VERIFIED",
        "sources": ["S01"],
    }
    base.update(overrides)
    return base


def write_store(
    root: Path,
    store_id: str = "test_store",
    *,
    store: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    layout: dict[str, Any] | None = None,
    observations: list[dict[str, Any]] | None = None,
) -> Path:
    """最小構成の店舗ディレクトリを作る."""
    directory = root / store_id
    directory.mkdir(parents=True, exist_ok=True)
    store_data: dict[str, Any] = {
        "store_id": store_id,
        "name": "テスト店",
        "address": "東京都千代田区1-1-1",
        "phone": "03-0000-0001",
        "external_ids": {"tabelog": "111"},
        "related_stores": [],
    }
    if store:
        store_data.update(store)
    (directory / "store.yaml").write_text(
        yaml.safe_dump(store_data, allow_unicode=True), encoding="utf-8"
    )
    (directory / "sources.yaml").write_text(
        yaml.safe_dump(
            {"sources": sources if sources is not None else [SOURCE]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (directory / "observations.yaml").write_text(
        yaml.safe_dump(
            {
                "observations": observations
                or [
                    {
                        "id": "OBS-001",
                        "at": "2026-10-30T12:00",
                        "floor": 1,
                        "note": "観察",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (directory / "layout.yaml").write_text(
        yaml.safe_dump(
            layout or {"floors": [{"id": "1f", "tables": _value()}]}, allow_unicode=True
        ),
        encoding="utf-8",
    )
    return directory


def _messages(directory: Path) -> list[str]:
    return [str(issue) for issue in validate_store(directory)]


def test_public_verified_without_sources(tmp_path: Path) -> None:
    directory = write_store(
        tmp_path / "stores", layout={"floors": [_value(sources=[])]}
    )
    messages = _messages(directory)
    assert any("layout.yaml" in m and "floors" in m for m in messages)


def test_source_without_accessed_at(tmp_path: Path) -> None:
    source = dict(SOURCE)
    source.pop("accessed_at")
    directory = write_store(tmp_path / "stores", sources=[source])
    assert any("accessed_at" in m for m in _messages(directory))


@pytest.mark.parametrize("missing", ["justification", "range"])
def test_assumption_requires_justification_and_range(
    tmp_path: Path, missing: str
) -> None:
    value = _value(
        status="ASSUMPTION",
        sources=[],
        justification="仮置き",
        range={"low": 10, "base": 20, "high": 30},
    )
    value.pop(missing)
    directory = write_store(tmp_path / "stores", layout={"floors": [value]})
    assert any(missing in m for m in _messages(directory))


def test_unresolved_requires_null_value_and_candidates(tmp_path: Path) -> None:
    directory = write_store(
        tmp_path / "stores",
        layout={"floors": [_value(status="UNRESOLVED", value=10, candidates={})]},
    )
    messages = _messages(directory)
    assert any("null" in m for m in messages)
    assert any("candidates" in m for m in messages)


def test_derived_is_rejected_in_inputs(tmp_path: Path) -> None:
    directory = write_store(
        tmp_path / "stores", layout={"floors": [_value(status="DERIVED")]}
    )
    assert any("DERIVED" in m for m in _messages(directory))


def test_observed_requires_known_observation_id(tmp_path: Path) -> None:
    missing = write_store(
        tmp_path / "missing",
        layout={"floors": [_value(status="OBSERVED", sources=[], observation_id=None)]},
    )
    unknown = write_store(
        tmp_path / "unknown",
        layout={
            "floors": [_value(status="OBSERVED", sources=[], observation_id="OBS-999")]
        },
    )
    assert any("observation_id" in m for m in _messages(missing))
    assert any("OBS-999" in m for m in _messages(unknown))


def test_store_identity_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "data" / "stores"
    write_store(root, "store_a")
    write_store(
        root, "store_b", store={"address": "東京都港区2-2-2", "phone": "03-0000-0002"}
    )
    messages = [str(issue) for issue in validate_all(tmp_path / "data")]
    assert any("store_a" in m and "store_b" in m for m in messages)
    assert any("tabelog" in m for m in messages)


def test_source_body_stored_is_rejected(tmp_path: Path) -> None:
    source = dict(SOURCE, body_stored=True)
    directory = write_store(tmp_path / "stores", sources=[source])
    assert any("body_stored" in m for m in _messages(directory))


def test_input_hash_is_order_independent(tmp_path: Path) -> None:
    first = write_store(tmp_path / "a")
    before = input_hash(load_store(first))
    layout = yaml.safe_load((first / "layout.yaml").read_text(encoding="utf-8"))
    reordered = {key: layout[key] for key in reversed(list(layout))}
    (first / "layout.yaml").write_text(
        yaml.safe_dump(reordered, allow_unicode=True), encoding="utf-8"
    )
    assert input_hash(load_store(first)) == before

    layout["floors"] = [_value(value=99)]
    (first / "layout.yaml").write_text(
        yaml.safe_dump(layout, allow_unicode=True), encoding="utf-8"
    )
    assert input_hash(load_store(first)) != before


def test_assumption_with_null_value_is_rejected(tmp_path: Path) -> None:
    value = _value(
        status="ASSUMPTION",
        value=None,
        sources=[],
        justification="仮置き",
        range={"low": 1, "base": 2, "high": 3},
    )
    directory = write_store(tmp_path / "stores", layout={"floors": [value]})
    assert any("null" in m for m in _messages(directory))


def test_related_store_source_is_rejected(tmp_path: Path) -> None:
    related = {
        "name": "姉妹店",
        "relation": "sister",
        "included": False,
        "external_ids": {"tabelog": "999"},
        "source": "S01b",
    }
    sister_source = {
        "id": "S01b",
        "url": "https://example.com/store/999",
        "accessed_at": "2026-10-30",
        "facts": ["姉妹店 30 席"],
    }
    directory = write_store(
        tmp_path / "stores",
        store={"related_stores": [related]},
        sources=[SOURCE, sister_source],
        layout={"floors": [_value(sources=["S01b"])]},
    )
    assert any("姉妹店" in m for m in _messages(directory))


def test_valid_fixture_passes(tmp_path: Path) -> None:
    directory = write_store(tmp_path / "stores")
    assert validate_store(directory) == []
    result = runner.invoke(app, ["validate", str(directory)])
    assert result.exit_code == 0


def test_validate_all_reports_errors_with_exit_code_1(tmp_path: Path) -> None:
    root = tmp_path / "data" / "stores"
    write_store(root, "store_a", layout={"floors": [_value(sources=[])]})
    result = runner.invoke(
        app, ["validate", "--all", "--data-dir", str(tmp_path / "data")]
    )
    assert result.exit_code == 1
    assert "layout.yaml" in result.stdout


def test_validate_does_not_modify_inputs(tmp_path: Path) -> None:
    directory = write_store(tmp_path / "stores")
    before = (directory / "layout.yaml").read_bytes()
    validate_store(directory)
    assert (directory / "layout.yaml").read_bytes() == before
    shutil.rmtree(directory)
