"""画面・API からの評価実行と保存."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session

from restaurant_revenue_estimator import code_version
from restaurant_revenue_estimator.inputs import input_hash, load_store
from restaurant_revenue_estimator.models import Run, Scenario
from restaurant_revenue_estimator.report import result_to_dict
from restaurant_revenue_estimator.repositories.scenario_repo import (
    RunRepository,
    ScenarioRepository,
)
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationError,
    EvaluationRequest,
    apply_overrides,
    evaluate,
)
from restaurant_revenue_estimator.services.reporting import build_report_markdown


class RunRequest(BaseModel):
    scenario_id: int | None = None
    assumption_set: str = "standard"
    calendar_mode: str = "steady"
    year: int = 2026
    seed: int = 42
    replications: int = Field(default=20, ge=1, le=1000)
    overrides: dict[str, Any] = Field(default_factory=dict)


class OverrideError(Exception):
    """上書きできないキー."""


def _validate_overrides(data_dir: Path, store_id: str, req: RunRequest) -> None:
    inputs = load_store(data_dir / "stores" / store_id)
    assumptions = inputs.files.get(f"assumptions/{req.assumption_set}.yaml")
    if not isinstance(assumptions, dict):
        raise OverrideError(f"仮定セットが無い: {req.assumption_set}")
    assumption_overrides = {
        key: value for key, value in req.overrides.items() if key != "layout.floor2"
    }
    try:
        apply_overrides(assumptions, assumption_overrides)
    except EvaluationError as exc:
        raise OverrideError(str(exc)) from exc

    if "layout.floor2" in req.overrides:
        choice = req.overrides["layout.floor2"]
        if isinstance(choice, str):
            layout = inputs.files.get("layout.yaml", {}) or {}
            candidates: set[str] = set()
            for floor in layout.get("floors", []) or []:
                tables = floor.get("tables")
                if isinstance(tables, dict):
                    candidates.update(tables.get("candidates") or {})
            if choice not in candidates:
                raise OverrideError(
                    f"上書きできないキー: layout.floor2（候補 {sorted(candidates)}）"
                )
        elif isinstance(choice, list):
            for item in choice:
                if (
                    not isinstance(item, dict)
                    or "id" not in item
                    or "seats" not in item
                ):
                    raise OverrideError("layout.floor2 は {id, seats} の配列で指定する")
        else:
            raise OverrideError("layout.floor2 は候補名か卓の配列で指定する")


def execute_run(
    session: Session, data_dir: Path, store_id: str, req: RunRequest
) -> Run:
    """実行する. 同じ入力・seed・replications なら DB の結果を返す."""
    scenario: Scenario | None = None
    if req.scenario_id is not None:
        scenario = ScenarioRepository(session).get(req.scenario_id)
        if scenario is None or scenario.store_id != store_id:
            raise OverrideError(f"シナリオが無い: {req.scenario_id}")
        req = req.model_copy(
            update={
                "overrides": json.loads(scenario.overrides_json),
                "assumption_set": scenario.base_assumption_set,
            }
        )

    _validate_overrides(data_dir, store_id, req)
    inputs = load_store(data_dir / "stores" / store_id)
    run_hash = input_hash(inputs, req.overrides)
    repository = RunRepository(session)
    target_scenario_id = scenario.id if scenario else None
    cached = repository.find(
        store_id=store_id,
        input_hash=run_hash,
        seed=req.seed,
        replications=req.replications,
        assumption_set=req.assumption_set,
        calendar_mode=req.calendar_mode,
        year=req.year,
        scenario_id=target_scenario_id,
    )
    if cached is not None:
        return cached

    request = EvaluationRequest(
        store_id=store_id,
        assumption_set=req.assumption_set,
        year=req.year,
        calendar_mode=req.calendar_mode,
        seed=req.seed,
        replications=req.replications,
        overrides=req.overrides,
    )
    results = evaluate(data_dir, request)
    choice = req.overrides.get("layout.floor2")
    if isinstance(choice, str):
        result = next((item for item in results if item.variant == choice), results[0])
    elif isinstance(choice, list):
        result = next(
            (item for item in results if item.variant == "custom"), results[0]
        )
    else:
        result = results[0]

    report = build_report_markdown(data_dir, result, request)
    run = Run(
        store_id=store_id,
        scenario_id=scenario.id if scenario else None,
        assumption_set=req.assumption_set,
        calendar_mode=req.calendar_mode,
        year=req.year,
        seed=req.seed,
        replications=req.replications,
        input_hash=run_hash,
        model_version=result.meta.model_version,
        code_sha256=code_version.current_code_sha256(),
        calendar_sha256=result.meta.calendar_sha256,
        result_json=json.dumps(
            result_to_dict(result), ensure_ascii=False, sort_keys=True
        ),
        report_md=report,
    )
    return repository.add(run)
