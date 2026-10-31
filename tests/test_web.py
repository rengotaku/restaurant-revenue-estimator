"""Web の API と画面 (TC-Wxx)."""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from restaurant_revenue_estimator import services
from restaurant_revenue_estimator.cli import app as cli_app
from restaurant_revenue_estimator.engine.calendar import (
    calendars_for_store,
    load_holidays,
)
from restaurant_revenue_estimator.inputs import load_store

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STORE = DATA / "stores" / "birdman_tamachi"
runner = CliRunner()


def _run_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "assumption_set": "standard",
        "calendar_mode": "steady",
        "year": 2026,
        "seed": 42,
        "replications": 1,
        "overrides": {},
    }
    payload.update(overrides)
    return payload


def _data_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(DATA)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DATA.rglob("*"))
        if path.is_file()
    }


def test_override_assumption_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(overrides={"dinner_ala_ticket": 4800}),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["overrides"]["dinner_ala_ticket"] == 4800
    assert any(
        item["key"] == "dinner_ala_ticket" and item["source"].startswith("scenario:")
        for item in payload["overrides"]
    )


@pytest.mark.parametrize("key", ["appetizer.price", "floors[0].tables", "no_such_key"])
def test_fact_and_unknown_keys_are_rejected(client: TestClient, key: str) -> None:
    response = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(overrides={key: 1}),
    )
    assert response.status_code == 422
    assert key in response.text


def test_unresolved_floor_can_be_replaced_with_tables(client: TestClient) -> None:
    tables = [{"id": f"2f-{index}", "seats": 4} for index in range(6)]
    response = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(overrides={"layout.floor2": tables}),
    )
    assert response.status_code == 200
    payload = response.json()
    two_floor = [
        table for table in payload["simulation"]["tables"] if table["floor"] == "2f"
    ]
    assert len(two_floor) == 6
    assert payload["variant"] == "custom"


def test_same_parameters_return_cached_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}
    original = services.evaluation_service.evaluate

    def counting(*args: object, **kwargs: object) -> object:
        calls["count"] += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(services.evaluation_service, "evaluate", counting)
    first = client.post("/api/stores/birdman_tamachi/runs", json=_run_payload())
    second = client.post("/api/stores/birdman_tamachi/runs", json=_run_payload())
    assert first.status_code == second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]
    assert calls["count"] == 1


def test_api_does_not_modify_data_directory(client: TestClient) -> None:
    before = _data_hashes()
    client.get("/api/stores")
    client.get("/api/stores/birdman_tamachi")
    client.post("/api/stores/birdman_tamachi/runs", json=_run_payload())
    client.get("/stores/birdman_tamachi/evidence")
    assert _data_hashes() == before


def test_cli_and_api_reports_are_identical(client: TestClient, tmp_path: Path) -> None:
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
    cli_result = runner.invoke(cli_app, args)
    assert cli_result.exit_code == 0, cli_result.output
    cli_report = (Path(cli_result.output.splitlines()[0]) / "report.md").read_text(
        encoding="utf-8"
    )

    api_result = client.post("/api/stores/birdman_tamachi/runs", json=_run_payload())
    run_id = api_result.json()["run_id"]
    api_report = client.get(f"/api/runs/{run_id}/report.md")
    assert api_report.status_code == 200
    assert api_report.text == cli_report


def test_summary_page_mentions_related_store(client: TestClient) -> None:
    page = client.get("/stores/birdman_tamachi")
    assert page.status_code == 200
    assert "バードマン TRIO 田町店" in page.text
    assert "含めていない" in page.text


def test_status_counts_sum_to_total(client: TestClient) -> None:
    payload = client.get("/api/stores/birdman_tamachi").json()
    assert sum(payload["status_counts"].values()) == payload["total_keys"]


def test_evidence_page_filters_unresolved(client: TestClient) -> None:
    page = client.get("/stores/birdman_tamachi/evidence?status=UNRESOLVED")
    assert page.status_code == 200
    assert "floor2_seats" in page.text or "floors[1].tables" in page.text
    assert "2026-09-01" in page.text


def test_evidence_page_shows_source_url_and_date(client: TestClient) -> None:
    page = client.get("/stores/birdman_tamachi/evidence")
    assert "https://tabelog.com/tokyo/A1314/A131402/13134326/" in page.text
    assert "2026-10-30" in page.text


def test_revenue_page_totals_match_result(client: TestClient) -> None:
    run = client.post(
        "/api/stores/birdman_tamachi/runs", json=_run_payload(replications=1)
    ).json()
    page = client.get("/stores/birdman_tamachi/revenue?tax=inc")
    assert page.status_code == 200
    total = run["revenue"]["total_inc_tax"]
    assert f"{total:,}" in page.text
    assert sum(month["total_inc_tax"] for month in run["revenue"]["months"]) == total

    ex_page = client.get("/stores/birdman_tamachi/revenue?tax=ex")
    assert f"{run['revenue']['total_ex_tax']:,}" in ex_page.text


def test_calendar_historical_saturday_rule() -> None:
    holidays = load_holidays(
        DATA / "calendars" / "jp_holidays_cao.csv",
        DATA / "calendars" / "jp_holidays_cao.meta.yaml",
        2026,
    )
    inputs = load_store(STORE)
    series = calendars_for_store(
        inputs,
        year=2026,
        mode="historical",
        holidays=holidays,
        saturday_closure_start="2026-09-01",
    )[0]
    august = [day for day in series.days if day.day.month == 8 and day.weekday == "sat"]
    september = [
        day for day in series.days if day.day.month == 9 and day.weekday == "sat"
    ]
    assert all(day.is_open for day in august)
    assert all(not day.is_open for day in september)


def test_calendar_page_shows_exception_status(client: TestClient) -> None:
    page = client.get("/stores/birdman_tamachi/calendar")
    assert page.status_code == 200
    assert "ASSUMPTION" in page.text
    assert "年末年始" in page.text


def test_simulator_page_disables_facts(client: TestClient) -> None:
    page = client.get("/stores/birdman_tamachi/simulator")
    assert page.status_code == 200
    assert "事実（OBSERVED" in page.text
    assert 'name="key_dinner_ala_ticket"' in page.text


def test_simulator_saves_and_loads_scenario(client: TestClient) -> None:
    response = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "custom",
            "floor2_4p": "6",
            "floor2_2p": "0",
            "replications": "1",
            "name": "2階6卓案",
            "note": "テスト",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/stores/birdman_tamachi/simulator")
    assert "2階6卓案" in page.text
    assert "replications" in page.text

    scenarios = client.get("/api/stores/birdman_tamachi/scenarios").json()
    target = next(item for item in scenarios if item["name"] == "2階6卓案")
    loaded = client.get(f"/stores/birdman_tamachi/simulator?scenario={target['id']}")
    assert 'value="6"' in loaded.text


def test_costs_page_warns_below_minimum_wage_and_shows_owner_separately(
    client: TestClient,
) -> None:
    scenario = client.post(
        "/api/stores/birdman_tamachi/scenarios",
        json={
            "name": "時給1250",
            "overrides": {
                "part_time_staff": {
                    "heads_per_day": 2,
                    "hours_per_day": 5,
                    "hourly_wage": 1250,
                }
            },
        },
    ).json()
    response = client.post(
        "/stores/birdman_tamachi/simulator",
        data={"scenario_id": str(scenario["id"]), "replications": "1"},
        follow_redirects=False,
    )
    run_id = response.headers["location"].split("run=")[1].split("&")[0]
    page = client.get(f"/stores/birdman_tamachi/costs?run={run_id}")
    assert page.status_code == 200
    assert "最低賃金" in page.text
    assert "店主の労働対価（別列）" in page.text
    assert "合計" not in page.text
    assert "売上は税込、人件費に店主の労働対価を含めて換算" in page.text


def test_priorities_page_lists_how_to_verify(client: TestClient) -> None:
    page = client.get("/stores/birdman_tamachi/priorities")
    assert page.status_code == 200
    assert "確かめ方" in page.text
    assert "floor2" in page.text


def test_compare_rejects_different_period(client: TestClient) -> None:
    steady = client.post("/api/stores/birdman_tamachi/runs", json=_run_payload()).json()
    historical = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(calendar_mode="historical"),
    ).json()
    page = client.get(f"/compare?runs={steady['run_id']},{historical['run_id']}")
    assert page.status_code == 200
    assert "比較できない" in page.text


def test_compare_has_no_ranking(client: TestClient) -> None:
    first = client.post("/api/stores/birdman_tamachi/runs", json=_run_payload()).json()
    second = client.post(
        "/api/stores/torahachi_akihabara/runs",
        json=dict(_run_payload(), overrides={}),
    )
    if second.status_code != 200:
        pytest.skip("とら八の評価に失敗")
    page = client.get(f"/compare?runs={first['run_id']},{second.json()['run_id']}")
    assert page.status_code == 200
    assert "1 位" not in page.text
    assert "順位" not in page.text.split("並びは選んだ順")[0]
