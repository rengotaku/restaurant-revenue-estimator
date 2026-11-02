"""Tests for report sensitivity stubbing (Issue #53)."""

from pathlib import Path

from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    evaluate,
)
from restaurant_revenue_estimator.services.reporting import build_report_markdown
from tests.report_stub import REAL_REPORT_SENSITIVITY_TESTS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def test_tc_53_01_report_sensitivity_is_empty() -> None:
    """TC-53-01: 対象外のテストでは、レポートの感度分析が空になる."""
    assert (
        "test_tc_53_01_report_sensitivity_is_empty" not in REAL_REPORT_SENSITIVITY_TESTS
    )
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides={"layout.floor2": "f2_42"},
    )
    result = evaluate(DATA, req)[0]
    report = build_report_markdown(DATA, result, req)
    sensitivity_section = report.split("## 7. 感度と調査の優先順位")[1].split("## 8.")[
        0
    ]
    assert "- 感度分析を実行していない。" in report
    assert "| `dinner_demand_by_weekday` |" not in sensitivity_section


def test_tc_53_02_report_has_real_sensitivity_rows() -> None:
    """TC-53-02: 一覧に入れたテストでは、レポートに本物の感度分析の行が載る."""
    assert (
        "test_tc_53_02_report_has_real_sensitivity_rows"
        in REAL_REPORT_SENSITIVITY_TESTS
    )
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        seed=42,
        replications=1,
        overrides={"layout.floor2": "f2_42"},
    )
    result = evaluate(DATA, req)[0]
    report = build_report_markdown(DATA, result, req)
    assert "## 7. 感度と調査の優先順位" in report
    assert "| `dinner_demand_by_weekday` |" in report
    assert "- 感度分析を実行していない。" not in report


def test_tc_53_03_real_sensitivity_tests_exist() -> None:
    """TC-53-03: 一覧のテスト名が実在する."""
    tests_dir = Path(__file__).parent
    test_files = list(tests_dir.glob("test_*.py"))
    assert test_files, "No test files found"
    all_content = "\n".join(f.read_text(encoding="utf-8") for f in test_files)
    for name in REAL_REPORT_SENSITIVITY_TESTS:
        assert f"def {name}(" in all_content
