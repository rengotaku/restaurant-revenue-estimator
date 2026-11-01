"""TC-F1-01 〜 TC-F1-11: シミュレーターの標準評価・?run=・帯・2階・保存の事前設計テスト.

Issue #27 のテストケース仕様に準拠.
"""

import re

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from restaurant_revenue_estimator.models import Run, Scenario
from restaurant_revenue_estimator.repositories.scenario_repo import RunRepository


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


def _run_id_from_location(location: str) -> int:
    match = re.search(r"[?&]run=(\d+)", location)
    assert match is not None, f"run id not found in Location: {location}"
    return int(match.group(1))


def test_tc_f1_01_default_form_does_not_override_floor2(client: TestClient) -> None:
    """TC-F1-01: 既定値で 2 階が空になる（RV-1）を検知."""
    res = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "",
            "floor2_4p": "",
            "floor2_2p": "",
            "replications": "1",
            "name": "",
            "note": "",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    run_id = _run_id_from_location(res.headers["location"])
    run_payload = client.get(f"/api/runs/{run_id}").json()
    assert "layout.floor2" not in run_payload.get("meta", {}).get("overrides", {})


def test_tc_f1_02_empty_choice_ignores_zero_inputs(client: TestClient) -> None:
    """TC-F1-02: 旧フォームの値（0）が残っていても空にならないことを検知."""
    res = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "",
            "floor2_4p": "0",
            "floor2_2p": "0",
            "replications": "1",
            "name": "",
            "note": "",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    run_id = _run_id_from_location(res.headers["location"])
    run_payload = client.get(f"/api/runs/{run_id}").json()
    assert "layout.floor2" not in run_payload.get("meta", {}).get("overrides", {})


def test_tc_f1_03_custom_choice_requires_at_least_one_table(
    client: TestClient, session: Session
) -> None:
    """TC-F1-03: 自由入力で 0 卓を実行してしまうことを検知."""
    before_count = len(session.exec(select(Run)).all())
    res = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "custom",
            "floor2_4p": "",
            "floor2_2p": "",
            "replications": "1",
            "name": "",
            "note": "",
        },
        follow_redirects=False,
    )
    after_count = len(session.exec(select(Run)).all())
    assert after_count == before_count
    assert "1 卓以上" in res.text


def test_tc_f1_04_candidate_choice_sets_floor2_override(client: TestClient) -> None:
    """TC-F1-04: 候補（f2_12）を選べないことを検知."""
    res = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "f2_12",
            "replications": "1",
            "name": "",
            "note": "",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    run_id = _run_id_from_location(res.headers["location"])
    run_payload = client.get(f"/api/runs/{run_id}").json()
    assert (
        run_payload.get("meta", {}).get("overrides", {}).get("layout.floor2") == "f2_12"
    )
    floor2_tables = [
        t
        for t in run_payload["variants"][0].get("simulation", {}).get("tables", [])
        if t.get("floor") == "2f"
    ]
    assert len(floor2_tables) == 4


def test_tc_f1_05_unsaved_submission_shows_unsaved_estimate(client: TestClient) -> None:
    """TC-F1-05: 偽の「保存しました」（RV-1）を検知."""
    response = client.post(
        "/stores/birdman_tamachi/simulator",
        data={"name": "", "replications": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "saved=1" not in location
    page = client.get(location)
    assert page.status_code == 200
    assert "保存しました" not in page.text
    assert "未保存の試算" in page.text
    scenarios = client.get("/api/stores/birdman_tamachi/scenarios").json()
    assert len(scenarios) == 0


def test_tc_f1_06_saved_submission_shows_saved_and_persists_scenario(
    client: TestClient,
) -> None:
    """TC-F1-06: 保存の表示が出ないことを検知."""
    response = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "name": "2階6卓案",
            "floor2_choice": "custom",
            "floor2_4p": "6",
            "floor2_2p": "0",
            "replications": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "saved=1" in location
    page = client.get(location)
    assert page.status_code == 200
    assert "保存しました" in page.text
    scenarios = client.get("/api/stores/birdman_tamachi/scenarios").json()
    assert len(scenarios) == 1
    assert scenarios[0]["name"] == "2階6卓案"


def test_tc_f1_07_default_pages_show_latest_default_run(client: TestClient) -> None:
    """TC-F1-07: 試算が標準の評価として出る（RV-1）を検知."""
    std_res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(replications=1),
    )
    assert std_res.status_code == 200
    std_total = std_res.json()["variants"][0]["revenue"]["total_inc_tax"]

    sim_res = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "key_dinner_ala_ticket": "5000",
            "replications": "1",
            "name": "",
            "note": "",
        },
        follow_redirects=False,
    )
    assert sim_res.status_code == 303
    sim_run_id = _run_id_from_location(sim_res.headers["location"])
    over_res = client.get(f"/api/runs/{sim_run_id}")
    assert over_res.status_code == 200
    over_total = over_res.json()["variants"][0]["revenue"]["total_inc_tax"]

    # 2つの値が同じにならないこと（上書きで売上が変わること）を先に assert
    assert std_total != over_total

    for path in [
        "/stores/birdman_tamachi",
        "/stores/birdman_tamachi/revenue",
        "/stores/birdman_tamachi/costs",
        "/",
    ]:
        page = client.get(path)
        assert page.status_code == 200
        assert f"{std_total:,}" in page.text
        assert f"{over_total:,}" not in page.text


def test_tc_f1_08_run_param_shows_specified_run_and_unsaved_banner(
    client: TestClient,
) -> None:
    """TC-F1-08: ?run= で開けないことを検知."""
    std_res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(replications=1),
    )
    assert std_res.status_code == 200

    sim_res = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "key_dinner_ala_ticket": "5000",
            "replications": "1",
            "name": "",
            "note": "",
        },
        follow_redirects=False,
    )
    assert sim_res.status_code == 303
    sim_run_id = _run_id_from_location(sim_res.headers["location"])
    over_res = client.get(f"/api/runs/{sim_run_id}")
    assert over_res.status_code == 200
    over_total = over_res.json()["variants"][0]["revenue"]["total_inc_tax"]

    page = client.get(f"/stores/birdman_tamachi/revenue?run={sim_run_id}")
    assert page.status_code == 200
    assert f"{over_total:,}" in page.text
    assert "未保存の試算" in page.text
    assert "dinner_ala_ticket" in page.text


def test_tc_f1_09_scenario_run_shows_scenario_name_in_banner(
    client: TestClient,
) -> None:
    """TC-F1-09: シナリオ名が出ないことを検知."""
    sc_res = client.post(
        "/api/stores/birdman_tamachi/scenarios",
        json={
            "name": "夜単価5000",
            "overrides": {"dinner_ala_ticket": 5000},
        },
    )
    assert sc_res.status_code == 201
    scenario_id = sc_res.json()["id"]

    run_res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(scenario_id=scenario_id, replications=1),
    )
    assert run_res.status_code == 200
    run_id = run_res.json()["run_id"]

    page = client.get(f"/stores/birdman_tamachi/costs?run={run_id}")
    assert page.status_code == 200
    assert "シナリオ『夜単価5000』の試算" in page.text


def test_tc_f1_10_automatic_run_uses_20_replications_and_standard_assumption(
    client: TestClient, session: Session
) -> None:
    """TC-F1-10: 自動実行の replications が 2 であることを検知."""
    page = client.get("/stores/birdman_tamachi")
    assert page.status_code == 200

    run = RunRepository(session).latest("birdman_tamachi")
    assert run is not None
    assert run.replications == 20
    assert run.assumption_set == "standard"


def test_tc_f1_11_run_from_other_store_returns_404(client: TestClient) -> None:
    """TC-F1-11: 別店舗の実行を表示してしまうことを検知."""
    t_run = client.post(
        "/api/stores/torahachi_akihabara/runs",
        json=_run_payload(replications=1),
    )
    assert t_run.status_code == 200
    t_run_id = t_run.json()["run_id"]

    page = client.get(f"/stores/birdman_tamachi/revenue?run={t_run_id}")
    assert page.status_code == 404


def test_tc_f1_12_simulator_without_run_omits_unsaved_estimate(
    client: TestClient,
) -> None:
    """?run なしのシミュレーター画面は標準の評価を出し「未保存の試算」を表示しない."""
    res = client.get("/stores/birdman_tamachi/simulator")
    assert res.status_code == 200
    assert "未保存の試算" not in res.text

    client.post(
        "/stores/birdman_tamachi/simulator",
        data={"name": "", "replications": "1"},
        follow_redirects=False,
    )
    res_after = client.get("/stores/birdman_tamachi/simulator")
    assert res_after.status_code == 200
    assert "未保存の試算" not in res_after.text


def test_tc_f1_13_scenario_run_reexecution_updates_scenario_id_and_banner(
    client: TestClient, session: Session
) -> None:
    """(a) 未保存の試算 → 同じ設定で名前 A → 帯 A・run.scenario_id が A.

    続けて名前 B → 帯 B.
    """
    # 1. 未保存の試算
    res_unsaved = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "key_dinner_ala_ticket": "5000",
            "replications": "1",
            "name": "",
        },
        follow_redirects=False,
    )
    assert res_unsaved.status_code == 303
    run_unsaved_id = _run_id_from_location(res_unsaved.headers["location"])
    run_unsaved = RunRepository(session).get(run_unsaved_id)
    assert run_unsaved is not None
    assert run_unsaved.scenario_id is None

    # 2. 同じ設定で名前 A
    res_a = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "key_dinner_ala_ticket": "5000",
            "replications": "1",
            "name": "案A",
        },
        follow_redirects=False,
    )
    assert res_a.status_code == 303
    run_a_id = _run_id_from_location(res_a.headers["location"])
    run_a = RunRepository(session).get(run_a_id)
    assert run_a is not None
    assert run_a.scenario_id is not None
    scenario_a = session.get(Scenario, run_a.scenario_id)
    assert scenario_a is not None and scenario_a.name == "案A"

    page_a = client.get(f"/stores/birdman_tamachi/simulator?run={run_a_id}")
    assert page_a.status_code == 200
    assert "シナリオ『案A』の試算" in page_a.text

    # 3. 続けて同じ設定で名前 B
    res_b = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "key_dinner_ala_ticket": "5000",
            "replications": "1",
            "name": "案B",
        },
        follow_redirects=False,
    )
    assert res_b.status_code == 303
    run_b_id = _run_id_from_location(res_b.headers["location"])
    run_b = RunRepository(session).get(run_b_id)
    assert run_b is not None
    assert run_b.scenario_id is not None
    assert run_b.scenario_id != run_a.scenario_id
    scenario_b = session.get(Scenario, run_b.scenario_id)
    assert scenario_b is not None and scenario_b.name == "案B"

    page_b = client.get(f"/stores/birdman_tamachi/simulator?run={run_b_id}")
    assert page_b.status_code == 200
    assert "シナリオ『案B』の試算" in page_b.text


def test_tc_f1_14_default_run_not_polluted_by_standard_scenario(
    client: TestClient, session: Session
) -> None:
    """(b) 上書きなしシナリオを replications 20・seed 42 で実行.

    実行後 GET で scenario_id なしの run が作られる.
    """
    sc_res = client.post(
        "/api/stores/birdman_tamachi/scenarios",
        json={"name": "標準同等シナリオ", "overrides": {}},
    )
    assert sc_res.status_code == 201
    sc_id = sc_res.json()["id"]

    run_res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json=_run_payload(scenario_id=sc_id, replications=20, seed=42),
    )
    assert run_res.status_code == 200
    sc_run_id = run_res.json()["run_id"]

    page = client.get("/stores/birdman_tamachi")
    assert page.status_code == 200
    assert "シナリオ『標準同等シナリオ』の試算" not in page.text

    default_run = RunRepository(session).latest_default("birdman_tamachi")
    assert default_run is not None
    assert default_run.scenario_id is None
    assert default_run.id != sc_run_id


def test_tc_f1_15_custom_floor2_invalid_table_counts_rejected(
    client: TestClient, session: Session
) -> None:
    """(c) custom で floor2_4p=-1 と floor2_4p=abc → run が増えず 200 でエラー文言."""
    before_runs = len(session.exec(select(Run)).all())
    before_scenarios = len(session.exec(select(Scenario)).all())

    # 1. 負の数 (-1)
    res_neg = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "custom",
            "floor2_4p": "-1",
            "floor2_2p": "2",
            "replications": "1",
            "name": "不正案1",
        },
        follow_redirects=False,
    )
    assert res_neg.status_code == 200
    assert len(session.exec(select(Run)).all()) == before_runs
    assert len(session.exec(select(Scenario)).all()) == before_scenarios
    assert "1 卓以上" in res_neg.text

    # 2. 数字以外 (abc)
    res_abc = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "custom",
            "floor2_4p": "abc",
            "floor2_2p": "2",
            "replications": "1",
            "name": "不正案2",
        },
        follow_redirects=False,
    )
    assert res_abc.status_code == 200
    assert len(session.exec(select(Run)).all()) == before_runs
    assert len(session.exec(select(Scenario)).all()) == before_scenarios
    assert "1 卓以上" in res_abc.text

    # 3. 小数 (1.5)
    res_dec = client.post(
        "/stores/birdman_tamachi/simulator",
        data={
            "floor2_choice": "custom",
            "floor2_4p": "1.5",
            "floor2_2p": "2",
            "replications": "1",
            "name": "不正案3",
        },
        follow_redirects=False,
    )
    assert res_dec.status_code == 200
    assert len(session.exec(select(Run)).all()) == before_runs
    assert len(session.exec(select(Scenario)).all()) == before_scenarios
    assert "1 卓以上" in res_dec.text


def test_tc_f1_16_revenue_tax_switch_preserves_run_parameter(
    client: TestClient,
) -> None:
    """(d) revenue?run=<id> の税抜リンクに run=<id> があり、?tax=ex で税抜合計が出る."""
    res = client.post(
        "/stores/birdman_tamachi/simulator",
        data={"key_dinner_ala_ticket": "5000", "replications": "1"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    run_id = _run_id_from_location(res.headers["location"])
    run_payload = client.get(f"/api/runs/{run_id}").json()
    total_ex = run_payload["variants"][0]["revenue"]["total_ex_tax"]

    # 税込画面を開く
    page_inc = client.get(f"/stores/birdman_tamachi/revenue?run={run_id}")
    assert page_inc.status_code == 200
    assert f"run={run_id}&tax=ex" in page_inc.text

    # 税抜リンクを開く
    page_ex = client.get(f"/stores/birdman_tamachi/revenue?run={run_id}&tax=ex")
    assert page_ex.status_code == 200
    assert "未保存の試算" in page_ex.text
    assert f"{total_ex:,}" in page_ex.text
