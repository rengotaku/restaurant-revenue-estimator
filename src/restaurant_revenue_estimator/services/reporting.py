"""レポート生成の共有経路 (CLI と Web で同じ内容にする)."""

from pathlib import Path

from restaurant_revenue_estimator.engine.scenario import sensitivity
from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.inputs.legacy import check_legacy, format_legacy_check
from restaurant_revenue_estimator.report import load_benchmark, render_markdown
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    EvaluationResult,
    evaluate,
)


def build_report_markdown(
    data_dir: Path,
    result: EvaluationResult,
    request: EvaluationRequest,
) -> str:
    store_dir = data_dir / "stores" / request.store_id
    inputs = load_store(store_dir)
    benchmark_path = data_dir / "benchmarks" / "jfc_2025_izakaya.yaml"
    benchmark = load_benchmark(benchmark_path) if benchmark_path.exists() else None
    rows = (
        sensitivity(data_dir, request) if request.assumption_set == "standard" else []
    )
    legacy_lines = (
        format_legacy_check(check_legacy(store_dir, data_dir))
        if (store_dir / "legacy" / "v5.yaml").exists()
        else None
    )
    return render_markdown(
        result,
        inputs,
        sensitivity_rows=rows,
        legacy_lines=legacy_lines,
        benchmark=benchmark,
    )


def run_and_report(
    data_dir: Path,
    request: EvaluationRequest,
) -> tuple[EvaluationResult, str]:
    result = evaluate(data_dir, request)[0]
    return result, build_report_markdown(data_dir, result, request)
