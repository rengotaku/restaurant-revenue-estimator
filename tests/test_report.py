"""レポート (TC-13-xx)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from restaurant_revenue_estimator.cli import app
from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.report import (
    render_run_markdown,
    result_to_dict,
)
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    evaluate,
    evaluate_run,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STORE = DATA / "stores" / "birdman_tamachi"
runner = CliRunner()

REQUEST = EvaluationRequest(store_id="birdman_tamachi", replications=1)


def _report() -> str:
    document = evaluate_run(DATA, REQUEST)
    return render_run_markdown(
        document,
        load_store(STORE),
        benchmark={"definition_note": "税込", "metrics": {}},
    )


def test_first_section_is_scenario_note_with_unresolved() -> None:
    text = _report()
    head = text.split("## 2.")[0]
    assert "シナリオであって実績でも予測区間でもない" in head
    assert "UNRESOLVED" not in head
    assert "floor2_seats" in head


def test_report_has_no_forbidden_words() -> None:
    text = _report()
    for word in ("信頼度", "精度", "信頼区間", "±"):
        assert word not in text


def test_sales_rows_have_status() -> None:
    text = _report()
    assert "ステータス: 売上は DERIVED" in text


def test_evidence_lists_every_input_key() -> None:
    text = _report()
    inputs = load_store(STORE)
    for entry in inputs.values:
        assert f"`{entry.path}`" in text


def test_related_store_note() -> None:
    assert "バードマン TRIO 田町店" in _report()
    assert "含めていない" in _report()


def test_run_output_is_byte_identical(tmp_path: Path) -> None:
    args = [
        "run",
        "data/stores/birdman_tamachi",
        "--replications",
        "1",
        "--runs-dir",
        str(tmp_path),
        "--data-dir",
        str(DATA),
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    run_dir = Path(first.output.splitlines()[0])
    result_first = (run_dir / "result.json").read_bytes()
    report_first = (run_dir / "report.md").read_bytes()
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert (run_dir / "result.json").read_bytes() == result_first
    assert (run_dir / "report.md").read_bytes() == report_first
    run_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert "created_at" in run_meta


def test_month_sum_equals_year() -> None:
    result = evaluate(DATA, REQUEST)[0]
    assert (
        sum(month.total_inc_tax for month in result.revenue.months)
        == result.revenue.total_inc_tax
    )
    payload = result_to_dict(result)
    assert (
        sum(month["total_inc_tax"] for month in payload["revenue"]["months"])
        == payload["revenue"]["total_inc_tax"]
    )
