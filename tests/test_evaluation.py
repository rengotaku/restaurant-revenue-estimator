"""評価の再現性・感度・シナリオ (TC-12-xx)."""

from pathlib import Path

import pytest

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
