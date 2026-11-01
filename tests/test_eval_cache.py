"""Tests for evaluation caching (Issue #42)."""

import shutil
from pathlib import Path

import pytest
import yaml

from restaurant_revenue_estimator.services.evaluation import (
    EvaluationError,
    EvaluationRequest,
    evaluate,
)
from tests.eval_cache import EXCLUDED_TESTS, CachedEvaluate


def test_tc_42_01_same_condition_cached() -> None:
    """TC-42-01: 同じ条件なら 2 回目は計算しない.

    前提: 元の evaluate を包んだ新しい CachedEvaluate を作る
    （fixture の差し替えとは別に、テストの中で作る）
    手順: バードマン田町店、標準、seed 42、replications 1、
    overrides={"layout.floor2": "f2_42"} の同じ EvaluationRequest で 2 回呼ぶ
    期待: real_calls == 1 。2 回の戻り値は等しい（ == ）が、
    同じオブジェクトではない（ is not ）
    """
    cached = CachedEvaluate(evaluate)
    data_dir = Path("data")
    request = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides={"layout.floor2": "f2_42"},
    )
    res1 = cached(data_dir, request)
    res2 = cached(data_dir, request)

    assert cached.real_calls == 1
    assert res1 == res2
    assert res1 is not res2


def test_tc_42_02_different_condition_computes_separately() -> None:
    """TC-42-02: 条件が違えば別に計算する.

    手順: TC-42-01 と同じ条件のあと、 overrides だけを
    {"layout.floor2": "f2_12"} に変えて呼ぶ
    期待: real_calls == 2
    """
    cached = CachedEvaluate(evaluate)
    data_dir = Path("data")
    req1 = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides={"layout.floor2": "f2_42"},
    )
    cached(data_dir, req1)
    assert cached.real_calls == 1

    req2 = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides={"layout.floor2": "f2_12"},
    )
    cached(data_dir, req2)
    assert cached.real_calls == 2


def test_tc_42_03_data_change_computes_separately(tmp_path: Path) -> None:
    """TC-42-03: データの中身が変われば別に計算する.

    前提: data/ を tmp_path にコピーする
    手順: コピーで 1 回呼ぶ → コピーの
    stores/birdman_tamachi/assumptions/standard.yaml の lunch_avg_ticket の
    value を変える → 同じ条件でもう 1 回呼ぶ
    期待: real_calls == 2
    制約: 書き換えるのは tmp_path の下のコピーだけ。元の data/ は触らない
    """
    tmp_data = tmp_path / "data"
    shutil.copytree("data", tmp_data)

    cached = CachedEvaluate(evaluate)
    request = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides={"layout.floor2": "f2_42"},
    )

    # コピーで 1 回呼ぶ
    cached(tmp_data, request)
    assert cached.real_calls == 1

    # コピーの stores/birdman_tamachi/assumptions/standard.yaml の
    # lunch_avg_ticket の value を変える
    yaml_path = (
        tmp_data / "stores" / "birdman_tamachi" / "assumptions" / "standard.yaml"
    )
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    doc["lunch_avg_ticket"]["value"] = int(doc["lunch_avg_ticket"]["value"]) + 100
    yaml_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    # 同じ条件でもう 1 回呼ぶ
    cached(tmp_data, request)
    assert cached.real_calls == 2


def test_tc_42_04_excluded_tests_exist() -> None:
    """TC-42-04: 対象外のテスト名が実在する.

    期待: EXCLUDED_TESTS の 2 つの名前が、それぞれ
    tests/test_web.py に def <名前>( として実在する
    検知するバグ: テストの名前が変わって、対象外の指定が黙って効かなくなる
    """
    test_web_path = Path(__file__).parent / "test_web.py"
    content = test_web_path.read_text(encoding="utf-8")
    for name in EXCLUDED_TESTS:
        assert f"def {name}(" in content


def test_tc_42_05_none_overrides_cached() -> None:
    """TC-42-05 (追加): overrides が None の場合もキャッシュされる."""
    cached = CachedEvaluate(evaluate)
    data_dir = Path("data")
    request = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides=None,
    )
    res1 = cached(data_dir, request)
    res2 = cached(data_dir, request)

    assert cached.real_calls == 1
    assert res1 == res2
    assert res1 is not res2


def test_tc_42_06_nonexistent_store_raises_evaluation_error() -> None:
    """TC-42-06 (追加): 存在しない店舗は evaluate にそのまま渡され、
    EvaluationError になる.
    """
    cached = CachedEvaluate(evaluate)
    data_dir = Path("data")
    request = EvaluationRequest(
        store_id="nonexistent_store",
        assumption_set="standard",
    )
    with pytest.raises(EvaluationError):
        cached(data_dir, request)


def test_tc_42_07_cached_result_isolated_from_mutated_request_overrides() -> None:
    """TC-42-07 (追加): 保存時も deepcopy され overrides の変更から隔離される."""
    cached = CachedEvaluate(evaluate)
    data_dir = Path("data")
    original_tables = [{"id": "t1", "seats": 4}]
    overrides = {"layout.floor2": original_tables}
    req1 = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides=overrides,
    )
    cached(data_dir, req1)
    assert cached.real_calls == 1

    # request の overrides の入れ子を書き換える
    original_tables[0]["seats"] = 8

    # 書き換える前と同じ中身の新しい request で呼ぶ
    req2 = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides={"layout.floor2": [{"id": "t1", "seats": 4}]},
    )
    res2 = cached(data_dir, req2)

    assert cached.real_calls == 1
    assert res2[0].meta.overrides == {"layout.floor2": [{"id": "t1", "seats": 4}]}
