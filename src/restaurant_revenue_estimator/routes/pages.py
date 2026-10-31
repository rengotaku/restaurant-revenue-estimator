"""画面 (ARCHITECTURE 6.2 節)."""

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from restaurant_revenue_estimator.config import data_directory
from restaurant_revenue_estimator.database import get_session
from restaurant_revenue_estimator.engine.calendar import (
    calendars_for_store,
    load_holidays,
)
from restaurant_revenue_estimator.engine.scenario import sensitivity
from restaurant_revenue_estimator.inputs import StoreInputs, load_store
from restaurant_revenue_estimator.models import Run, Scenario
from restaurant_revenue_estimator.repositories.scenario_repo import (
    RunRepository,
    ScenarioRepository,
    is_default_run,
)
from restaurant_revenue_estimator.services.evaluation import EvaluationRequest, plain
from restaurant_revenue_estimator.services.evaluation_service import (
    RunRequest,
    execute_run,
)
from restaurant_revenue_estimator.services.store_service import (
    evidence_rows,
    list_stores,
    summarize,
)

router = APIRouter()
templates: Jinja2Templates | None = None
_SENSITIVITY_CACHE: dict[tuple[str, int, str], list[dict[str, Any]]] = {}

STATUSES = (
    "OBSERVED",
    "PUBLIC_VERIFIED",
    "PUBLIC_REPORTED",
    "ASSUMPTION",
    "UNRESOLVED",
    "DERIVED",
)


def set_templates(tmpl: Jinja2Templates) -> None:
    global templates  # noqa: PLW0603
    templates = tmpl


def _ctx(**kwargs: Any) -> dict[str, Any]:
    context: dict[str, Any] = {
        "stores": list_stores(data_directory()),
        "store_id": None,
        "banner": None,
        "error": None,
        "floor2_candidates": [],
        "floor2_choice": "",
        "floor2_four": "",
        "floor2_two": "",
        "selected_overrides": {},
        "selected_scenario": None,
        "has_run": False,
        "run_id": None,
    }
    context.update(kwargs)
    return context


def _render(
    request: Request, template: str, status_code: int = 200, **kwargs: Any
) -> HTMLResponse:
    assert templates is not None
    return templates.TemplateResponse(
        request, template, _ctx(**kwargs), status_code=status_code
    )


def _floor2_candidates(inputs: StoreInputs) -> list[str]:
    layout = inputs.files.get("layout.yaml", {}) or {}
    candidates: list[str] = []
    floors = layout.get("floors", [])
    if isinstance(floors, list):
        for floor in floors:
            if isinstance(floor, dict) and floor.get("id") == "2f":
                tables = floor.get("tables")
                if isinstance(tables, dict):
                    cand_dict = tables.get("candidates") or {}
                    candidates = list(cand_dict.keys())
    return candidates


def _parse_table_count(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return 0
    if not re.fullmatch(r"[0-9]+", text):
        return None
    val = int(text)
    return val if val >= 0 else None


def _banner_text(session: Session, run: Run) -> str | None:
    if is_default_run(run):
        return None
    if run.scenario_id is not None:
        scenario = ScenarioRepository(session).get(run.scenario_id)
        name = scenario.name if scenario else str(run.scenario_id)
        return f"シナリオ『{name}』の試算"
    try:
        data = json.loads(run.result_json)
        overrides = data.get("meta", {}).get("overrides", {})
        if overrides:
            keys = ", ".join(sorted(overrides.keys()))
            return f"未保存の試算（上書き: {keys}）"
    except Exception:
        pass
    return "未保存の試算"


def _resolve_run(
    session: Session, store_id: str, run_id: int | None = None
) -> tuple[Run, str | None]:
    repository = RunRepository(session)
    if run_id is not None:
        run = repository.get(run_id)
        if run is None or run.store_id != store_id:
            raise HTTPException(status_code=404, detail=f"実行が無いか別店舗: {run_id}")
        return run, _banner_text(session, run)

    run = repository.latest_default(store_id)
    if run is None:
        run = execute_run(
            session,
            data_directory(),
            store_id,
            RunRequest(replications=20, seed=42),
        )
    return run, None


def _latest_run(session: Session, store_id: str) -> Run:
    run, _ = _resolve_run(session, store_id, None)
    return run


def _run_result(run: Run) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(run.result_json)
    payload["run_id"] = run.id
    payload["run"] = {
        "id": run.id,
        "replications": run.replications,
        "assumption_set": run.assumption_set,
        "calendar_mode": run.calendar_mode,
        "year": run.year,
        "seed": run.seed,
    }
    return payload


def _get_store(store_id: str) -> StoreInputs:
    directory = data_directory() / "stores" / store_id
    if not directory.exists():
        raise HTTPException(status_code=404, detail=f"店舗が無い: {store_id}")
    return load_store(directory)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    stores = list_stores(data_directory())
    repository = RunRepository(session)
    latest: dict[str, dict[str, Any]] = {}
    for store in stores:
        run = repository.latest_default(store["store_id"])
        if run is not None:
            result = _run_result(run)
            latest[store["store_id"]] = {
                "run_id": run.id,
                "gross": result["revenue"]["total_inc_tax"],
                "income": result["pnl"]["business_income"],
            }
    return _render(request, "index.html", stores=stores, latest=latest)


@router.get("/stores/{store_id}", response_class=HTMLResponse)
def store_summary(
    request: Request,
    store_id: str,
    run: int | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inputs = _get_store(store_id)
    summary = summarize(inputs)
    selected_run, banner = _resolve_run(session, store_id, run)
    result = _run_result(selected_run)
    return _render(
        request,
        "stores/summary.html",
        store_id=store_id,
        summary=summary,
        run=selected_run,
        result=result,
        banner=banner,
    )


@router.get("/stores/{store_id}/evidence", response_class=HTMLResponse)
def store_evidence(
    request: Request,
    store_id: str,
    status: str | None = None,
) -> HTMLResponse:
    inputs = _get_store(store_id)
    rows = evidence_rows(inputs)
    if status:
        rows = [row for row in rows if row.status == status]
    return _render(
        request,
        "stores/evidence.html",
        store_id=store_id,
        rows=rows,
        status=status,
        statuses=STATUSES,
    )


@router.get("/stores/{store_id}/revenue", response_class=HTMLResponse)
def store_revenue(
    request: Request,
    store_id: str,
    tax: str = "inc",
    run: int | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    selected_run, banner = _resolve_run(session, store_id, run)
    result = _run_result(selected_run)
    months = result["revenue"]["months"]
    key = "total_inc_tax" if tax == "inc" else "total_ex_tax"
    chart = {
        "labels": [f"{month['month']}月" for month in months],
        "values": [month[key] for month in months],
    }
    return _render(
        request,
        "stores/revenue.html",
        store_id=store_id,
        result=result,
        months=months,
        total=result["revenue"][key],
        tax=tax,
        chart=json.dumps(chart, ensure_ascii=False),
        banner=banner,
        run_id=run,
    )


@router.get("/stores/{store_id}/calendar", response_class=HTMLResponse)
def store_calendar(
    request: Request,
    store_id: str,
    mode: str = "steady",
    candidate: str | None = None,
    year: int = 2026,
) -> HTMLResponse:
    inputs = _get_store(store_id)
    holidays = load_holidays(
        data_directory() / "calendars" / "jp_holidays_cao.csv",
        data_directory() / "calendars" / "jp_holidays_cao.meta.yaml",
        year,
    )
    series = calendars_for_store(
        inputs,
        year=year,
        mode=mode,
        holidays=holidays,
        saturday_closure_start=candidate,
    )
    selected = series[0]
    months: list[dict[str, Any]] = []
    for month in range(1, 13):
        days = [day for day in selected.days if day.day.month == month]
        months.append({"month": month, "days": days})
    open_count = len(selected.open_days)
    return _render(
        request,
        "stores/calendar.html",
        store_id=store_id,
        mode=mode,
        candidate=candidate or "",
        candidate_names=[item.name for item in series],
        months=months,
        open_count=open_count,
        year=year,
    )


def _floor_view(inputs: Any) -> list[dict[str, Any]]:
    layout = inputs.files.get("layout.yaml", {}) or {}
    floors = layout.get("floors")
    rows: list[dict[str, Any]] = []
    if isinstance(floors, dict) and "status" in floors:
        for name, items in (floors.get("candidates") or {}).items():
            tables = [
                {"id": table["id"], "seats": table["seats"], "floor": floor.get("id")}
                for floor in items
                for table in floor.get("tables", [])
            ]
            rows.append(
                {
                    "id": f"候補 {name}",
                    "policy": "—",
                    "status": floors.get("status"),
                    "tables": tables,
                    "candidates": {},
                    "note": floors.get("note"),
                }
            )
        return rows
    for floor in floors or []:
        tables_raw = floor.get("tables", {})
        if isinstance(tables_raw, dict) and tables_raw.get("status") == "UNRESOLVED":
            status = tables_raw.get("status")
            candidates = tables_raw.get("candidates") or {}
            tables = []
            note = tables_raw.get("note")
        else:
            status = "OBSERVED"
            candidates = {}
            values = (
                tables_raw.get("value") if isinstance(tables_raw, dict) else tables_raw
            )
            tables = [
                {"id": item["id"], "seats": item["seats"], "floor": floor.get("id")}
                for item in values or []
            ]
            note = None
        rows.append(
            {
                "id": floor.get("id"),
                "policy": plain(floor.get("open_policy")),
                "status": status,
                "tables": tables,
                "candidates": candidates,
                "note": note,
            }
        )
    return rows


@router.get("/stores/{store_id}/layout", response_class=HTMLResponse)
def store_layout(
    request: Request,
    store_id: str,
    run: int | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inputs = _get_store(store_id)
    selected_run, banner = _resolve_run(session, store_id, run)
    result = _run_result(selected_run)
    return _render(
        request,
        "stores/layout.html",
        store_id=store_id,
        floor_rows=_floor_view(inputs),
        result=result,
        banner=banner,
    )


def _writable_keys(inputs: Any, assumption_set: str) -> list[dict[str, Any]]:
    assumptions = inputs.files.get(f"assumptions/{assumption_set}.yaml", {}) or {}
    keys: list[dict[str, Any]] = []
    for key, raw in assumptions.items():
        status = str(raw.get("status"))
        if status not in ("ASSUMPTION", "UNRESOLVED"):
            continue
        keys.append(
            {
                "key": key,
                "status": status,
                "value": raw.get("value"),
                "range": raw.get("range") or {},
                "candidates": raw.get("candidates") or {},
                "how_to_verify": raw.get("how_to_verify") or "",
                "scalar": isinstance(raw.get("value"), (int, float)),
            }
        )
    return keys


@router.get("/stores/{store_id}/simulator", response_class=HTMLResponse)
def store_simulator(
    request: Request,
    store_id: str,
    run: int | None = None,
    scenario: int | None = None,
    saved: bool = False,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inputs = _get_store(store_id)
    keys = _writable_keys(inputs, "standard")
    scenarios = ScenarioRepository(session).list(store_id)
    selected_run, banner = _resolve_run(session, store_id, run)
    result = _run_result(selected_run) if selected_run else None
    selected_scenario = None
    selected_overrides: dict[str, Any] = {}
    floor2_choice: str = ""
    floor2_four: int | str = ""
    floor2_two: int | str = ""

    if scenario is not None:
        selected_scenario = ScenarioRepository(session).get(scenario)
        if selected_scenario is not None:
            selected_overrides = json.loads(selected_scenario.overrides_json)
            f2 = selected_overrides.get("layout.floor2")
            if isinstance(f2, str):
                floor2_choice = f2
            elif isinstance(f2, list):
                floor2_choice = "custom"
                four_cnt = sum(
                    1
                    for item in f2
                    if isinstance(item, dict) and item.get("seats") == 4
                )
                two_cnt = sum(
                    1
                    for item in f2
                    if isinstance(item, dict) and item.get("seats") == 2
                )
                floor2_four = four_cnt
                floor2_two = two_cnt

    return _render(
        request,
        "stores/simulator.html",
        store_id=store_id,
        keys=keys,
        scenarios=scenarios,
        result=result,
        saved=saved,
        selected_scenario=selected_scenario,
        selected_overrides=selected_overrides,
        floor2_candidates=_floor2_candidates(inputs),
        floor2_choice=floor2_choice,
        floor2_four=floor2_four,
        floor2_two=floor2_two,
        banner=banner,
        has_run=run is not None,
    )


@router.post("/stores/{store_id}/simulator", response_class=HTMLResponse)
async def submit_simulator(
    request: Request,
    store_id: str,
    session: Session = Depends(get_session),
) -> Response:
    form = await request.form()
    inputs = _get_store(store_id)
    overrides: dict[str, Any] = {}
    for item in _writable_keys(inputs, "standard"):
        if not item["scalar"]:
            continue
        raw = str(form.get(f"key_{item['key']}", "")).strip()
        if raw == "":
            continue
        value: float | int = float(raw)
        if isinstance(item["value"], int):
            value = int(value)
        overrides[item["key"]] = value

    floor2_choice = str(form.get("floor2_choice", "")).strip()
    if floor2_choice == "custom":
        raw_4p = str(form.get("floor2_4p", "")).strip()
        raw_2p = str(form.get("floor2_2p", "")).strip()
        count_4p = _parse_table_count(raw_4p)
        count_2p = _parse_table_count(raw_2p)
        if count_4p is None or count_2p is None or (count_4p + count_2p == 0):
            error_msg = "自由入力では 0 以上の整数を入力し、1 卓以上入れる"
            selected_run = RunRepository(session).latest_default(store_id)
            result = _run_result(selected_run) if selected_run else None
            return _render(
                request,
                "stores/simulator.html",
                status_code=200,
                store_id=store_id,
                keys=_writable_keys(inputs, "standard"),
                scenarios=ScenarioRepository(session).list(store_id),
                result=result,
                saved=False,
                error=error_msg,
                floor2_candidates=_floor2_candidates(inputs),
                floor2_choice="custom",
                floor2_four=raw_4p,
                floor2_two=raw_2p,
                banner=None,
            )
        tables = [
            {"id": f"2f-4-{index}", "seats": 4}
            for index in range(count_4p)
        ] + [
            {"id": f"2f-2-{index}", "seats": 2}
            for index in range(count_2p)
        ]
        overrides["layout.floor2"] = tables
    elif floor2_choice != "":
        overrides["layout.floor2"] = floor2_choice

    replications = int(str(form.get("replications", "20")))
    scenario_id = str(form.get("scenario_id", "")).strip()
    if scenario_id:
        overrides = json.loads(
            ScenarioRepository(session).get(int(scenario_id)).overrides_json  # type: ignore[union-attr]
        )
        replications = int(str(form.get("replications", "20")))

    name = str(form.get("name", "")).strip()
    saved_scenario = None
    if name:
        saved_scenario = ScenarioRepository(session).add(
            Scenario(
                store_id=store_id,
                name=name,
                overrides_json=json.dumps(
                    overrides, ensure_ascii=False, sort_keys=True
                ),
                note=str(form.get("note", "")),
            )
        )

    target_scenario_id = (
        saved_scenario.id
        if saved_scenario
        else (int(scenario_id) if scenario_id else None)
    )
    run = execute_run(
        session,
        data_directory(),
        store_id,
        RunRequest(
            scenario_id=target_scenario_id,
            replications=replications,
            overrides=overrides,
        ),
    )
    redirect_url = f"/stores/{store_id}/simulator?run={run.id}"
    if name:
        redirect_url += "&saved=1"
    return RedirectResponse(
        url=redirect_url, status_code=303
    )


@router.get("/stores/{store_id}/costs", response_class=HTMLResponse)
def store_costs(
    request: Request,
    store_id: str,
    run: int | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inputs = _get_store(store_id)
    selected_run, banner = _resolve_run(session, store_id, run)
    result = _run_result(selected_run)
    assumptions = inputs.files.get("assumptions/standard.yaml", {}) or {}
    part_time = assumptions.get("part_time_staff", {})
    runtime_overrides = result.get("meta", {}).get("overrides", {}) or {}
    part_time_value = (
        runtime_overrides.get("part_time_staff") or part_time.get("value") or {}
    )
    wage = float(part_time_value.get("hourly_wage", 0))
    warnings = []
    if wage and wage < 1280:
        warnings.append(
            f"時給 {wage:.0f} 円が東京都最低賃金 1,280 円（2026-10-01〜）を下回る"
        )
    pnl = result["pnl"]
    denominator = pnl["gross_inc_tax"] or 1
    jfc = {
        "gross_profit_ratio": pnl["gross_profit"] / denominator,
        "labor_ratio": (pnl["labor"] + pnl["owner_labor"]) / denominator,
        "operating_margin": pnl["store_operating_profit"] / denominator,
    }
    return _render(
        request,
        "stores/costs.html",
        store_id=store_id,
        result=result,
        pnl=pnl,
        assumptions=assumptions,
        warnings=warnings,
        jfc=jfc,
        banner=banner,
    )


@router.get("/stores/{store_id}/priorities", response_class=HTMLResponse)
def store_priorities(
    request: Request,
    store_id: str,
    year: int = 2026,
    mode: str = "steady",
) -> HTMLResponse:
    cache_key = (store_id, year, mode)
    rows = _SENSITIVITY_CACHE.get(cache_key)
    if rows is None:
        request_model = EvaluationRequest(
            store_id=store_id,
            year=year,
            calendar_mode=mode,
            replications=5,
        )
        computed = sensitivity(data_directory(), request_model)
        rows = [row.as_dict() for row in computed]
        _SENSITIVITY_CACHE[cache_key] = rows
    chart = {
        "labels": [row["key"] for row in rows[:10]],
        "revenue": [row["delta_revenue"] for row in rows[:10]],
        "income": [row["delta_income"] for row in rows[:10]],
    }
    return _render(
        request,
        "stores/priorities.html",
        store_id=store_id,
        rows=rows[:10],
        chart=json.dumps(chart, ensure_ascii=False),
        year=year,
        mode=mode,
    )


@router.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    runs: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    repository = RunRepository(session)
    all_runs: list[Run | None] = []
    if runs:
        ids = [int(item) for item in runs.split(",") if item.strip()]
        all_runs = [repository.get(run_id) for run_id in ids]
    selected = [run for run in all_runs if run is not None]
    error = None
    if len(selected) >= 2:
        years = {run.year for run in selected}
        modes = {run.calendar_mode for run in selected}
        if len(years) > 1 or len(modes) > 1:
            error = "年または暦のモードが違う実行は比較できない。同じ期間で比べる。"
            selected = []
    rows = [_run_result(run) for run in selected]
    recent = repository.list_recent(20) if not selected else []
    return _render(
        request,
        "compare.html",
        selected=selected,
        rows=rows,
        error=error,
        runs_query=runs or "",
        recent=recent,
    )


@router.post("/stores/{store_id}/scenarios", response_class=HTMLResponse)
async def create_scenario(
    request: Request, store_id: str, session: Session = Depends(get_session)
) -> RedirectResponse:
    form = await request.form()
    overrides = json.loads(str(form.get("overrides_json", "{}")))
    scenario = Scenario(
        store_id=store_id,
        name=str(form.get("name", "無名のシナリオ")),
        overrides_json=json.dumps(overrides, ensure_ascii=False, sort_keys=True),
        note=str(form.get("note", "")),
    )
    ScenarioRepository(session).add(scenario)
    return RedirectResponse(
        url=f"/stores/{store_id}/simulator?saved=1", status_code=303
    )
