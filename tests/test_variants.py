"""候補（variant）の直積と 3 シナリオ評価のテスト (STEP1a)."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from typer.testing import CliRunner

from restaurant_revenue_estimator.cli import app
from restaurant_revenue_estimator.engine.scenario import (
    scenario as scenario_module,
)
from restaurant_revenue_estimator.engine.scenario import (
    sensitivity,
)
from restaurant_revenue_estimator.models import Run
from restaurant_revenue_estimator.report import result_to_dict
from restaurant_revenue_estimator.repositories.scenario_repo import RunRepository
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    EvaluationResult,
    evaluate,
    evaluate_run,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def test_evaluate_run_steady_variants_and_scenarios() -> None:
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        calendar_mode="steady",
        replications=1,
    )
    doc = evaluate_run(DATA, req)

    variant_names = [v["name"] for v in doc.variants]
    assert variant_names == ["f2_42", "f2_12", "f2_22"]
    assert len(doc.variants) == 3

    for v in doc.variants:
        assert v["choices"] == {"layout.floor2": v["name"]}
        assert "revenue" in v
        assert "pnl" in v
        assert "simulation" in v

    # 3 シナリオ × 3 variant = 9 件
    assert len(doc.scenarios) == 9
    scenario_sets = {s["assumption_set"] for s in doc.scenarios}
    assert scenario_sets == {"pessimistic", "standard", "optimistic"}

    scenario_variants = {s["variant"] for s in doc.scenarios}
    assert scenario_variants == {"f2_42", "f2_12", "f2_22"}

    for s in doc.scenarios:
        assert isinstance(s["gross_inc_tax"], int)
        assert isinstance(s["gross_ex_tax"], int)
        assert isinstance(s["business_income"], int)


def test_evaluate_run_historical_variants_and_scenarios() -> None:
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        calendar_mode="historical",
        replications=1,
    )
    doc = evaluate_run(DATA, req)

    # 3 (2階) × 2 (土曜定休開始日) × 2 (土曜営業時間) = 12 通り
    assert len(doc.variants) == 12
    variant_names = [v["name"] for v in doc.variants]
    assert len(set(variant_names)) == 12

    for v in doc.variants:
        choices = v["choices"]
        assert set(choices.keys()) == {
            "layout.floor2",
            "saturday_closure_start",
            "saturday_hours_before_closure",
        }
        assert choices["layout.floor2"] in ("f2_42", "f2_12", "f2_22")
        assert choices["saturday_closure_start"] in ("2025-09-01", "2026-09-01")
        assert choices["saturday_hours_before_closure"] in (
            "same_as_weekday",
            "dinner_only",
        )
        assert v["name"] == (
            f"{choices['layout.floor2']}/"
            f"{choices['saturday_closure_start']}/"
            f"{choices['saturday_hours_before_closure']}"
        )

    # 3 シナリオ × 12 variant = 36 件
    assert len(doc.scenarios) == 36


def test_evaluate_run_override_floor2_variants_and_scenarios() -> None:
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        calendar_mode="steady",
        replications=1,
        overrides={"layout.floor2": "f2_12"},
    )
    doc = evaluate_run(DATA, req)

    assert len(doc.variants) == 1
    assert doc.variants[0]["name"] == "f2_12"
    assert doc.variants[0]["choices"] == {"layout.floor2": "f2_12"}

    # 3 シナリオ × 1 variant = 3 件
    assert len(doc.scenarios) == 3


def test_evaluate_run_to_dict_structure() -> None:
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        calendar_mode="steady",
        replications=1,
    )
    doc = evaluate_run(DATA, req)
    data = doc.to_dict()

    expected_keys = {
        "store_id",
        "year",
        "calendar_mode",
        "assumption_set",
        "meta",
        "overrides",
        "unresolved",
        "variants",
        "scenarios",
    }
    assert expected_keys.issubset(data.keys())
    assert isinstance(data["variants"], list)
    assert isinstance(data["scenarios"], list)
    assert len(data["variants"]) == 3
    assert data["variants"][0]["name"] == "f2_42"


def test_tc_f6_01(client: TestClient) -> None:
    res = client.post("/api/stores/birdman_tamachi/runs", json={"replications": 1})
    assert res.status_code == 200
    payload = res.json()
    assert "variants" in payload
    variants = payload["variants"]
    assert [v["name"] for v in variants] == ["f2_42", "f2_12", "f2_22"]
    for v in variants:
        assert "revenue" in v
        assert "pnl" in v
    totals = [v["revenue"]["total_inc_tax"] for v in variants]
    assert len(set(totals)) > 1


def test_tc_f6_02(client: TestClient) -> None:
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1, "calendar_mode": "historical"},
    )
    assert res.status_code == 200
    payload = res.json()
    assert "variants" in payload
    variants = payload["variants"]
    assert len(variants) == 12
    choices_list = [v["choices"] for v in variants]
    for c in choices_list:
        assert set(c.keys()) == {
            "layout.floor2",
            "saturday_closure_start",
            "saturday_hours_before_closure",
        }
    choices_tuples = [
        (
            c["layout.floor2"],
            c["saturday_closure_start"],
            c["saturday_hours_before_closure"],
        )
        for c in choices_list
    ]
    assert len(set(choices_tuples)) == 12


def test_tc_f6_03(client: TestClient) -> None:
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1, "overrides": {"layout.floor2": "f2_12"}},
    )
    assert res.status_code == 200
    payload = res.json()
    assert "variants" in payload
    variants = payload["variants"]
    assert len(variants) == 1
    assert variants[0]["name"] == "f2_12"


def test_tc_f6_04(client: TestClient) -> None:
    res = client.post("/api/stores/birdman_tamachi/runs", json={"replications": 1})
    assert res.status_code == 200
    payload = res.json()
    assert "scenarios" in payload
    scenarios = payload["scenarios"]
    assert len(scenarios) == 9

    by_variant: dict[str, dict[str, dict[str, Any]]] = {}
    for item in scenarios:
        by_variant.setdefault(item["variant"], {})[item["assumption_set"]] = item

    for _var_name, sc_map in by_variant.items():
        assert (
            "pessimistic" in sc_map and "standard" in sc_map and "optimistic" in sc_map
        )
        p = sc_map["pessimistic"]
        s = sc_map["standard"]
        o = sc_map["optimistic"]
        assert p["gross_inc_tax"] <= s["gross_inc_tax"] <= o["gross_inc_tax"]
        assert p["business_income"] <= s["business_income"] <= o["business_income"]


def test_sensitivity_evaluates_single_variant_per_row_historical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[dict[str, Any] | None, int]] = []
    original_evaluate = scenario_module.evaluate

    def recording_evaluate(
        data_dir: Path, request: EvaluationRequest
    ) -> list[EvaluationResult]:
        results = original_evaluate(data_dir, request)
        recorded.append((request.overrides, len(results)))
        return results

    monkeypatch.setattr(
        "restaurant_revenue_estimator.engine.scenario.scenario.evaluate",
        recording_evaluate,
    )

    req = EvaluationRequest(
        store_id="birdman_tamachi",
        calendar_mode="historical",
        replications=1,
    )
    sensitivity(DATA, req)

    assert len(recorded) > 1, "評価が実行されていること"
    remaining = [
        item
        for item in recorded[1:]
        if not (item[0] and set(item[0].keys()) == {"layout.floor2"})
    ]
    assert remaining, "行ごとの評価が記録されていること"
    assert all(count == 1 for _, count in remaining), (
        "全軸の固定により行ごとの件数は 1 であるべき: "
        f"{[c for _, c in remaining if c != 1]}"
    )


def test_tc_f6_05_cli_run_variants_and_report(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "data/stores/birdman_tamachi",
            "--replications",
            "1",
            "--runs-dir",
            str(tmp_path),
            "--data-dir",
            str(DATA),
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = Path(result.output.splitlines()[0])
    result_data = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert len(result_data["variants"]) == 3
    assert len(result_data["scenarios"]) == 9

    report_text = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## 3. 年間・月別の売上" in report_text
    sec3 = report_text.split("## 3. 年間・月別の売上")[1].split("## 4.")[0]
    for v_name in ("f2_42", "f2_12", "f2_22"):
        assert v_name in sec3
    for s_name in ("悲観", "標準", "楽観"):
        assert s_name in sec3


def test_tc_f6_06_report_sales_status(client: TestClient) -> None:
    # 上書きなしの実行 (TC-F6-01)
    res1 = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res1.status_code == 200, res1.text
    run1 = res1.json()
    run_id1 = run1["run_id"]
    rep1 = client.get(f"/api/runs/{run_id1}/report.md")
    assert rep1.status_code == 200, rep1.text
    assert (
        "ステータス: 売上は DERIVED（元入力のうち最も弱いのは UNRESOLVED）。"
        in rep1.text
    )

    # overrides ありの実行
    res2 = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={
            "replications": 1,
            "overrides": {"layout.floor2": "f2_12"},
        },
    )
    assert res2.status_code == 200, res2.text
    run2 = res2.json()
    run_id2 = run2["run_id"]
    rep2 = client.get(f"/api/runs/{run_id2}/report.md")
    assert rep2.status_code == 200, rep2.text
    assert (
        "ステータス: 売上は DERIVED（元入力のうち最も弱いのは ASSUMPTION）。"
        in rep2.text
    )


def test_tc_f6_07(client: TestClient) -> None:
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    variants = payload["variants"]
    assert len(variants) == 3

    page = client.get("/stores/birdman_tamachi")
    assert page.status_code == 200, page.text
    for v in variants:
        inc = f"{v['revenue']['total_inc_tax']:,}"
        income = f"{v['pnl']['business_income']:,}"
        assert inc in page.text, (
            f"{v['name']} の年間税込売上 {inc} がサマリーにあること"
        )
        assert income in page.text, (
            f"{v['name']} の事業所得相当 {income} がサマリーにあること"
        )


def test_tc_f6_08(client: TestClient) -> None:
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    f2_12_v = next(v for v in payload["variants"] if v["name"] == "f2_12")
    f2_12_inc = f"{f2_12_v['revenue']['total_inc_tax']:,}"

    page = client.get("/stores/birdman_tamachi/revenue?variant=f2_12")
    assert page.status_code == 200, page.text
    assert f2_12_inc in page.text, f"f2_12 の年間税込売上 {f2_12_inc} があること"
    assert "variant=f2_42" in page.text, "variant=f2_42 へのリンクがあること"
    assert "variant=f2_22" in page.text, "variant=f2_22 へのリンクがあること"


def test_tc_f6_09(client: TestClient) -> None:
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    scenarios = [s for s in payload["scenarios"] if s["variant"] == "f2_42"]
    assert len(scenarios) == 3

    page = client.get("/stores/birdman_tamachi/revenue?variant=f2_42")
    assert page.status_code == 200, page.text
    for label in ("悲観", "標準", "楽観"):
        assert label in page.text, f"{label} がページにあること"
    for sc in scenarios:
        inc = f"{sc['gross_inc_tax']:,}"
        assert inc in page.text, (
            f"f2_42 の {sc['assumption_set']} 年間税込売上 {inc} があること"
        )


def test_tc_f6_10(client: TestClient) -> None:
    # 保存済みシナリオ「夜単価5000」を作る
    res = client.post(
        "/api/stores/birdman_tamachi/scenarios",
        json={"name": "夜単価5000", "overrides": {"dinner_ala_ticket": 5000}},
    )
    assert res.status_code in (200, 201), res.text
    scenario = res.json()
    scenario_id = scenario["id"]

    url = (
        f"/compare?items=birdman_tamachi:optimistic,birdman_tamachi:scenario:{scenario_id}"
        "&year=2026&calendar=steady"
    )
    page = client.get(url)
    assert page.status_code == 200, page.text

    # 結果の表の部分だけで比べる（ドロップダウンの選択肢を除外するため）
    assert 'id="compare-results"' in page.text
    results_html = page.text.split('id="compare-results"')[1]
    idx_opt = results_html.find("楽観")
    idx_sc = results_html.find("夜単価5000")
    assert idx_opt != -1, "楽観が結果の表にあること"
    assert idx_sc != -1, "夜単価5000が結果の表にあること"
    assert idx_opt < idx_sc, "選んだ順（楽観 → 夜単価5000）に並ぶこと"

    # 「1 位」「順位」が無い（注記の一文を除いて検証）
    assert "1 位" not in page.text
    text_without_note = page.text.replace(
        "並びは選んだ順。売上順の並べ替えや順位は付けない。", ""
    )
    assert "順位" not in text_without_note


def test_tc_f6_10_reverse_order(client: TestClient) -> None:
    # 保存済みシナリオ「夜単価5000」を作る
    res = client.post(
        "/api/stores/birdman_tamachi/scenarios",
        json={"name": "夜単価5000", "overrides": {"dinner_ala_ticket": 5000}},
    )
    assert res.status_code in (200, 201), res.text
    scenario = res.json()
    scenario_id = scenario["id"]

    # 逆順（シナリオ → 楽観）で指定
    url = (
        f"/compare?items=birdman_tamachi:scenario:{scenario_id},birdman_tamachi:optimistic"
        "&year=2026&calendar=steady"
    )
    page = client.get(url)
    assert page.status_code == 200, page.text

    # 結果の表の部分だけで比べる（ドロップダウンの選択肢を除外するため）
    assert 'id="compare-results"' in page.text
    results_html = page.text.split('id="compare-results"')[1]
    idx_sc = results_html.find("夜単価5000")
    idx_opt = results_html.find("楽観")
    assert idx_sc != -1, "夜単価5000が結果の表にあること"
    assert idx_opt != -1, "楽観が結果の表にあること"
    assert idx_sc < idx_opt, "逆順（夜単価5000 → 楽観）に並ぶこと"

    # 「1 位」「順位」が無い（注記の一文を除いて検証）
    assert "1 位" not in page.text
    text_without_note = page.text.replace(
        "並びは選んだ順。売上順の並べ替えや順位は付けない。", ""
    )
    assert "順位" not in text_without_note


def test_tc_f6_10_form_submission(client: TestClient) -> None:
    # フォームから item を送信して /compare?items=... にリダイレクトされ、
    # 開いたときに各行が選択済みとなること
    res = client.get(
        "/compare",
        params=[
            ("item", "birdman_tamachi:optimistic"),
            ("item", "birdman_tamachi:standard"),
            ("year", "2026"),
            ("calendar", "steady"),
        ],
        follow_redirects=False,
    )
    assert res.status_code == 303
    expected_loc = (
        "/compare?items=birdman_tamachi:optimistic,birdman_tamachi:standard"
        "&year=2026&calendar=steady"
    )
    assert res.headers["location"] == expected_loc

    # リダイレクト先を取得
    page = client.get(expected_loc)
    assert page.status_code == 200
    assert 'value="birdman_tamachi:optimistic" selected' in page.text
    assert 'value="birdman_tamachi:standard" selected' in page.text


def test_tc_f6_11(client: TestClient) -> None:
    def count_runs() -> int:
        count = 0
        while True:
            r = client.get(f"/api/runs/{count + 1}")
            if r.status_code == 200:
                count += 1
            else:
                break
        return count

    # 1 回目
    url = "/compare?items=birdman_tamachi:standard&year=2026&calendar=steady"
    page1 = client.get(url)
    assert page1.status_code == 200, page1.text
    count_after_first = count_runs()
    assert count_after_first >= 1, "初回の比較で run が作成されること"

    # 2 回目
    page2 = client.get(url)
    assert page2.status_code == 200, page2.text
    count_after_second = count_runs()
    assert count_after_second == count_after_first, "2 回目は新しい run を作らないこと"


def test_legacy_run_result_compatibility(client: TestClient, session: Session) -> None:
    # 旧形式の result_json
    # （variants キーを持たず revenue・pnl・variant を最上位に持つ）を作成。
    # (b) で新形式の run と値を見分けられるよう、2 階は f2_12 で作る
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        seed=42,
        replications=1,
        overrides={"layout.floor2": "f2_12"},
    )
    eval_res = evaluate(DATA, req)[0]
    legacy_payload = result_to_dict(eval_res)
    assert "variants" not in legacy_payload
    old_inc_tax = f"{legacy_payload['revenue']['total_inc_tax']:,}"

    old_run = Run(
        store_id="birdman_tamachi",
        scenario_id=None,
        assumption_set="standard",
        calendar_mode="steady",
        year=2026,
        seed=42,
        replications=1,
        input_hash="legacy_input_hash_01",
        code_sha256="legacy_sha256",
        result_json=json.dumps(legacy_payload, ensure_ascii=False),
    )
    repo = RunRepository(session)
    repo.add(old_run)
    old_id = old_run.id
    assert old_id is not None

    # (a) /compare?runs=<その id> に旧形式の total_inc_tax（3 桁区切り）が出る
    res_a = client.get(f"/compare?runs={old_id}")
    assert res_a.status_code == 200, res_a.text
    assert old_inc_tax in res_a.text, (
        f"旧形式の total_inc_tax ({old_inc_tax}) が /compare に表示されること"
    )

    # 新形式の run を 1 件作成
    res_new = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res_new.status_code == 200, res_new.text
    new_data = res_new.json()
    new_id = new_data["run_id"]
    new_v0 = new_data["variants"][0]
    new_inc_tax = f"{new_v0['revenue']['total_inc_tax']:,}"
    assert old_inc_tax != new_inc_tax, "旧形式と新形式の売上を見分けられること"

    # (b) 新形式の run と並べた /compare?runs=<旧>,<新> で両方の total_inc_tax が出る
    res_b = client.get(f"/compare?runs={old_id},{new_id}")
    assert res_b.status_code == 200, res_b.text
    assert old_inc_tax in res_b.text, (
        f"旧形式の total_inc_tax ({old_inc_tax}) が /compare に表示されること"
    )
    assert new_inc_tax in res_b.text, (
        f"新形式の total_inc_tax ({new_inc_tax}) が /compare に表示されること"
    )

    # (c) /stores/birdman_tamachi?run=<その id> が 200 で total_inc_tax が出る
    res_c = client.get(f"/stores/birdman_tamachi?run={old_id}")
    assert res_c.status_code == 200, res_c.text
    assert old_inc_tax in res_c.text, (
        f"サマリーに旧形式の total_inc_tax ({old_inc_tax}) が表示されること"
    )
