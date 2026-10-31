"""ARCHITECTURE 6.2 節の API."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from restaurant_revenue_estimator.config import data_directory
from restaurant_revenue_estimator.database import get_session
from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.models import Scenario
from restaurant_revenue_estimator.repositories.scenario_repo import (
    RunRepository,
    ScenarioRepository,
)
from restaurant_revenue_estimator.services.evaluation_service import (
    OverrideError,
    RunRequest,
    execute_run,
)
from restaurant_revenue_estimator.services.store_service import (
    evidence_rows,
    list_stores,
    summarize,
)

router = APIRouter(prefix="/api")


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_assumption_set: str = "standard"
    overrides: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class ScenarioUpdate(BaseModel):
    name: str | None = None
    base_assumption_set: str | None = None
    overrides: dict[str, Any] | None = None
    note: str | None = None


@router.get("/stores")
def api_stores() -> list[dict[str, str]]:
    return list_stores(data_directory())


@router.get("/stores/{store_id}")
def api_store(store_id: str) -> dict[str, Any]:
    directory = data_directory() / "stores" / store_id
    if not directory.exists():
        raise HTTPException(status_code=404, detail=f"店舗が無い: {store_id}")
    inputs = load_store(directory)
    summary = summarize(inputs)
    return {
        "store_id": summary.store_id,
        "name": summary.name,
        "address": summary.address,
        "phone": summary.phone,
        "related_stores": summary.related_names,
        "source_from": summary.source_from,
        "source_to": summary.source_to,
        "status_counts": summary.status_counts,
        "total_keys": summary.total_keys,
        "evidence": [row.__dict__ for row in evidence_rows(inputs)],
    }


def _get_run(run_id: int, session: Session):  # type: ignore[no-untyped-def]
    run = RunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"実行が無い: {run_id}")
    return run


@router.post("/stores/{store_id}/runs")
def api_create_run(
    store_id: str,
    body: RunRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        run = execute_run(session, data_directory(), store_id, body)
    except OverrideError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result: dict[str, Any] = json.loads(run.result_json)
    result["run_id"] = run.id
    result["run"] = {
        "id": run.id,
        "replications": run.replications,
        "assumption_set": run.assumption_set,
        "calendar_mode": run.calendar_mode,
        "year": run.year,
        "seed": run.seed,
    }
    return result


@router.get("/runs/{run_id}")
def api_get_run(run_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    run = _get_run(run_id, session)
    payload: dict[str, Any] = json.loads(run.result_json)
    payload["run_id"] = run.id
    return payload


@router.get("/runs/{run_id}/report.md")
def api_report(run_id: int, session: Session = Depends(get_session)) -> Response:
    run = _get_run(run_id, session)
    return Response(content=run.report_md, media_type="text/markdown; charset=utf-8")


@router.get("/stores/{store_id}/scenarios")
def api_list_scenarios(
    store_id: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    scenarios = ScenarioRepository(session).list(store_id)
    return [_scenario_dict(scenario) for scenario in scenarios]


@router.post("/stores/{store_id}/scenarios", status_code=201)
def api_create_scenario(
    store_id: str,
    body: ScenarioCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    scenario = Scenario(
        store_id=store_id,
        name=body.name,
        base_assumption_set=body.base_assumption_set,
        overrides_json=json.dumps(body.overrides, ensure_ascii=False, sort_keys=True),
        note=body.note,
    )
    return _scenario_dict(ScenarioRepository(session).add(scenario))


@router.get("/scenarios/{scenario_id}")
def api_get_scenario(
    scenario_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    scenario = ScenarioRepository(session).get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="シナリオが無い")
    return _scenario_dict(scenario)


@router.put("/scenarios/{scenario_id}")
def api_update_scenario(
    scenario_id: int,
    body: ScenarioUpdate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    repository = ScenarioRepository(session)
    scenario = repository.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="シナリオが無い")
    if body.name is not None:
        scenario.name = body.name
    if body.base_assumption_set is not None:
        scenario.base_assumption_set = body.base_assumption_set
    if body.overrides is not None:
        scenario.overrides_json = json.dumps(
            body.overrides, ensure_ascii=False, sort_keys=True
        )
    if body.note is not None:
        scenario.note = body.note
    session.add(scenario)
    session.commit()
    session.refresh(scenario)
    return _scenario_dict(scenario)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def api_delete_scenario(
    scenario_id: int, session: Session = Depends(get_session)
) -> Response:
    repository = ScenarioRepository(session)
    scenario = repository.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="シナリオが無い")
    repository.delete(scenario)
    return Response(status_code=204)


def _scenario_dict(scenario: Scenario) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "store_id": scenario.store_id,
        "name": scenario.name,
        "base_assumption_set": scenario.base_assumption_set,
        "overrides": json.loads(scenario.overrides_json),
        "note": scenario.note,
        "created_at": scenario.created_at.isoformat(),
        "updated_at": scenario.updated_at.isoformat(),
    }
