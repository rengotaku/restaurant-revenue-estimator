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
from restaurant_revenue_estimator.engine.pnl import (
    jfc_benchmark_rows,
    jfc_comparison,
)
from restaurant_revenue_estimator.engine.scenario import sensitivity
from restaurant_revenue_estimator.inputs import StoreInputs, load_store
from restaurant_revenue_estimator.models import Run, Scenario
from restaurant_revenue_estimator.report import load_benchmark
from restaurant_revenue_estimator.repositories.scenario_repo import (
    RunRepository,
    ScenarioRepository,
    is_default_run,
)
from restaurant_revenue_estimator.services.backtest_view import build_backtest_view
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    floor1_table_counts,
    plain,
)
from restaurant_revenue_estimator.services.evaluation_service import (
    RunRequest,
    execute_run,
)
from restaurant_revenue_estimator.services.screens import (
    build_daily_table_rows,
    build_monthly_table_rows,
    extract_cost_assumptions,
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


def _extract_variants(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """実行結果から候補の一覧を取り出す.
    variants があればそれを返し、無ければ旧形式の最上位の中身を 1 件の候補として返す
    （name は最上位の variant、choices は空の dict）.
    """
    if not result:
        return []
    variants = result.get("variants")
    if isinstance(variants, list):
        return [dict(v) for v in variants if isinstance(v, dict)]
    candidate = dict(result)
    candidate["name"] = result.get("variant", "")
    candidate["choices"] = {}
    return [candidate]


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
        "variants": [],
    }
    context.update(kwargs)
    return context


def _render(
    request: Request, template: str, status_code: int = 200, **kwargs: Any
) -> HTMLResponse:
    assert templates is not None
    ctx = _ctx(**kwargs)
    if "variants" not in kwargs:
        ctx["variants"] = _extract_variants(ctx.get("result"))
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


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


def _active_variant(
    result: dict[str, Any] | None, variant_name: str | None = None
) -> dict[str, Any] | None:
    """表示対象の variant を返す（?variant=<name>、無い・見つからないときは先頭）.
    最上位のメタ情報（run, run_id, meta, overrides など）も合成して返す.
    """
    if result is None:
        return None
    variants = result.get("variants")
    if not variants or not isinstance(variants, list):
        return result

    selected: dict[str, Any] | None = None
    if variant_name:
        for v in variants:
            if isinstance(v, dict) and v.get("name") == variant_name:
                selected = v
                break
    if selected is None:
        selected = variants[0]

    merged = dict(selected)
    for key in ("meta", "overrides", "unresolved"):
        if key in result and key not in merged:
            merged[key] = result[key]
    for key in ("run", "run_id", "variants", "scenarios"):
        if key in result:
            merged[key] = result[key]
    return merged


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
            result = _active_variant(_run_result(run))
            assert result is not None
            latest[store["store_id"]] = {
                "run_id": run.id,
                "gross": result["revenue"]["total_inc_tax"],
                "income": result["pnl"]["business_income"],
            }
    return _render(request, "index.html", stores=stores, latest=latest)


def _get_store_external_urls(inputs: StoreInputs) -> list[dict[str, str]]:
    store = inputs.files.get("store.yaml", {}) or {}
    urls: list[dict[str, str]] = []
    official = plain(store.get("official_url") or store.get("url"))
    if official:
        urls.append({"label": "公式 URL", "url": str(official)})
    external_ids = store.get("external_ids", {}) or {}
    for name, item in external_ids.items():
        val = str(plain(item))
        url = None
        if isinstance(item, dict) and item.get("sources"):
            for sid in item.get("sources", []):
                src = inputs.sources.get(sid)
                if src and src.url:
                    url = src.url
                    break
        if not url:
            for src in inputs.sources.values():
                if "tabelog.com" in src.url and val in src.url:
                    url = src.url
                    break
        if url:
            label = "食べログ" if name == "tabelog" else name
            urls.append({"label": label, "url": url})
    return urls


@router.get("/stores/{store_id}", response_class=HTMLResponse)
def store_summary(
    request: Request,
    store_id: str,
    run: int | None = None,
    variant: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inputs = _get_store(store_id)
    summary = summarize(inputs)
    selected_run, banner = _resolve_run(session, store_id, run)
    raw = _run_result(selected_run) if selected_run else None
    result = _active_variant(raw, variant)
    variants = _extract_variants(raw)
    external_urls = _get_store_external_urls(inputs)
    operations = inputs.files.get("operations.yaml", {}) or {}
    closure_candidates = (
        operations.get("saturday_closure_start", {}).get("candidates", {})
        if isinstance(operations.get("saturday_closure_start"), dict)
        else {}
    )
    hours_candidates = (
        operations.get("saturday_hours_before_closure", {}).get("candidates", {})
        if isinstance(operations.get("saturday_hours_before_closure"), dict)
        else {}
    )
    return _render(
        request,
        "stores/summary.html",
        store_id=store_id,
        summary=summary,
        run=selected_run,
        result=result,
        variants=variants,
        banner=banner,
        external_urls=external_urls,
        saturday_closure_candidates=closure_candidates,
        saturday_hours_candidates=hours_candidates,
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
    variant: str | None = None,
    month: int | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    selected_run, banner = _resolve_run(session, store_id, run)
    raw = _run_result(selected_run) if selected_run else None
    result = _active_variant(raw, variant)
    variants = _extract_variants(raw)
    assert result is not None
    months = result["revenue"]["months"]
    key = "total_inc_tax" if tax == "inc" else "total_ex_tax"
    chart = {
        "labels": [f"{m['month']}月" for m in months],
        "values": [m[key] for m in months],
    }

    scenario_order = {"pessimistic": 0, "standard": 1, "optimistic": 2}
    scenario_names = {"pessimistic": "悲観", "standard": "標準", "optimistic": "楽観"}
    raw_scenarios = [
        s for s in result.get("scenarios", []) if s.get("variant") == result.get("name")
    ]
    raw_scenarios.sort(
        key=lambda s: scenario_order.get(s.get("assumption_set", ""), 99)
    )
    variant_scenarios = [
        {
            **s,
            "display_name": scenario_names.get(
                s.get("assumption_set", ""), s.get("assumption_set", "")
            ),
        }
        for s in raw_scenarios
    ]

    has_other = any(m.get("other_inc_tax", 0) > 0 for m in months)
    table_rows = build_monthly_table_rows(months, tax, has_other)
    year_summary = {
        "lunch": sum(r["lunch"] for r in table_rows),
        "dinner": sum(r["dinner"] for r in table_rows),
        "ala": sum(r["ala"] for r in table_rows),
        "course": sum(r["course"] for r in table_rows),
        "appetizer": sum(r["appetizer"] for r in table_rows),
        "other": sum(r["other"] for r in table_rows),
        "total": sum(r["total"] for r in table_rows),
    }

    all_days = result.get("days") or result.get("revenue", {}).get("days", [])
    selected_month: int | None = None
    daily_rows: list[dict[str, Any]] = []
    daily_has_other = False
    daily_total = 0

    if month is not None and 1 <= month <= 12:
        selected_month = month
        month_prefix = f"-{month:02d}-"
        m_days = [
            d
            for d in all_days
            if (month_prefix in d.get("date", "") or month_prefix in d.get("day", ""))
        ]
        daily_has_other = any(d.get("other", 0) > 0 for d in m_days)
        daily_rows, daily_total = build_daily_table_rows(
            m_days, months, month, tax, daily_has_other
        )

    resolved_run_id = selected_run.id if selected_run else run
    backtest = build_backtest_view(
        store_dir=data_directory() / "stores" / store_id,
        days=result.get("days") or [],
        scenario=str(result.get("assumption_set") or "standard"),
        variant=str(result.get("name") or ""),
        store_id=store_id,
    )

    return _render(
        request,
        "stores/revenue.html",
        store_id=store_id,
        result=result,
        backtest=backtest,
        variants=variants,
        months=months,
        table_rows=table_rows,
        year_summary=year_summary,
        has_other=has_other,
        total=year_summary["total"],
        tax=tax,
        chart=json.dumps(chart, ensure_ascii=False),
        variant_scenarios=variant_scenarios,
        banner=banner,
        run_id=resolved_run_id,
        selected_month=selected_month,
        daily_rows=daily_rows,
        daily_has_other=daily_has_other,
        daily_total=daily_total,
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
    variant: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inputs = _get_store(store_id)
    selected_run, banner = _resolve_run(session, store_id, run)
    raw = _run_result(selected_run) if selected_run else None
    result = _active_variant(raw, variant)
    variants = _extract_variants(raw)
    return _render(
        request,
        "stores/layout.html",
        store_id=store_id,
        floor_rows=_floor_view(inputs),
        result=result,
        variants=variants,
        banner=banner,
        floor2_candidates=_floor2_candidates(inputs),
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
    variant: str | None = None,
    scenario: int | None = None,
    saved: bool = False,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inputs = _get_store(store_id)
    keys = _writable_keys(inputs, "standard")
    scenarios = ScenarioRepository(session).list(store_id)
    selected_run, banner = _resolve_run(session, store_id, run)
    result = (
        _active_variant(_run_result(selected_run), variant) if selected_run else None
    )
    selected_scenario = None
    selected_overrides: dict[str, Any] = {}
    floor1_four, floor1_two = floor1_table_counts(inputs)
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
        floor1_four=floor1_four,
        floor1_two=floor1_two,
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
            result = (
                _active_variant(_run_result(selected_run)) if selected_run else None
            )
            floor1_four, floor1_two = floor1_table_counts(inputs)
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
                floor1_four=floor1_four,
                floor1_two=floor1_two,
                floor2_candidates=_floor2_candidates(inputs),
                floor2_choice="custom",
                floor2_four=raw_4p,
                floor2_two=raw_2p,
                banner=None,
            )
        tables = [{"id": f"2f-4-{index}", "seats": 4} for index in range(count_4p)] + [
            {"id": f"2f-2-{index}", "seats": 2} for index in range(count_2p)
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
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/stores/{store_id}/costs", response_class=HTMLResponse)
def store_costs(
    request: Request,
    store_id: str,
    run: int | None = None,
    variant: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    selected_run, banner = _resolve_run(session, store_id, run)
    raw = _run_result(selected_run) if selected_run else None
    result = _active_variant(raw, variant)
    variants = _extract_variants(raw)
    assert result is not None
    warnings = list(result.get("warnings") or [])
    pnl = result["pnl"]
    benchmark_path = data_directory() / "benchmarks" / "jfc_2025_izakaya.yaml"
    benchmark = load_benchmark(benchmark_path) if benchmark_path.exists() else {}
    jfc = jfc_comparison(pnl)
    jfc_rows = jfc_benchmark_rows(pnl, benchmark, null_label="—（公表値なし）")
    cost_data = extract_cost_assumptions(result=result, raw=raw)
    return _render(
        request,
        "stores/costs.html",
        store_id=store_id,
        result=result,
        variants=variants,
        pnl=pnl,
        warnings=warnings,
        jfc=jfc,
        jfc_rows=jfc_rows,
        banner=banner,
        **cost_data,
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


def _resolve_item_run(
    session: Session,
    raw_token: str,
    year: int,
    calendar: str,
) -> tuple[dict[str, Any] | None, Run | None, str | None]:
    token = raw_token.strip()
    if not token:
        return None, None, None
    parts = token.split(":")
    scenario_names = {
        "pessimistic": "悲観",
        "standard": "標準",
        "optimistic": "楽観",
    }
    if len(parts) == 3 and parts[1] == "scenario":
        store_id = parts[0]
        try:
            sc_id = int(parts[2])
        except ValueError:
            return None, None, f"不正なシナリオID: {parts[2]}"
        sc = ScenarioRepository(session).get(sc_id)
        if sc is None:
            return None, None, f"シナリオが無い: {sc_id}"
        label = sc.name
        req = RunRequest(
            scenario_id=sc_id,
            year=year,
            calendar_mode=calendar,
            seed=42,
            replications=20,
        )
    elif len(parts) == 2:
        store_id = parts[0]
        aset = parts[1]
        label = scenario_names.get(aset, aset)
        req = RunRequest(
            assumption_set=aset,
            year=year,
            calendar_mode=calendar,
            seed=42,
            replications=20,
        )
    else:
        return None, None, f"不正な形式: {token}"

    try:
        run = execute_run(session, data_directory(), store_id, req)
    except Exception as exc:
        return None, None, str(exc)

    res_data = _run_result(run)
    group = {
        "label": label,
        "store_id": store_id,
        "run": run,
        "variants": _extract_variants(res_data),
        "year": run.year,
        "calendar_mode": run.calendar_mode,
    }
    return group, run, None


def _load_compare_items(
    session: Session,
    items: str,
    year: int,
    calendar: str,
) -> tuple[list[dict[str, Any]], list[Run], str | None]:
    groups: list[dict[str, Any]] = []
    selected: list[Run] = []
    for raw_token in items.split(","):
        group, run, err = _resolve_item_run(session, raw_token, year, calendar)
        if err:
            return [], [], err
        if group and run:
            groups.append(group)
            selected.append(run)
    return groups, selected, None


def _load_compare_runs(
    repository: RunRepository,
    session: Session,
    runs: str,
) -> tuple[list[dict[str, Any]], list[Run], str | None]:
    ids = [int(item) for item in runs.split(",") if item.strip()]
    all_runs = [repository.get(run_id) for run_id in ids]
    selected = [run for run in all_runs if run is not None]
    if len(selected) >= 2:
        years = {run.year for run in selected}
        modes = {run.calendar_mode for run in selected}
        if len(years) > 1 or len(modes) > 1:
            return (
                [],
                [],
                "年または暦のモードが違う実行は比較できない。同じ期間で比べる。",
            )
    scenario_names = {
        "pessimistic": "悲観",
        "standard": "標準",
        "optimistic": "楽観",
    }
    groups: list[dict[str, Any]] = []
    for run in selected:
        label = _banner_text(session, run) or scenario_names.get(
            run.assumption_set, run.assumption_set
        )
        res_data = _run_result(run)
        groups.append(
            {
                "label": label,
                "store_id": run.store_id,
                "run": run,
                "variants": _extract_variants(res_data),
                "year": run.year,
                "calendar_mode": run.calendar_mode,
            }
        )
    return groups, selected, None


def _compare_options(
    session: Session,
    stores: list[dict[str, str]],
) -> list[dict[str, str]]:
    scenario_repo = ScenarioRepository(session)
    options: list[dict[str, str]] = [{"value": "", "label": "（選ばない）"}]
    for store in stores:
        store_id = store["store_id"]
        store_name = store["name"]
        options.append(
            {"value": f"{store_id}:pessimistic", "label": f"{store_name} 悲観"}
        )
        options.append({"value": f"{store_id}:standard", "label": f"{store_name} 標準"})
        options.append(
            {"value": f"{store_id}:optimistic", "label": f"{store_name} 楽観"}
        )
        for sc in scenario_repo.list(store_id):
            options.append(
                {
                    "value": f"{store_id}:scenario:{sc.id}",
                    "label": f"{store_name} シナリオ「{sc.name}」",
                }
            )
    return options


@router.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    items: str | None = None,
    year: int = 2026,
    calendar: str = "steady",
    runs: str | None = None,
    session: Session = Depends(get_session),
) -> Response:
    if not items and "item" in request.query_params:
        raw_items = request.query_params.getlist("item")
        chosen = [t.strip() for t in raw_items if t.strip()]
        if chosen:
            return RedirectResponse(
                url=f"/compare?items={','.join(chosen)}&year={year}&calendar={calendar}",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/compare?year={year}&calendar={calendar}",
            status_code=303,
        )

    repository = RunRepository(session)
    error = None
    groups: list[dict[str, Any]] = []
    selected: list[Run] = []

    if items:
        groups, selected, error = _load_compare_items(session, items, year, calendar)
    elif runs:
        groups, selected, error = _load_compare_runs(repository, session, runs)

    rows = (
        [_active_variant(_run_result(run)) for run in selected]
        if selected and not groups
        else []
    )
    recent = (
        repository.list_recent(20) if not selected and not items and not runs else []
    )
    stores = list_stores(data_directory())
    compare_options = _compare_options(session, stores)
    current_tokens = [t.strip() for t in items.split(",") if t.strip()] if items else []
    selected_items = [
        current_tokens[i] if i < len(current_tokens) else "" for i in range(4)
    ]
    return _render(
        request,
        "compare.html",
        selected=selected,
        groups=groups,
        rows=rows,
        error=error,
        items_query=items or "",
        runs_query=runs or "",
        year=year,
        calendar=calendar,
        recent=recent,
        stores=stores,
        compare_options=compare_options,
        selected_items=selected_items,
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
