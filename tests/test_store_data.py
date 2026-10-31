"""事前設計テスト: 実データ検査 (TC-03-xx) および仮定の整合性 (TC-F2-xx)."""

import functools
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from restaurant_revenue_estimator.cli import app
from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.services.evaluation import EvaluationRequest, evaluate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STORES = ("birdman_tamachi", "torahachi_akihabara")


def _load_assumption_keys(store_id: str) -> list[str]:
    path = DATA / "stores" / store_id / "assumptions" / "standard.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return sorted(data.keys())


BIRDMAN_KEYS = _load_assumption_keys("birdman_tamachi")
TORAHACHI_KEYS = _load_assumption_keys("torahachi_akihabara")

runner = CliRunner()


def test_tc_03_01_validate_all() -> None:
    """TC-03-01: `rre validate --all` の終了コードが 0."""
    result = runner.invoke(app, ["validate", "--all"])
    assert result.exit_code == 0


def test_tc_03_02_birdman_floor1() -> None:
    (
        "TC-03-02: バードマンの 1 階を読む -> "
        "卓の席数が [4, 4, 4, 4, 4, 2]、ステータス OBSERVED."
    )
    inputs = load_store(DATA / "stores" / "birdman_tamachi")
    floor1 = inputs.files["layout.yaml"]["floors"][0]
    tables = floor1["tables"]
    seats = [t["seats"] for t in tables["value"]]
    assert seats == [4, 4, 4, 4, 4, 2]
    assert tables["status"] == "OBSERVED"


def test_tc_03_03_birdman_floor2() -> None:
    (
        "TC-03-03: バードマンの 2 階を読む -> "
        "value が null、ステータス UNRESOLVED、候補が f2_42・f2_12・f2_22."
    )
    inputs = load_store(DATA / "stores" / "birdman_tamachi")
    floor2 = inputs.files["layout.yaml"]["floors"][1]
    tables = floor2["tables"]
    assert tables["value"] is None
    assert tables["status"] == "UNRESOLVED"
    assert set(tables["candidates"].keys()) == {"f2_42", "f2_12", "f2_22"}


def test_tc_03_04_birdman_courses() -> None:
    """TC-03-04: バードマンのコースを読む -> 通常価格と割引価格が別フィールドで一致."""
    inputs = load_store(DATA / "stores" / "birdman_tamachi")
    courses = inputs.files["menu.yaml"]["courses"]

    assert courses["sumibi"]["name"] == "炭火焼鳥会席"
    assert courses["sumibi"]["normal_price"]["value"] == 6500
    assert courses["sumibi"]["discounted_price"]["value"] == 5500

    assert courses["oyama"]["name"] == "大山どり満喫"
    assert courses["oyama"]["normal_price"]["value"] == 7000
    assert courses["oyama"]["discounted_price"]["value"] == 6000

    assert courses["niku"]["name"] == "肉三昧"
    assert courses["niku"]["normal_price"]["value"] == 8000
    assert courses["niku"]["discounted_price"]["value"] == 7000


def test_tc_03_05_appetizer() -> None:
    """TC-03-05: お通しを読む -> バードマン 450 円（コース客は免除）、とら八 0 円."""
    birdman = load_store(DATA / "stores" / "birdman_tamachi")
    torahachi = load_store(DATA / "stores" / "torahachi_akihabara")

    b_app = birdman.files["menu.yaml"]["appetizer"]
    t_app = torahachi.files["menu.yaml"]["appetizer"]

    assert b_app["price"]["value"] == 450
    assert b_app["waived_for_course"]["value"] is True
    assert t_app["price"]["value"] == 0


def test_tc_03_06_torahachi_recruiting_wage() -> None:
    """TC-03-06: とら八の store.yaml の recruiting_wages を読む."""
    inputs = load_store(DATA / "stores" / "torahachi_akihabara")
    store_file = inputs.files["store.yaml"]
    assert "recruiting_wages" in store_file, (
        "recruiting_wages が store.yaml にありません"
    )
    wages = store_file["recruiting_wages"]
    assert wages["status"] == "PUBLIC_REPORTED"
    assert wages["value"] == {
        "full_time_monthly_from": 317000,
        "part_time_hourly_from": 1330,
    }
    assert sorted(wages["sources"]) == ["S07", "S08"]


def test_tc_03_07_birdman_trio_source_not_used() -> None:
    (
        "TC-03-07: バードマン TRIO の食べログ ID（13119788）を含む出典 -> "
        "どの値の根拠にも使われていない."
    )
    inputs = load_store(DATA / "stores" / "birdman_tamachi")
    trio_sources = {
        s_id
        for s_id, s in inputs.sources.items()
        if "13119788" in s.url or any("13119788" in str(fact) for fact in s.facts)
    }
    assert trio_sources, "TRIO の出典が見つかりません"
    used_sources = {src for entry in inputs.values for src in entry.value.sources}
    assert not (trio_sources & used_sources)


@pytest.mark.parametrize(
    "store_id,key",
    [(s, k) for s in STORES for k in _load_assumption_keys(s)],
)
def test_tc_03_08_assumptions_match_range(store_id: str, key: str) -> None:
    (
        "TC-03-08: 仮定ファイル 3 つを読む -> "
        "各キーの値が low/base/high のどれかと一致. "
        "標準は base と一致."
    )
    inputs = load_store(DATA / "stores" / store_id)
    std = inputs.files["assumptions/standard.yaml"][key]
    pess = inputs.files["assumptions/pessimistic.yaml"][key]
    opt = inputs.files["assumptions/optimistic.yaml"][key]

    rng = std["range"]
    low = rng["low"]
    base = rng["base"]
    high = rng["high"]

    assert std["value"] == base, f"{store_id}.{key}: 標準の値が base と一致しない"
    assert pess["value"] in (low, base, high), (
        f"{store_id}.{key}: 悲観の値が low/base/high のどれとも一致しない"
    )
    assert opt["value"] in (low, base, high), (
        f"{store_id}.{key}: 楽観の値が low/base/high のどれとも一致しない"
    )


def _make_eval_request(
    store_id: str,
    assumption_set: str = "standard",
    overrides: dict[str, Any] | None = None,
) -> EvaluationRequest:
    base_overrides: dict[str, Any] = (
        {"layout.floor2": "f2_42"} if store_id == "birdman_tamachi" else {}
    )
    if overrides:
        base_overrides = {**base_overrides, **overrides}
    return EvaluationRequest(
        store_id=store_id,
        assumption_set=assumption_set,
        year=2026,
        calendar_mode="steady",
        seed=42,
        replications=20,
        overrides=base_overrides,
    )


@functools.cache
def _standard_evaluation_income(store_id: str) -> int:
    res = evaluate(DATA, _make_eval_request(store_id))[0]
    return res.pnl.yen(res.pnl.business_income)


@pytest.mark.parametrize(
    "store_id,key",
    [(s, k) for s in STORES for k in _load_assumption_keys(s)],
)
def test_tc_f2_01_pessimistic_not_greater_than_standard(
    store_id: str, key: str
) -> None:
    (
        "TC-F2-01: 標準＋{キー: 悲観ファイルの値} の上書きで評価 -> "
        "事業所得相当 ≤ 標準の事業所得相当."
    )
    inputs = load_store(DATA / "stores" / store_id)
    pess_val = inputs.files["assumptions/pessimistic.yaml"][key]["value"]

    std_income = _standard_evaluation_income(store_id)

    pess_res = evaluate(
        DATA, _make_eval_request(store_id, overrides={key: pess_val})
    )[0]
    pess_income = pess_res.pnl.yen(pess_res.pnl.business_income)

    assert pess_income <= std_income, (
        f"{store_id}.{key}: "
        f"悲観上書きの事業所得相当 ({pess_income}) > 標準 ({std_income})"
    )


@pytest.mark.parametrize(
    "store_id,key",
    [(s, k) for s in STORES for k in _load_assumption_keys(s)],
)
def test_tc_f2_02_optimistic_not_less_than_standard(
    store_id: str, key: str
) -> None:
    (
        "TC-F2-02: 標準＋{キー: 楽観ファイルの値} の上書きで評価 -> "
        "事業所得相当 ≥ 標準の事業所得相当."
    )
    inputs = load_store(DATA / "stores" / store_id)
    opt_val = inputs.files["assumptions/optimistic.yaml"][key]["value"]

    std_income = _standard_evaluation_income(store_id)

    opt_res = evaluate(
        DATA, _make_eval_request(store_id, overrides={key: opt_val})
    )[0]
    opt_income = opt_res.pnl.yen(opt_res.pnl.business_income)

    assert opt_income >= std_income, (
        f"{store_id}.{key}: "
        f"楽観上書きの事業所得相当 ({opt_income}) < 標準 ({std_income})"
    )


@pytest.mark.parametrize("store_id", STORES)
def test_tc_f2_03_monotonicity_across_variants(store_id: str) -> None:
    (
        "TC-F2-03: 2 店舗・2 階の全候補について、悲観・標準・楽観で評価 -> "
        "事業所得相当も年間税込売上も 悲観 ≤ 標準 ≤ 楽観."
    )
    req_kwargs: dict[str, Any] = {
        "store_id": store_id,
        "year": 2026,
        "calendar_mode": "steady",
        "seed": 42,
        "replications": 20,
    }
    pess_map = {
        r.variant: r
        for r in evaluate(
            DATA, EvaluationRequest(assumption_set="pessimistic", **req_kwargs)
        )
    }
    std_map = {
        r.variant: r
        for r in evaluate(
            DATA, EvaluationRequest(assumption_set="standard", **req_kwargs)
        )
    }
    opt_map = {
        r.variant: r
        for r in evaluate(
            DATA, EvaluationRequest(assumption_set="optimistic", **req_kwargs)
        )
    }

    assert set(pess_map.keys()) == set(std_map.keys()) == set(opt_map.keys())

    for variant, p_res in pess_map.items():
        s_res = std_map[variant]
        o_res = opt_map[variant]

        p_income = p_res.pnl.yen(p_res.pnl.business_income)
        s_income = s_res.pnl.yen(s_res.pnl.business_income)
        o_income = o_res.pnl.yen(o_res.pnl.business_income)

        p_rev = p_res.revenue.total_inc_tax
        s_rev = s_res.revenue.total_inc_tax
        o_rev = o_res.revenue.total_inc_tax

        assert p_income <= s_income <= o_income, (
            f"{store_id}[{variant}]: 事業所得相当の単調性が崩れている: "
            f"悲観={p_income}, 標準={s_income}, 楽観={o_income}"
        )
        assert p_rev <= s_rev <= o_rev, (
            f"{store_id}[{variant}]: 年間税込売上の単調性が崩れている: "
            f"悲観={p_rev}, 標準={s_rev}, 楽観={o_rev}"
        )
