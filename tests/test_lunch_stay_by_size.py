"""組の人数ごとの昼の滞在モデル (Issue #60)."""

from pathlib import Path

import numpy as np
import pytest
import yaml

from restaurant_revenue_estimator.engine.lunch import (
    arrival_profile,
    lunch_stay_by_group_size,
    simulate_lunch_day,
)
from restaurant_revenue_estimator.engine.observation import fit_grid
from restaurant_revenue_estimator.engine.tables import (
    Floor,
    SimulationConfig,
    Table,
    simulate,
)
from restaurant_revenue_estimator.engine.tables.simulation import _sample_arrivals
from restaurant_revenue_estimator.inputs import validate_store
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    evaluate,
)


def test_case_1_ratio_normalized_by_mean() -> None:
    """Case 1: 比を平均で割り戻す."""
    stay_minutes = 40
    ratios = {1: 27.6, 2: 44.9, 3: 47.2, 4: 52.3}
    group_sizes = [1, 2, 3, 4]
    group_probs = [0.25, 0.5, 0.15, 0.1]

    result = lunch_stay_by_group_size(
        stay_minutes, ratios, group_sizes, group_probs
    )

    assert result[1] == pytest.approx(26.500, abs=0.001)
    assert result[2] == pytest.approx(43.111, abs=0.001)
    assert result[3] == pytest.approx(45.319, abs=0.001)
    assert result[4] == pytest.approx(50.216, abs=0.001)

    weighted_mean = sum(
        p * result[s] for p, s in zip(group_probs, group_sizes, strict=True)
    )
    assert weighted_mean == pytest.approx(40.0, abs=1e-9)


def test_case_2_identical_ratios_equal_mean_exactly() -> None:
    """Case 2: 比がすべて同じなら平均そのまま."""
    group_sizes = [1, 2, 3, 4]
    group_probs = [0.25, 0.5, 0.15, 0.1]

    res1 = lunch_stay_by_group_size(
        40, {1: 1, 2: 1, 3: 1, 4: 1}, group_sizes, group_probs
    )
    res2 = lunch_stay_by_group_size(
        40, {1: 2, 2: 2, 3: 2, 4: 2}, group_sizes, group_probs
    )

    assert res1 == {1: 40, 2: 40, 3: 40, 4: 40}
    assert res2 == {1: 40, 2: 40, 3: 40, 4: 40}


def test_case_3_input_validation() -> None:
    """Case 3: 入力の検査."""
    group_sizes = [1, 2, 3, 4]
    group_probs = [0.25, 0.5, 0.15, 0.1]

    # 欠けの検査 (4 が欠けている)
    with pytest.raises(ValueError) as exc_info:
        lunch_stay_by_group_size(
            40, {1: 27.6, 2: 44.9, 3: 47.2}, group_sizes, group_probs
        )
    assert "4" in str(exc_info.value)

    # 不正な値の検査
    invalid_values = [0, -1, float("nan"), float("inf"), "a"]
    for inv in invalid_values:
        with pytest.raises(ValueError):
            lunch_stay_by_group_size(
                40,
                {1: inv, 2: 44.9, 3: 47.2, 4: 52.3},  # type: ignore[arg-type]
                group_sizes,
                group_probs,
            )

    # 文字列キー
    str_keys = {"1": 27.6, "2": 44.9, "3": 47.2, "4": 52.3}
    res_str = lunch_stay_by_group_size(40, str_keys, group_sizes, group_probs)
    res_base = lunch_stay_by_group_size(
        40, {1: 27.6, 2: 44.9, 3: 47.2, 4: 52.3}, group_sizes, group_probs
    )
    assert res_str == res_base

    # 5 人の比が余分にあっても無視される
    extra_ratio = {1: 27.6, 2: 44.9, 3: 47.2, 4: 52.3, 5: 99.9}
    res_extra = lunch_stay_by_group_size(
        40, extra_ratio, group_sizes, group_probs
    )
    assert res_extra == res_base


def test_case_4_identical_ratios_matches_simulate_without_ratio() -> None:
    """Case 4: 比がすべて同じなら simulate の結果が完全に一致."""
    tables = [Table(f"t4_{i}", 4, "1f") for i in range(15)] + [
        Table(f"t2_{i}", 2, "1f") for i in range(2)
    ]
    arrival_rate = arrival_profile(45)
    config = SimulationConfig(
        close_minute=150,
        last_entry_minute=120,
        max_wait_minutes=10,
        cleanup_minutes=5,
    )

    res_without = simulate(
        tables,
        config,
        arrival_rate=arrival_rate,
        seed=7,
        stay_minutes=40,
        stay_spread=5,
    )
    res_with = simulate(
        tables,
        config,
        arrival_rate=arrival_rate,
        seed=7,
        stay_minutes=40,
        stay_spread=5,
        stay_by_size={1: 40.0, 2: 40.0, 3: 40.0, 4: 40.0},
    )

    assert res_without.entered_guests == res_with.entered_guests
    assert res_without.lost_guests == res_with.lost_guests
    assert res_without.lost_by_reason == res_with.lost_by_reason
    assert res_without.seating_events == res_with.seating_events
    assert res_without.occupied_people == res_with.occupied_people


def test_case_5_stay_varies_by_size_arrivals_sequence_unchanged() -> None:
    """Case 5: 人数ごとに滞在が変わり、組の並びは変わらない."""
    rng1 = np.random.default_rng(3)
    rng2 = np.random.default_rng(3)
    arrival_rate = [4000.0]
    group_sizes = [1, 2, 3, 4]
    group_probs = [0.25, 0.5, 0.15, 0.1]
    stay_minutes = 40
    stay_spread = 5
    stay_by_size = {1: 26.5, 2: 43.111, 3: 45.319, 4: 50.216}

    arr_without = _sample_arrivals(
        rng1, arrival_rate, group_sizes, group_probs, stay_minutes, stay_spread
    )
    arr_with = _sample_arrivals(
        rng2,
        arrival_rate,
        group_sizes,
        group_probs,
        stay_minutes,
        stay_spread,
        stay_by_size=stay_by_size,
    )

    assert len(arr_without) == len(arr_with)
    assert all(
        a1.minute == a2.minute and a1.size == a2.size
        for a1, a2 in zip(arr_without, arr_with, strict=True)
    )

    for s in [1, 2, 3, 4]:
        stays_s = [a.stay_minutes for a in arr_with if a.size == s]
        mean_s = sum(stays_s) / len(stays_s)
        assert abs(mean_s - stay_by_size[s]) <= 1.0

    std_1 = float(np.std([a.stay_minutes for a in arr_with if a.size == 1]))
    std_4 = float(np.std([a.stay_minutes for a in arr_with if a.size == 4]))
    assert std_1 < std_4


def test_case_7_no_ratio_matches_identical_ratios_in_simulate_lunch_day() -> None:
    """Case 7: 比が無いときはいまの計算のまま."""
    tables = [Table(f"t4_{i}", 4, "1f") for i in range(15)] + [
        Table(f"t2_{i}", 2, "1f") for i in range(2)
    ]
    floors = [Floor("1f", "always")]
    group_sizes = [1, 2, 3, 4]
    group_probs = [0.25, 0.5, 0.15, 0.1]

    res_none = simulate_lunch_day(
        tables=tables,
        floors=floors,
        total_groups=45.0,
        group_sizes=group_sizes,
        group_probs=group_probs,
        stay_minutes=40,
        cleanup_minutes=5,
        max_wait_minutes=10,
        replications=3,
        seed=42,
        stay_ratio_by_group_size=None,
    )
    res_equal = simulate_lunch_day(
        tables=tables,
        floors=floors,
        total_groups=45.0,
        group_sizes=group_sizes,
        group_probs=group_probs,
        stay_minutes=40,
        cleanup_minutes=5,
        max_wait_minutes=10,
        replications=3,
        seed=42,
        stay_ratio_by_group_size={1: 1, 2: 1, 3: 1, 4: 1},
    )

    assert res_none.guests == res_equal.guests
    assert res_none.lost == res_equal.lost
    assert res_none.occupied_people == res_equal.occupied_people


def test_case_9_fit_grid_identical_when_ratios_are_equal() -> None:
    """Case 9: 観察との照合も、人数で変えない設定ならいまと同じ."""
    floor1 = [
        Table("1f-1", 4, "1f"),
        Table("1f-2", 4, "1f"),
        Table("1f-3", 4, "1f"),
        Table("1f-4", 4, "1f"),
        Table("1f-5", 4, "1f"),
        Table("1f-6", 2, "1f"),
    ]
    candidates = {"f2_1": [Table("2f-1", 4, "2f")]}
    group_levels = {"low": 30.0, "base": 45.0}
    stay_levels = {"low": 30, "base": 40}
    group_sizes = [1, 2, 3, 4]
    group_probs = [0.25, 0.5, 0.15, 0.1]

    rows_none = fit_grid(
        floor1_tables=floor1,
        candidates=candidates,
        group_levels=group_levels,
        stay_levels=stay_levels,
        group_sizes=group_sizes,
        group_probs=group_probs,
        cleanup_minutes=5,
        max_wait_minutes=10,
        replications=3,
        seed=42,
        stay_ratio_by_group_size=None,
    )
    rows_equal = fit_grid(
        floor1_tables=floor1,
        candidates=candidates,
        group_levels=group_levels,
        stay_levels=stay_levels,
        group_sizes=group_sizes,
        group_probs=group_probs,
        cleanup_minutes=5,
        max_wait_minutes=10,
        replications=3,
        seed=42,
        stay_ratio_by_group_size={1: 1, 2: 1, 3: 1, 4: 1},
    )

    assert len(rows_none) == len(rows_equal)
    for r1, r2 in zip(rows_none, rows_equal, strict=True):
        assert r1.candidate == r2.candidate
        assert r1.reproduction_rate == r2.reproduction_rate
        assert r1.failed_conditions == r2.failed_conditions


def test_unnormalized_group_probs() -> None:
    """追加テスト: 分布の確率の合計が 1 でない場合も正しく正規化されることの確認."""
    stay_minutes = 40
    ratios = {1: 27.6, 2: 44.9, 3: 47.2, 4: 52.3}
    group_sizes = [1, 2, 3, 4]
    # Sum is 2.0 instead of 1.0
    group_probs = [0.5, 1.0, 0.3, 0.2]

    res = lunch_stay_by_group_size(
        stay_minutes, ratios, group_sizes, group_probs
    )
    res_base = lunch_stay_by_group_size(
        stay_minutes, ratios, group_sizes, [0.25, 0.5, 0.15, 0.1]
    )
    assert res == res_base


def test_stay_minimum_one_minute() -> None:
    """追加テスト: 平均滞在が小さくても 1 分以上になること (max(1, ...) の確認)."""
    rng = np.random.default_rng(42)
    # Average stay is 0.1 minute with normal noise
    arr = _sample_arrivals(
        rng,
        arrival_rate=[10.0],
        group_sizes=[1],
        group_probs=[1.0],
        stay_minutes=1,
        stay_spread=1,
        stay_by_size={1: 0.1},
    )
    assert len(arr) > 0
    assert all(a.stay_minutes >= 1 for a in arr)


def test_case_6_evaluation_lunch_stay_ratio() -> None:
    """Case 6: 評価で、人数で変えない設定はいまの値と同じ、標準は座れる人数が増える."""
    data_dir = Path(__file__).resolve().parents[1] / "data"

    req_override = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=5,
        overrides={
            "layout.floor2": "f2_42",
            "lunch_stay_ratio_by_group_size": {1: 1, 2: 1, 3: 1, 4: 1},
            "lunch_arrival_weekday_factor": {
                "mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1
            },
        },
    )
    res_override = evaluate(data_dir, req_override)[0]
    assert res_override.lunch_guests_by_weekday == {
        "mon": 73,
        "tue": 75,
        "wed": 75,
        "thu": 75,
        "fri": 73,
    }
    assert round(res_override.lost_lunch_guests, 2) == 24.97

    req_standard = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=5,
        overrides={
            "layout.floor2": "f2_42",
            "lunch_arrival_weekday_factor": {
                "mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1
            },
        },
    )
    res_standard = evaluate(data_dir, req_standard)[0]
    assert sum(res_standard.lunch_guests_by_weekday.values()) > sum(
        res_override.lunch_guests_by_weekday.values()
    )


def test_case_8_input_validation(tmp_path: Path) -> None:
    """Case 8: 読み込み時の検査."""
    def _create_store_with_ratio(ratio_data: dict[str, object]) -> Path:
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
                {"lunch_stay_ratio_by_group_size": ratio_data},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return store_dir

    # 1. value を {1: 0, 2: 1, 3: 1, 4: 1} にしたもの
    bad_val_store = _create_store_with_ratio({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": {1: 0, 2: 1, 3: 1, 4: 1},
        "range": {
            "low": {1: 1, 2: 1, 3: 1, 4: 1},
            "base": {1: 1, 2: 1, 3: 1, 4: 1},
            "high": {1: 1, 2: 1, 3: 1, 4: 1},
        },
    })
    issues_bad_val = validate_store(bad_val_store)
    assert any(
        i.path == "lunch_stay_ratio_by_group_size"
        for i in issues_bad_val
    )

    # 2. range.low に -1 を入れたもの
    bad_range_store = _create_store_with_ratio({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": {1: 1, 2: 1, 3: 1, 4: 1},
        "range": {
            "low": -1,
            "base": {1: 1, 2: 1, 3: 1, 4: 1},
            "high": {1: 1, 2: 1, 3: 1, 4: 1},
        },
    })
    issues_bad_range = validate_store(bad_range_store)
    assert any(
        i.path == "lunch_stay_ratio_by_group_size.range.low"
        for i in issues_bad_range
    )

    # 3. value を対応表でなく数値 1 にしたもの
    bad_scalar_store = _create_store_with_ratio({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": 1,
        "range": {
            "low": {1: 1, 2: 1, 3: 1, 4: 1},
            "base": {1: 1, 2: 1, 3: 1, 4: 1},
            "high": {1: 1, 2: 1, 3: 1, 4: 1},
        },
    })
    issues_bad_scalar = validate_store(bad_scalar_store)
    assert any(
        i.path == "lunch_stay_ratio_by_group_size"
        for i in issues_bad_scalar
    )

    # 4. 正しい値のもの
    good_store = _create_store_with_ratio({
        "unit": "ratio",
        "status": "ASSUMPTION",
        "justification": "テスト用",
        "how_to_verify": "テスト用",
        "value": {1: 27.6, 2: 44.9, 3: 47.2, 4: 52.3},
        "range": {
            "low": {1: 1, 2: 1, 3: 1, 4: 1},
            "base": {1: 27.6, 2: 44.9, 3: 47.2, 4: 52.3},
            "high": {1: 27.6, 2: 44.9, 3: 47.2, 4: 52.3},
        },
    })
    issues_good = validate_store(good_store)
    assert not any(
        "lunch_stay_ratio_by_group_size" in i.path
        for i in issues_good
    )

