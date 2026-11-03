"""曜日ごとの昼の到着組数倍率モデル (Issue #63)."""

from pathlib import Path

import pytest
import yaml

from restaurant_revenue_estimator.engine.lunch import (
    lunch_arrival_groups_for_weekday,
)
from restaurant_revenue_estimator.inputs import validate_store
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    evaluate,
)


def test_case_1_lunch_arrival_groups_for_weekday() -> None:
    """Case 1: 曜日ごとの組数."""
    groups = 45.0
    factors = {
        "mon": 0.778,
        "tue": 1.0,
        "wed": 0.989,
        "thu": 1.108,
        "fri": 1.126,
    }
    assert lunch_arrival_groups_for_weekday(groups, factors, "mon") == 45.0 * 0.778
    assert lunch_arrival_groups_for_weekday(groups, factors, "tue") == 45.0 * 1.0
    assert lunch_arrival_groups_for_weekday(groups, factors, "wed") == 45.0 * 0.989
    assert lunch_arrival_groups_for_weekday(groups, factors, "thu") == 45.0 * 1.108
    assert lunch_arrival_groups_for_weekday(groups, factors, "fri") == 45.0 * 1.126
    assert lunch_arrival_groups_for_weekday(groups, factors, "sat") == 45.0
    assert lunch_arrival_groups_for_weekday(groups, None, "mon") == 45.0


def test_case_2_input_validation() -> None:
    """Case 2: 入力の検査."""
    base_factors = {
        "mon": 0.778,
        "tue": 1.0,
        "wed": 0.989,
        "thu": 1.108,
        "fri": 1.126,
    }
    invalid_values = [0, -1, float("nan"), float("inf"), "a", True]
    for inv in invalid_values:
        f = dict(base_factors)
        f["mon"] = inv  # type: ignore[assignment]
        with pytest.raises(ValueError):
            lunch_arrival_groups_for_weekday(45.0, f, "mon")

    # キーに "xyz" を足したもの
    f_extra_key = dict(base_factors)
    f_extra_key["xyz"] = 1.0
    with pytest.raises(ValueError):
        lunch_arrival_groups_for_weekday(45.0, f_extra_key, "mon")


def test_case_3_evaluation_lunch_arrival_weekday_factor() -> None:
    """Case 3: 評価で、倍率なしの上書きはいまの値、標準は曜日ごとに変わる."""
    data_dir = Path(__file__).resolve().parents[1] / "data"

    req_override = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=5,
        overrides={
            "layout.floor2": "f2_42",
            "lunch_arrival_weekday_factor": {
                "mon": 1,
                "tue": 1,
                "wed": 1,
                "thu": 1,
                "fri": 1,
            },
        },
    )
    res_override = evaluate(data_dir, req_override)[0]
    assert res_override.lunch_guests_by_weekday == {
        "mon": 74,
        "tue": 76,
        "wed": 77,
        "thu": 78,
        "fri": 76,
    }
    assert round(res_override.lost_lunch_guests, 2) == 23.02

    req_standard = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=5,
        overrides={"layout.floor2": "f2_42"},
    )
    res_standard = evaluate(data_dir, req_standard)[0]
    assert res_standard.lunch_guests_by_weekday == {
        "mon": 67,
        "tue": 76,
        "wed": 75,
        "thu": 79,
        "fri": 79,
    }


def test_case_4_loader_input_validation(tmp_path: Path) -> None:
    """Case 4: 読み込み時の検査."""

    def _create_store_with_factor(factor_data: dict[str, object] | object) -> Path:
        store_dir = tmp_path / f"store_{len(list(tmp_path.iterdir()))}"
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / "store.yaml").write_text(
            yaml.safe_dump(
                {
                    "store_id": "test_store",
                    "name": "テスト店",
                    "address": "東京都千代田区1-1-1",
                    "phone": "03-0000-0001",
                    "external_ids": {"tabelog": "111"},
                    "related_stores": [],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        (store_dir / "sources.yaml").write_text(
            yaml.safe_dump(
                {
                    "sources": [
                        {
                            "id": "S01",
                            "url": "https://example.com/store/111",
                            "accessed_at": "2026-10-30",
                            "published_at": "2026-10-01",
                            "facts": ["席数 20"],
                            "license_status": "unknown",
                            "body_stored": False,
                        }
                    ]
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        (store_dir / "observations.yaml").write_text(
            yaml.safe_dump(
                {
                    "observations": [
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
        (store_dir / "layout.yaml").write_text(
            yaml.safe_dump(
                {
                    "floors": [
                        {
                            "id": "1f",
                            "tables": {
                                "value": 20,
                                "unit": "seats",
                                "status": "PUBLIC_VERIFIED",
                                "sources": ["S01"],
                            },
                        }
                    ]
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        assumptions_dir = store_dir / "assumptions"
        assumptions_dir.mkdir(parents=True, exist_ok=True)
        (assumptions_dir / "standard.yaml").write_text(
            yaml.safe_dump(
                {"lunch_arrival_weekday_factor": factor_data},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return store_dir

    # 1. value の mon を 0 にしたもの
    bad_val_store = _create_store_with_factor({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": {"mon": 0, "tue": 1.0, "wed": 1.0, "thu": 1.0, "fri": 1.0},
        "range": {
            "low": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
            "base": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
            "high": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
        },
    })
    issues_bad_val = validate_store(bad_val_store)
    assert any(
        i.path == "lunch_arrival_weekday_factor" for i in issues_bad_val
    )

    # 2. range.low を数値 1 にしたもの
    bad_range_store = _create_store_with_factor({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": {"mon": 1.0, "tue": 1.0, "wed": 1.0, "thu": 1.0, "fri": 1.0},
        "range": {
            "low": 1,
            "base": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
            "high": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
        },
    })
    issues_bad_range = validate_store(bad_range_store)
    assert any(
        i.path == "lunch_arrival_weekday_factor.range.low"
        for i in issues_bad_range
    )

    # 3. value にキー xyz を足したもの
    bad_key_store = _create_store_with_factor({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": {
            "mon": 1.0,
            "tue": 1.0,
            "wed": 1.0,
            "thu": 1.0,
            "fri": 1.0,
            "xyz": 1.0,
        },
        "range": {
            "low": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
            "base": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
            "high": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
        },
    })
    issues_bad_key = validate_store(bad_key_store)
    assert any(
        i.path == "lunch_arrival_weekday_factor" for i in issues_bad_key
    )

    # 4. 正しい値のもの
    good_store = _create_store_with_factor({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": {
            "mon": 0.778,
            "tue": 1.0,
            "wed": 0.989,
            "thu": 1.108,
            "fri": 1.126,
        },
        "range": {
            "low": {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1},
            "base": {
                "mon": 0.778,
                "tue": 1.0,
                "wed": 0.989,
                "thu": 1.108,
                "fri": 1.126,
            },
            "high": {
                "mon": 0.778,
                "tue": 1.0,
                "wed": 0.989,
                "thu": 1.108,
                "fri": 1.126,
            },
        },
    })
    issues_good = validate_store(good_store)
    assert not any(
        "lunch_arrival_weekday_factor" in i.path for i in issues_good
    )


def test_empty_factors_map() -> None:
    """追加テスト: 空の倍率表が渡されたときにデフォルト 1 が使われることの確認."""
    assert lunch_arrival_groups_for_weekday(45.0, {}, "mon") == 45.0


def test_weekend_factor() -> None:
    """追加テスト: 土日の倍率が指定されている場合に正しく適用されることの確認."""
    factors = {"sat": 1.5, "sun": 0.5}
    assert lunch_arrival_groups_for_weekday(40.0, factors, "sat") == 40.0 * 1.5
    assert lunch_arrival_groups_for_weekday(40.0, factors, "sun") == 40.0 * 0.5


def test_loader_range_high_null_validation(tmp_path: Path) -> None:
    """追加テスト: range.high が null のとき検査エラーとなることの確認."""
    store_dir = tmp_path / "store_range_high_null"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "store.yaml").write_text(
        yaml.safe_dump(
            {
                "store_id": "test_store",
                "name": "テスト店",
                "address": "東京都千代田区1-1-1",
                "phone": "03-0000-0001",
                "external_ids": {"tabelog": "111"},
                "related_stores": [],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (store_dir / "sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "id": "S01",
                        "url": "https://example.com/store/111",
                        "accessed_at": "2026-10-30",
                        "published_at": "2026-10-01",
                        "facts": ["席数 20"],
                        "license_status": "unknown",
                        "body_stored": False,
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (store_dir / "observations.yaml").write_text(
        yaml.safe_dump(
            {
                "observations": [
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
    (store_dir / "layout.yaml").write_text(
        yaml.safe_dump(
            {
                "floors": [
                    {
                        "id": "1f",
                        "tables": {
                            "value": 20,
                            "unit": "seats",
                            "status": "PUBLIC_VERIFIED",
                            "sources": ["S01"],
                        },
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assumptions_dir = store_dir / "assumptions"
    assumptions_dir.mkdir(parents=True, exist_ok=True)
    (assumptions_dir / "standard.yaml").write_text(
        yaml.safe_dump(
            {
                "lunch_arrival_weekday_factor": {
                    "unit": "ratio",
                    "status": "ASSUMPTION",
                    "justification": "テスト用",
                    "how_to_verify": "テスト用",
                    "value": {
                        "mon": 1.0,
                        "tue": 1.0,
                        "wed": 1.0,
                        "thu": 1.0,
                        "fri": 1.0,
                    },
                    "range": {
                        "low": {
                            "mon": 1,
                            "tue": 1,
                            "wed": 1,
                            "thu": 1,
                            "fri": 1,
                        },
                        "base": {
                            "mon": 1,
                            "tue": 1,
                            "wed": 1,
                            "thu": 1,
                            "fri": 1,
                        },
                        "high": None,
                    },
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    issues = validate_store(store_dir)
    assert any(
        i.path == "lunch_arrival_weekday_factor.range.high" for i in issues
    )

