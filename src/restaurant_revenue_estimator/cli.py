"""rre コマンド: 入力の検証・評価の実行・レポート出力."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from restaurant_revenue_estimator import __version__
from restaurant_revenue_estimator.engine.observation import fit_grid
from restaurant_revenue_estimator.engine.tables import Table
from restaurant_revenue_estimator.inputs import load_store, validate_all, validate_store
from restaurant_revenue_estimator.inputs.legacy import check_legacy, format_legacy_check
from restaurant_revenue_estimator.report import (
    load_benchmark,
    render_run_markdown,
)
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationError,
    EvaluationRequest,
    evaluate,  # noqa: F401
    evaluate_run,
    plain,
)
from restaurant_revenue_estimator.services.reporting import build_report_markdown

app = typer.Typer(
    help="飲食店 1 店舗の収益を根拠付きシナリオで評価する。",
    no_args_is_help=True,
)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _version_callback(value: bool) -> None:
    """`--version` が指定されたらバージョンを出して終了する."""
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="バージョンを表示して終了する。",
        ),
    ] = False,
) -> None:
    """サブコマンドの入口."""


@app.command()
def validate(
    store_dir: Annotated[
        Path | None,
        typer.Argument(help="data/stores/<store_id> のディレクトリ"),
    ] = None,
    all_stores: Annotated[bool, typer.Option("--all", help="全店舗を検査する")] = False,
    data_dir: Annotated[
        Path, typer.Option("--data-dir", hidden=True)
    ] = DEFAULT_DATA_DIR,
) -> None:
    """入力 YAML を検査する. 1 件でもエラーがあれば終了コード 1."""
    if not all_stores and store_dir is None:
        typer.echo("店舗ディレクトリか --all を指定してください。", err=True)
        raise typer.Exit(2)

    issues = (
        validate_all(data_dir) if all_stores else validate_store(store_dir or data_dir)
    )
    for issue in issues:
        typer.echo(str(issue))
    if issues:
        raise typer.Exit(1)
    typer.echo("OK")


@app.command("legacy-check")
def legacy_check(
    store_dir: Annotated[
        Path, typer.Argument(help="data/stores/<store_id> のディレクトリ")
    ],
    data_dir: Annotated[
        Path, typer.Option("--data-dir", hidden=True)
    ] = DEFAULT_DATA_DIR,
) -> None:
    """過去の試算 v5 の不整合を出す."""
    check = check_legacy(store_dir, data_dir)
    for line in format_legacy_check(check):
        typer.echo(line)


DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


@app.command()
def run(
    store_dir: Annotated[
        Path, typer.Argument(help="data/stores/<store_id> のディレクトリ")
    ],
    assumptions: Annotated[str, typer.Option("--assumptions")] = "standard",
    year: Annotated[int, typer.Option("--year")] = 2026,
    calendar: Annotated[str, typer.Option("--calendar")] = "steady",
    seed: Annotated[int, typer.Option("--seed")] = 42,
    replications: Annotated[int, typer.Option("--replications")] = 20,
    runs_dir: Annotated[
        Path, typer.Option("--runs-dir", hidden=True)
    ] = DEFAULT_RUNS_DIR,
    data_dir: Annotated[
        Path, typer.Option("--data-dir", hidden=True)
    ] = DEFAULT_DATA_DIR,
) -> None:
    """評価を実行し、runs/<run_id>/ に result.json と report.md を書く."""
    store_id = store_dir.name
    request = EvaluationRequest(
        store_id=store_id,
        assumption_set=assumptions,
        year=year,
        calendar_mode=calendar,
        seed=seed,
        replications=replications,
    )
    try:
        document = evaluate_run(data_dir, request)
    except EvaluationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    result = document.results[0]
    run_id = (
        f"{store_id}-{document.meta.input_hash[:12]}-{seed}-{assumptions}-{calendar}"
    )
    directory = runs_dir / run_id
    directory.mkdir(parents=True, exist_ok=True)

    report_text = build_report_markdown(data_dir, document, request)

    (directory / "result.json").write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (directory / "report.md").write_text(report_text, encoding="utf-8")
    (directory / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "created_at": datetime.now(UTC).isoformat(),
                "store_id": store_id,
                "year": year,
                "calendar": calendar,
                "assumptions": assumptions,
                "seed": seed,
                "replications": replications,
                "meta": document.meta.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    typer.echo(f"{directory}")
    income = result.pnl.yen(result.pnl.business_income)
    typer.echo(f"年間税込売上 {result.gross_inc_tax:,} 円 / 事業所得相当 {income:,} 円")


@app.command()
def report(
    run_dir: Annotated[Path, typer.Argument(help="runs/<run_id> のディレクトリ")],
    data_dir: Annotated[
        Path, typer.Option("--data-dir", hidden=True)
    ] = DEFAULT_DATA_DIR,
) -> None:
    """result.json から report.md を作り直す."""
    run_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    store_id = str(run_meta["store_id"])
    inputs = load_store(data_dir / "stores" / store_id)
    request = EvaluationRequest(
        store_id=store_id,
        assumption_set=str(run_meta["assumptions"]),
        year=int(run_meta["year"]),
        calendar_mode=str(run_meta["calendar"]),
        seed=int(run_meta["seed"]),
        replications=int(run_meta["replications"]),
    )
    document = evaluate_run(data_dir, request)
    benchmark_path = data_dir / "benchmarks" / "jfc_2025_izakaya.yaml"
    benchmark = load_benchmark(benchmark_path) if benchmark_path.exists() else None
    (run_dir / "report.md").write_text(
        render_run_markdown(document, inputs, benchmark=benchmark), encoding="utf-8"
    )
    typer.echo(str(run_dir / "report.md"))


@app.command("fit-observation")
def fit_observation(
    store_dir: Annotated[
        Path, typer.Argument(help="data/stores/<store_id> のディレクトリ")
    ],
    replications: Annotated[int, typer.Option("--replications")] = 20,
) -> None:
    """観察を再現できた卓構成・到着・滞在の組合せを全部出す."""
    inputs = load_store(store_dir)
    layout = inputs.files.get("layout.yaml", {}) or {}
    floor1: list[Table] = []
    candidates: dict[str, list[Table]] = {}
    for floor in layout.get("floors", []):
        floor_id = str(floor.get("id"))
        tables_raw = floor.get("tables", {})
        if isinstance(tables_raw, dict) and tables_raw.get("status") == "UNRESOLVED":
            for name, items in (tables_raw.get("candidates") or {}).items():
                candidates[str(name)] = [
                    Table(str(item["id"]), int(item["seats"]), floor_id)
                    for item in items
                ]
        else:
            items = (
                tables_raw.get("value") if isinstance(tables_raw, dict) else tables_raw
            )
            floor1.extend(
                Table(str(item["id"]), int(item["seats"]), floor_id)
                for item in items or []
            )
    assumptions = inputs.files.get("assumptions/standard.yaml", {}) or {}
    groups = assumptions["lunch_arrival_groups"]
    stays = assumptions["lunch_stay_minutes"]
    ratio_raw = assumptions.get("lunch_stay_ratio_by_group_size")
    stay_ratio = plain(ratio_raw) if ratio_raw is not None else None
    rows = fit_grid(
        floor1_tables=floor1,
        candidates=candidates,
        group_levels={name: float(value) for name, value in groups["range"].items()},
        stay_levels={name: int(value) for name, value in stays["range"].items()},
        group_sizes=[
            int(key) for key in assumptions["group_size_distribution"]["value"]
        ],
        group_probs=[
            float(value)
            for value in assumptions["group_size_distribution"]["value"].values()
        ],
        cleanup_minutes=int(plain(assumptions["cleanup_minutes"])),
        max_wait_minutes=int(plain(assumptions["max_wait_minutes"])),
        replications=replications,
        stay_ratio_by_group_size=stay_ratio,
    )
    typer.echo(
        "観察は 1 回・時刻を選んで見ている。ここで出るのは候補であって推定値ではない。"
    )
    for row in rows:
        failed = ", ".join(row.failed_conditions) or "なし"
        typer.echo(
            f"{row.candidate}: 再現率 {row.reproduction_rate:.2f} / 失敗 {failed}"
        )


if __name__ == "__main__":
    app()
