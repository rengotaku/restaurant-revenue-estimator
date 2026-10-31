"""評価の再現性・感度・シナリオ (TC-12-xx)."""

import shutil
from pathlib import Path

import pytest
import yaml

from restaurant_revenue_estimator.engine.scenario import (
    compare_assumption_sets,
    sensitivity,
)
from restaurant_revenue_estimator.report import result_to_dict
from restaurant_revenue_estimator.services.evaluation import EvaluationRequest, evaluate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _request(**overrides: object) -> EvaluationRequest:
    base = EvaluationRequest(store_id="birdman_tamachi", replications=2)
    return EvaluationRequest(
        store_id=str(overrides.get("store_id", base.store_id)),
        assumption_set=str(overrides.get("assumption_set", base.assumption_set)),
        year=int(overrides.get("year", base.year)),
        calendar_mode=str(overrides.get("calendar_mode", base.calendar_mode)),
        seed=int(overrides.get("seed", base.seed)),
        replications=int(overrides.get("replications", base.replications)),
        overrides=overrides.get("overrides", base.overrides),  # type: ignore[arg-type]
    )


def test_same_input_and_seed_give_identical_result() -> None:
    first = evaluate(DATA, _request())[0]
    second = evaluate(DATA, _request())[0]
    assert first == second


def test_override_changes_input_hash() -> None:
    base = evaluate(DATA, _request())[0]
    changed = evaluate(DATA, _request(overrides={"lunch_avg_ticket": 1200}))[0]
    assert base.meta.input_hash != changed.meta.input_hash


def test_three_floor_candidates_are_all_returned() -> None:
    results = evaluate(DATA, _request())
    assert {result.variant for result in results} == {"f2_42", "f2_12", "f2_22"}
    assert len(results) == 3


def test_lunch_ticket_monotonicity() -> None:
    low = evaluate(DATA, _request(overrides={"lunch_avg_ticket": 1100}))[0]
    high = evaluate(DATA, _request(overrides={"lunch_avg_ticket": 1200}))[0]
    assert high.gross_inc_tax > low.gross_inc_tax


def test_cost_rate_reduces_gross_profit() -> None:
    low = evaluate(
        DATA, _request(overrides={"cost_rate_ala": 0.28, "cost_rate_course": 0.28})
    )[0]
    high = evaluate(
        DATA, _request(overrides={"cost_rate_ala": 0.36, "cost_rate_course": 0.36})
    )[0]
    assert high.pnl.gross_profit < low.pnl.gross_profit


def test_scenarios_are_ordered() -> None:
    scenarios = compare_assumption_sets(DATA, _request())
    values = {scenario.name: scenario.gross_inc_tax for scenario in scenarios}
    assert values["pessimistic"] <= values["standard"] <= values["optimistic"]


def test_sensitivity_is_sorted_and_has_how_to_verify() -> None:
    rows = sensitivity(DATA, _request(replications=1))
    magnitudes = [row.magnitude for row in rows]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert all(row.how_to_verify for row in rows)
    assert any(row.key == "layout.floor2" for row in rows)


def test_output_has_no_confidence_or_interval_fields() -> None:
    payload = result_to_dict(evaluate(DATA, _request())[0])

    def keys(node: object) -> list[str]:
        if isinstance(node, dict):
            result = list(node)
            for value in node.values():
                result.extend(keys(value))
            return result
        if isinstance(node, list):
            result = []
            for item in node:
                result.extend(keys(item))
            return result
        return []

    names = keys(payload)
    for forbidden in ("confidence", "accuracy", "p10", "p90"):
        assert not any(forbidden in name for name in names)


@pytest.mark.parametrize("store_id", ["birdman_tamachi", "torahachi_akihabara"])
def test_both_stores_evaluate(store_id: str) -> None:
    results = evaluate(DATA, _request(store_id=store_id, replications=1))
    assert results
    assert results[0].gross_inc_tax > 0


def test_tc_12_02(tmp_path: Path) -> None:
    tmp_data = tmp_path / "data"
    shutil.copytree(DATA, tmp_data)
    standard_yaml = (
        tmp_data / "stores" / "birdman_tamachi" / "assumptions" / "standard.yaml"
    )
    content = yaml.safe_load(standard_yaml.read_text(encoding="utf-8"))
    content["unused_probe"] = {
        "status": "ASSUMPTION",
        "value": 1,
        "range": {"low": 0, "base": 1, "high": 2},
        "justification": "テスト用",
        "how_to_verify": "テスト用確認手順",
    }
    standard_yaml.write_text(
        yaml.safe_dump(content, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    rows = sensitivity(
        tmp_data, EvaluationRequest(store_id="birdman_tamachi", replications=20)
    )

    unused_rows = [row for row in rows if row.key == "unused_probe"]
    assert len(unused_rows) == 2
    for row in unused_rows:
        assert row.delta_revenue == 0
        assert row.delta_income == 0

    unused_indices = [i for i, row in enumerate(rows) if row.key == "unused_probe"]
    nonzero_indices = [i for i, row in enumerate(rows) if row.magnitude != 0]
    min_unused_idx = min(unused_indices)
    assert all(i < min_unused_idx for i in nonzero_indices)


def test_tc_12_03(tmp_path: Path) -> None:
    tmp_data = tmp_path / "data"
    shutil.copytree(DATA, tmp_data)
    standard_yaml = (
        tmp_data / "stores" / "birdman_tamachi" / "assumptions" / "standard.yaml"
    )
    content = yaml.safe_load(standard_yaml.read_text(encoding="utf-8"))

    dinner = content["dinner_demand_by_weekday"]
    base_dinner = (
        dinner["range"]["base"]
        if "range" in dinner and "base" in dinner["range"]
        else dinner["value"]
    )
    if "range" not in dinner:
        dinner["range"] = {}
    dinner["range"]["low"] = {k: round(v * 0.7) for k, v in base_dinner.items()}
    dinner["range"]["high"] = {k: round(v * 1.3) for k, v in base_dinner.items()}

    lunch = content["lunch_avg_ticket"]
    if "range" not in lunch:
        lunch["range"] = {}
    lunch["range"]["low"] = 1100
    lunch["range"]["high"] = 1160

    standard_yaml.write_text(
        yaml.safe_dump(content, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    rows = sensitivity(
        tmp_data, EvaluationRequest(store_id="birdman_tamachi", replications=5)
    )

    dinner_ranks = [
        i for i, row in enumerate(rows) if row.key == "dinner_demand_by_weekday"
    ]
    lunch_ranks = [
        i for i, row in enumerate(rows) if row.key == "lunch_avg_ticket"
    ]
    assert min(dinner_ranks) < min(lunch_ranks)


def test_tc_f3_01() -> None:
    res20 = sensitivity(
        DATA, EvaluationRequest(store_id="birdman_tamachi", replications=20)
    )
    res5 = sensitivity(
        DATA, EvaluationRequest(store_id="birdman_tamachi", replications=5)
    )
    assert res20 == res5

