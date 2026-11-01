"""画面およびレポートの追加検証 (TC-F7-xx)."""

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from restaurant_revenue_estimator.cli import app
from restaurant_revenue_estimator.engine.calendar import (
    calendars_for_store,
    load_holidays,
)
from restaurant_revenue_estimator.engine.pnl import jfc_comparison
from restaurant_revenue_estimator.engine.revenue import allocate_ex_tax
from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    evaluate,
)
from restaurant_revenue_estimator.services.screens import (
    compute_late_night_minutes,
    format_late_night_duration,
    late_night_text,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STORE = DATA / "stores" / "birdman_tamachi"


def _parse_single_table(lines: list[str]) -> list[dict[str, str]]:
    if len(lines) < 2:
        return []
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def _parse_markdown_tables(text: str) -> list[list[dict[str, str]]]:
    """Parse all markdown tables in text into lists of row-dicts."""
    tables: list[list[dict[str, str]]] = []
    lines = [line.strip() for line in text.splitlines()]
    current_block: list[str] = []

    for line in lines:
        if line.startswith("|") and line.endswith("|"):
            current_block.append(line)
        else:
            if len(current_block) >= 2:
                parsed = _parse_single_table(current_block)
                if parsed:
                    tables.append(parsed)
            current_block = []

    if len(current_block) >= 2:
        parsed = _parse_single_table(current_block)
        if parsed:
            tables.append(parsed)

    return tables


def test_tc_f7_07(tmp_path: Path) -> None:
    """TC-F7-07: 8 章「根拠一覧」に出典 URL・取得日がある."""
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
    report_text = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "## 8. 根拠一覧" in report_text
    sec8_text = report_text.split("## 8. 根拠一覧")[1]
    if "\n## " in sec8_text:
        sec8_text = sec8_text.split("\n## ")[0]

    tables = _parse_markdown_tables(sec8_text)
    assert tables, "8 章に根拠一覧の表が存在すること"
    rows = tables[0]
    assert rows, "根拠一覧の表に行が存在すること"

    headers = list(rows[0].keys())
    assert "キー" in headers
    assert "ステータス" in headers
    assert "値" in headers
    assert "出典 URL" in headers
    assert "取得日" in headers
    assert "注記" in headers

    inputs = load_store(STORE)
    by_key = {r["キー"].strip("`"): r for r in rows}

    for entry in inputs.values:
        assert entry.path in by_key, f"キー `{entry.path}` が根拠一覧に存在すること"
        row = by_key[entry.path]
        assert row["ステータス"] == entry.value.status.value

        if entry.value.status.value == "ASSUMPTION":
            assert row["出典 URL"] == "—"
            assert row["取得日"] == "—"
            if entry.value.justification:
                assert entry.value.justification in row["注記"]
        elif entry.value.sources:
            for source_id in entry.value.sources:
                source = inputs.sources.get(source_id)
                assert source is not None, f"出典 {source_id} が見つからない"
                assert source.url in row["出典 URL"]
                if source.accessed_at:
                    assert source.accessed_at.isoformat() in row["取得日"]


def test_tc_f7_08(tmp_path: Path) -> None:
    """TC-F7-08: 3 章の月別の表にステータス列があり、12 か月と年の各行に入っている."""
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
    report_text = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "## 3. 年間・月別の売上" in report_text
    sec3_text = report_text.split("## 3. 年間・月別の売上")[1].split("## 4.")[0]

    tables = _parse_markdown_tables(sec3_text)
    monthly_tables = [t for t in tables if t and "月" in t[0]]
    assert monthly_tables, "3 章に月別の表が存在すること"

    for table in monthly_tables:
        headers = list(table[0].keys())
        assert "ステータス" in headers, "ヘッダに「ステータス」の列があること"
        month_rows = {r["月"]: r for r in table}
        for m in range(1, 13):
            key = f"{m} 月"
            assert key in month_rows, f"{key} の行が存在すること"
            assert month_rows[key]["ステータス"], (
                f"{key} の行にステータスが入っていること"
            )
        assert "年" in month_rows, "年の行が存在すること"
        assert month_rows["年"]["ステータス"], "年の行にステータスが入っていること"

    assert "ステータス: 売上は DERIVED（元入力のうち最も弱いのは" in sec3_text


def test_tc_f7_section2_external_urls(tmp_path: Path) -> None:
    """2 章「店舗と根拠の時点」に外部 URL（食べログ URL など）が出ること."""
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
    report_text = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "## 2. 店舗と根拠の時点" in report_text
    sec2_text = report_text.split("## 2. 店舗と根拠の時点")[1].split("## 3.")[0]
    assert "https://tabelog.com/tokyo/A1314/A131402/13134326/" in sec2_text


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._current_table = []
        elif tag == "tr":
            self._current_row = []
        elif tag in ("th", "td"):
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._current_table:
                self.tables.append(self._current_table)
        elif tag == "tr":
            if self._current_row:
                self._current_table.append(self._current_row)
        elif tag in ("th", "td"):
            self._in_cell = False
            self._current_row.append("".join(self._current_cell).strip())

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def _parse_html_table(html: str, required_header: str = "指標") -> list[dict[str, str]]:
    parser = _HTMLTableParser()
    parser.feed(html)
    for table in parser.tables:
        if len(table) >= 2:
            headers = table[0]
            if required_header in headers:
                rows = []
                for row in table[1:]:
                    if len(row) == len(headers):
                        rows.append(dict(zip(headers, row, strict=True)))
                return rows
    return []


def test_tc_f7_06(client: TestClient) -> None:
    """TC-F7-06: 費用画面の公庫照合の表に黒字企業平均の列があり、
    推計の 3 つの値が jfc_comparison と一致.
    """
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_data = res.json()
    run_id = run_data["run_id"]

    page = client.get(f"/stores/birdman_tamachi/costs?run={run_id}")
    assert page.status_code == 200, page.text

    rows = _parse_html_table(page.text)
    assert rows, "費用画面に公庫照合の表が存在すること"

    headers = list(rows[0].keys())
    assert "黒字企業平均" in headers, "「黒字企業平均」の列があること"
    assert "推計" in headers, "「推計」の列があること"
    assert "公庫平均" in headers, "「公庫平均」の列があること"

    by_metric = {r["指標"]: r for r in rows}
    assert "営業利益率" in by_metric, "営業利益率の行があること"
    assert "総利益率" in by_metric, "総利益率の行があること"

    assert "4.4%" in by_metric["営業利益率"]["黒字企業平均"]
    assert "—" in by_metric["総利益率"]["黒字企業平均"]

    active_variant = run_data["variants"][0]
    comp = jfc_comparison(active_variant["pnl"])
    assert by_metric["総利益率"]["推計"] == f"{comp.gross_profit_ratio * 100:.1f}%"
    assert by_metric["人件費率"]["推計"] == f"{comp.labor_ratio * 100:.1f}%"
    assert by_metric["営業利益率"]["推計"] == f"{comp.operating_margin * 100:.1f}%"


def test_tc_f7_09(tmp_path: Path) -> None:
    """TC-F7-09: レポート 6 章の表で推計・公庫平均・黒字企業平均の値が
    すべて 数字.数字% か「—」.
    """
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
    report_text = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "## 6. 公庫ベンチマークとの照合" in report_text
    sec6_text = report_text.split("## 6. 公庫ベンチマークとの照合")[1].split("## 7.")[0]

    tables = _parse_markdown_tables(sec6_text)
    assert tables, "6 章に照合の表が存在すること"
    rows = tables[0]
    assert rows, "表に行が存在すること"

    headers = list(rows[0].keys())
    assert "推計" in headers
    assert "公庫平均" in headers
    assert "黒字企業平均" in headers

    pattern = re.compile(r"^-?\d+\.\d+%$")
    for row in rows:
        for col in ("推計", "公庫平均", "黒字企業平均"):
            val = row[col]
            assert val == "—" or pattern.match(val), (
                f"列 '{col}' の値 '{val}' は '数字.数字%' または '—' であること"
            )


def test_tc_f7_02(client: TestClient) -> None:
    """TC-F7-02: ?tax=ex の表で 昼＋夜＝合計、アラカルト＋コース＋お通し＝合計、
    合計＝result.json の total_ex_tax、年の行の各列＝月の和、
    年の合計＝result.json の total_ex_tax.
    """
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_data = res.json()
    run_id = run_data["run_id"]
    active_variant = run_data["variants"][0]
    expected_months = active_variant["revenue"]["months"]
    expected_total_ex = active_variant["revenue"]["total_ex_tax"]

    page = client.get(f"/stores/birdman_tamachi/revenue?run={run_id}&tax=ex")
    assert page.status_code == 200, page.text

    rows = _parse_html_table(page.text, required_header="昼")
    assert rows, "売上内訳の表が存在すること"

    headers = list(rows[0].keys())
    assert "昼" in headers
    assert "夜" in headers
    assert "アラカルト" in headers
    assert "コース" in headers
    assert "お通し" in headers
    assert "合計" in headers

    month_rows = [r for r in rows if r["月"].endswith("月")]
    assert len(month_rows) == 12, "12 か月分の行があること"

    for i, r in enumerate(month_rows):
        lunch = int(r["昼"].replace(",", ""))
        dinner = int(r["夜"].replace(",", ""))
        ala = int(r["アラカルト"].replace(",", ""))
        course = int(r["コース"].replace(",", ""))
        app = int(r["お通し"].replace(",", ""))
        total = int(r["合計"].replace(",", ""))

        assert lunch + dinner == total, f"{r['月']} で 昼+夜 == 合計"
        assert ala + course + app == total, (
            f"{r['月']} で アラカルト+コース+お通し == 合計"
        )
        assert total == expected_months[i]["total_ex_tax"], (
            f"{r['月']} の合計 == total_ex_tax"
        )

    year_rows = [r for r in rows if r["月"] == "年"]
    assert len(year_rows) == 1, "年の行が 1 行存在すること"
    yr = year_rows[0]
    yr_lunch = int(yr["昼"].replace(",", ""))
    yr_dinner = int(yr["夜"].replace(",", ""))
    yr_ala = int(yr["アラカルト"].replace(",", ""))
    yr_course = int(yr["コース"].replace(",", ""))
    yr_app = int(yr["お通し"].replace(",", ""))
    yr_total = int(yr["合計"].replace(",", ""))

    assert yr_lunch == sum(int(r["昼"].replace(",", "")) for r in month_rows)
    assert yr_dinner == sum(int(r["夜"].replace(",", "")) for r in month_rows)
    assert yr_ala == sum(int(r["アラカルト"].replace(",", "")) for r in month_rows)
    assert yr_course == sum(int(r["コース"].replace(",", "")) for r in month_rows)
    assert yr_app == sum(int(r["お通し"].replace(",", "")) for r in month_rows)
    assert yr_total == sum(int(r["合計"].replace(",", "")) for r in month_rows)
    assert yr_total == expected_total_ex


def test_tc_f7_03(client: TestClient) -> None:
    """TC-F7-03: ?tax=inc の表で各月の行で アラカルト＋コース＋お通し＝合計."""
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    page = client.get(f"/stores/birdman_tamachi/revenue?run={run_id}&tax=inc")
    assert page.status_code == 200, page.text

    rows = _parse_html_table(page.text, required_header="昼")
    assert rows, "売上内訳の表が存在すること"

    headers = list(rows[0].keys())
    assert "お通し" in headers, "「お通し」の列があること"
    assert "アラカルト" in headers
    assert "コース" in headers
    assert "合計" in headers

    month_rows = [r for r in rows if r["月"].endswith("月")]
    assert len(month_rows) == 12, "12 か月分の行があること"

    for r in month_rows:
        ala = int(r["アラカルト"].replace(",", ""))
        course = int(r["コース"].replace(",", ""))
        app = int(r["お通し"].replace(",", ""))
        total = int(r["合計"].replace(",", ""))
        assert ala + course + app == total, (
            f"{r['月']} で アラカルト+コース+お通し == 合計"
        )


def test_allocate_ex_tax_unit() -> None:
    """allocate_ex_tax の端数調整・合計一致・単一要素・ゼロなどの単体挙動を検証."""
    assert allocate_ex_tax([], 100) == []
    assert allocate_ex_tax([0, 0], 0) == [0, 0]
    assert allocate_ex_tax([5000], 4545) == [4545]

    res = allocate_ex_tax([1000, 2000], 2727)
    assert res == [909, 1818]
    assert sum(res) == 2727

    res3 = allocate_ex_tax([100, 100, 100], 272)
    assert res3 == [91, 91, 90]
    assert sum(res3) == 272


def test_tc_f7_01(client: TestClient) -> None:
    """TC-F7-01: 6 月の営業日の行が暦の営業日数と同じ数。
    日別の合計の和 ＝ result.json の 6 月の合計.
    """
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_data = res.json()
    run_id = run_data["run_id"]
    active_variant = run_data["variants"][0]
    expected_months = active_variant["revenue"]["months"]
    june_expected = next(m for m in expected_months if m["month"] == 6)
    expected_june_total = june_expected["total_inc_tax"]

    # 営業日数は engine.calendar.calendars_for_store で数える
    holidays = load_holidays(
        DATA / "calendars" / "jp_holidays_cao.csv",
        DATA / "calendars" / "jp_holidays_cao.meta.yaml",
        2026,
    )
    inputs = load_store(STORE)
    series = calendars_for_store(inputs, year=2026, mode="steady", holidays=holidays)[0]
    june_open_days = [d for d in series.days if d.day.month == 6 and d.is_open]
    expected_open_count = len(june_open_days)

    page = client.get(f"/stores/birdman_tamachi/revenue?run={run_id}&month=6")
    assert page.status_code == 200, page.text

    rows = _parse_html_table(page.text, required_header="日付")
    assert rows, "6 月の日別の表が存在すること"
    assert len(rows) == expected_open_count, (
        f"6 月の営業日の行数 ({len(rows)}) が"
        f"暦の営業日数 ({expected_open_count}) と一致すること"
    )

    headers = list(rows[0].keys())
    for col in ("日付", "昼", "夜", "アラカルト", "コース", "お通し", "合計"):
        assert col in headers, f"「{col}」の列があること"

    daily_totals_sum = 0
    for r in rows:
        lunch = int(r["昼"].replace(",", ""))
        dinner = int(r["夜"].replace(",", ""))
        ala = int(r["アラカルト"].replace(",", ""))
        course = int(r["コース"].replace(",", ""))
        app = int(r["お通し"].replace(",", ""))
        total = int(r["合計"].replace(",", ""))

        assert lunch + dinner == total, f"{r['日付']} で 昼+夜 == 合計"
        assert ala + course + app == total, (
            f"{r['日付']} で アラカルト+コース+お通し == 合計"
        )
        daily_totals_sum += total

    assert daily_totals_sum == expected_june_total, (
        f"日別の合計の和 ({daily_totals_sum}) ＝ "
        f"result.json の 6 月の合計 ({expected_june_total})"
    )

    # 月の行から ?month=6 へリンクがあり、クエリ run / variant / tax を引き継ぐこと
    base_page = client.get(
        f"/stores/birdman_tamachi/revenue?run={run_id}&tax=inc&variant={active_variant['name']}"
    )
    assert base_page.status_code == 200
    assert "month=6" in base_page.text
    assert f"run={run_id}" in base_page.text


def test_tc_f7_01_result_json_daily_sales(client: TestClient) -> None:
    """result.json の variant ごとに日別の売上が格納されていることを検証."""
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_data = res.json()
    active_variant = run_data["variants"][0]

    assert "days" in active_variant, "variant に days が存在すること"
    days = active_variant["days"]
    assert len(days) > 200, "1 年分の営業日の売上データが存在すること"

    holidays = load_holidays(
        DATA / "calendars" / "jp_holidays_cao.csv",
        DATA / "calendars" / "jp_holidays_cao.meta.yaml",
        2026,
    )
    inputs = load_store(STORE)
    series = calendars_for_store(inputs, year=2026, mode="steady", holidays=holidays)[0]
    expected_june_days = len([d for d in series.days if d.day.month == 6 and d.is_open])

    june_days = [d for d in days if d["date"].startswith("2026-06")]
    assert len(june_days) == expected_june_days, (
        f"6 月の営業日数 ({len(june_days)}) == {expected_june_days}"
    )

    june_month = next(m for m in active_variant["revenue"]["months"] if m["month"] == 6)
    assert sum(d["total"] for d in june_days) == june_month["total_inc_tax"]

    for d in june_days:
        for key in ("date", "lunch", "dinner", "ala", "course", "appetizer", "total"):
            assert key in d, f"日別データに {key} が存在すること"
        assert d["lunch"] + d["dinner"] == d["total"]
        assert d["ala"] + d["course"] + d["appetizer"] == d["total"]


def test_tc_f7_04(client: TestClient) -> None:
    """TC-F7-04: 「店長」「調理」と各月給、シフトの「11:00」「14:00」
    「17:00」「23:00」、原価率、「独立化代替費」と 960,000（年額）、
    月次の損益の 12 か月の行と年の行がある.
    """
    page = client.get("/stores/birdman_tamachi/costs")
    assert page.status_code == 200, page.text

    text = page.text
    assert "店長" in text
    assert "調理" in text
    assert "300,000" in text  # 各月給

    assert "11:00" in text
    assert "14:00" in text
    assert "17:00" in text
    assert "23:00" in text

    assert "原価率" in text

    assert "独立化代替費" in text
    assert "960,000" in text

    # 月次の損益の 12 か月の行と年の行
    assert "1 月" in text or "1月" in text
    assert "12 月" in text or "12月" in text
    assert "年" in text
    assert "合計" not in text

    # 月次の損益の表を _HTMLTableParser で検証
    pnl_rows = _parse_html_table(text, required_header="事業所得相当")
    assert pnl_rows, "月次の損益段階の表が存在すること"
    month_rows = [r for r in pnl_rows if r["月"].endswith("月")]
    assert len(month_rows) == 12, "月次の損益に 12 か月の行があること"
    for m in range(1, 13):
        assert any(r["月"].startswith(str(m)) for r in month_rows), (
            f"{m} 月の行が存在すること"
        )
    year_rows = [r for r in pnl_rows if r["月"] == "年"]
    assert len(year_rows) == 1, "「年」の行が 1 行存在すること"

    yr_income = int(year_rows[0]["事業所得相当"].replace(",", ""))
    month_incomes = [int(r["事業所得相当"].replace(",", "")) for r in month_rows]
    assert yr_income == sum(month_incomes), (
        f"年の事業所得相当 ({yr_income}) が 12 か月の和 ({sum(month_incomes)}) と一致"
    )

    # 職種別月給テーブルを検証
    staff_rows = _parse_html_table(text, required_header="月給")
    assert staff_rows, "職種別の表が存在すること"
    staff_by_role = {r["職種"]: r for r in staff_rows}
    assert "店長" in staff_by_role
    assert "300,000" in staff_by_role["店長"]["月給"]
    assert "調理" in staff_by_role
    assert "300,000" in staff_by_role["調理"]["月給"]


def test_tc_f7_05(client: TestClient) -> None:
    """TC-F7-05: part_time_shifts の時給を 1,250 にしたシナリオの run で、
    警告に「2026-10-01」と「1,280」がある.
    """
    scenario = client.post(
        "/api/stores/birdman_tamachi/scenarios",
        json={
            "name": "時給1250_TC_F7_05",
            "overrides": {
                "part_time_shifts": [
                    {
                        "id": "lunch",
                        "weekdays": [
                            "mon",
                            "tue",
                            "wed",
                            "thu",
                            "fri",
                            "sat",
                            "sun",
                        ],
                        "service": "lunch",
                        "start": "11:00",
                        "end": "14:00",
                        "heads": 2,
                        "hourly_wage": 1250,
                    },
                    {
                        "id": "dinner",
                        "weekdays": [
                            "mon",
                            "tue",
                            "wed",
                            "thu",
                            "fri",
                            "sat",
                            "sun",
                        ],
                        "service": "dinner",
                        "start": "17:00",
                        "end": "23:00",
                        "heads": 3,
                        "hourly_wage": 1250,
                    },
                ]
            },
        },
    ).json()
    response = client.post(
        "/stores/birdman_tamachi/simulator",
        data={"scenario_id": str(scenario["id"]), "replications": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    run_id = response.headers["location"].split("run=")[1].split("&")[0]

    page = client.get(f"/stores/birdman_tamachi/costs?run={run_id}")
    assert page.status_code == 200, page.text
    assert "2026-10-01" in page.text
    assert "1,280" in page.text


def test_tc_f7_10(client: TestClient) -> None:
    """TC-F7-10: GET /stores/birdman_tamachi で
    食べログの URL（https://tabelog.com/tokyo/A1314/A131402/13134326/）と、
    土曜定休の開始日の候補 2025-09-01・2026-09-01 がある.
    """
    page = client.get("/stores/birdman_tamachi")
    assert page.status_code == 200, page.text
    assert "https://tabelog.com/tokyo/A1314/A131402/13134326/" in page.text
    assert "2025-09-01" in page.text
    assert "2026-09-01" in page.text


def test_tc_f7_11(client: TestClient) -> None:
    """TC-F7-11: GET /stores/birdman_tamachi/layout?variant=f2_42 で
    物理席数（64）と、昼の着席人数の最大・12:00〜13:00 の平均が出る。
    最大 ≤ 64.
    """
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    page = client.get(f"/stores/birdman_tamachi/layout?variant=f2_42&run={run_id}")
    assert page.status_code == 200, page.text
    text = page.text

    # 物理席数 64（ページのどこかの 64 ではなく、「物理席数」の欄の値で確かめる）
    assert "64" in text
    assert "物理席数" in text
    seats_match = re.search(r"物理席数</dt>\s*<dd[^>]*>\s*(\d+)", text)
    assert seats_match is not None, "物理席数の欄に値があること"
    assert int(seats_match.group(1)) == 64

    # 昼の着席人数の最大・12:00〜13:00 の平均
    assert "最大" in text
    assert "12:00〜13:00" in text

    # 画面上の最大値を抽出して最大 ≤ 64 を検証
    match = re.search(r"昼の着席人数の最大.*?(\d+\.?\d*)\s*人", text, re.DOTALL)
    assert match is not None, "昼の着席人数の最大が取得できること"
    max_val = float(match.group(1))
    assert max_val <= 64, f"最大 ({max_val}) ≤ 64"

    # 2 階のシミュレーター POST フォームが存在すること
    assert 'action="/stores/birdman_tamachi/simulator"' in text
    assert 'name="floor2_choice"' in text

    # result.json の variant に occupied_people の時系列が入っていること
    run_data = res.json()
    f2_42 = next(v for v in run_data["variants"] if v["name"] == "f2_42")
    assert "occupied_people" in f2_42 or (
        "occupied_people" in f2_42.get("simulation", {})
    )
    occ = f2_42.get("occupied_people") or f2_42["simulation"]["occupied_people"]
    assert len(occ) > 0
    assert max(occ) <= 64


def test_tc_f7_12(client: TestClient) -> None:
    """TC-F7-12: GET /stores/birdman_tamachi/simulator で
    1 階の卓の入力欄が disabled 属性つきで出る。
    「昼の入店客数（1 日・曜日別）」と
    「昼の失った需要（営業日 1 日あたりの平均）」のラベルがある.
    """
    page = client.get("/stores/birdman_tamachi/simulator")
    assert page.status_code == 200, page.text
    text = page.text

    # 1 階の卓の入力欄が disabled 属性つきで出る
    assert "事実は YAML で直す" in text
    assert "1 階" in text or "1階" in text
    assert re.search(r'<input[^>]*name=["\']floor1_[^>]*disabled', text) or re.search(
        r'<input[^>]*disabled[^>]*name=["\']floor1_', text
    )

    # ラベルの検証
    assert "昼の入店客数（1 日・曜日別）" in text
    assert "昼の失った需要（営業日 1 日あたりの平均）" in text

    # 事実（OBSERVED...）の一文と key_dinner_ala_ticket が残っていること
    assert "事実（OBSERVED" in text
    assert 'name="key_dinner_ala_ticket"' in text

    # disabled の入力欄は送信されないので、シミュレーターの POST の上書きに入らないこと
    post_res = client.post(
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
    assert post_res.status_code == 303
    run_id = post_res.headers["location"].split("run=")[1].split("&")[0]
    run_payload = client.get(f"/api/runs/{run_id}").json()
    overrides = run_payload.get("meta", {}).get("overrides", {})
    assert not any("1f" in k or "floor1" in k for k in overrides)


def test_tc_f7_13(client: TestClient) -> None:
    """TC-F7-13: GET /stores/birdman_tamachi/priorities の表で、
    各行の振れ幅（売上差と事業所得相当の差の絶対値の大きい方）が上から大きい順で、
    全行に確かめ方がある.
    """
    page = client.get("/stores/birdman_tamachi/priorities")
    assert page.status_code == 200, page.text

    rows = _parse_html_table(page.text, required_header="確かめ方")
    assert rows, "改善優先順位の表が存在すること"

    prev_swing: float | int = float("inf")
    for r in rows:
        assert r["確かめ方"].strip() != "", f"{r['入力']} に確かめ方があること"
        d_rev = abs(int(r["年間税込売上の差"].replace(",", "")))
        d_inc = abs(int(r["事業所得相当の差"].replace(",", "")))
        swing = max(d_rev, d_inc)
        assert swing <= prev_swing, (
            f"振れ幅 ({swing}) が前行 ({prev_swing}) 以下であること (降順)"
        )
        prev_swing = swing


def test_late_night_shift_hours() -> None:
    """深夜時間帯（22:00〜05:00）との重複時間の計算と表記のテスト.

    17:00〜23:00 -> 1 時間
    18:20〜23:20 -> 1 時間 20 分
    17:00〜01:00 -> 3 時間（日またぎ）
    22:30〜23:00 -> 30 分
    """
    assert compute_late_night_minutes("17:00", "23:00") == 60
    assert late_night_text("17:00", "23:00") == "1 時間"

    assert compute_late_night_minutes("18:20", "23:20") == 80
    assert late_night_text("18:20", "23:20") == "1 時間 20 分"

    assert compute_late_night_minutes("17:00", "01:00") == 180
    assert late_night_text("17:00", "01:00") == "3 時間"

    assert compute_late_night_minutes("22:30", "23:00") == 30
    assert late_night_text("22:30", "23:00") == "30 分"

    assert format_late_night_duration(0) == "—"


def test_variant_nav_preserves_month_and_tax_query_params(
    client: TestClient,
) -> None:
    """売上画面で month・tax クエリがあるとき、variant 切替リンクに引き継がれること."""
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    page = client.get(
        f"/stores/birdman_tamachi/revenue?run={run_id}&month=6&tax=ex&variant=f2_42"
    )
    assert page.status_code == 200, page.text

    # f2_12 への切替リンクに month=6 と tax=ex が含まれること
    match = re.search(r'href="([^"]*variant=f2_12[^"]*)"', page.text)
    assert match is not None, "f2_12 への切替リンクが存在すること"
    link = match.group(1)
    assert "month=6" in link, f"month=6 がリンクに含まれること: {link}"
    assert "tax=ex" in link, f"tax=ex がリンクに含まれること: {link}"
    assert f"run={run_id}" in link, f"run={run_id} がリンクに含まれること: {link}"


def test_weekday_order_and_japanese_single_char_display(
    client: TestClient,
) -> None:
    """シミュレーター画面と座席画面で曜日が月・火・水・木・金・土・日順に並び、
    日本語1文字で表示されること.
    """
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    # (1) シミュレーター画面
    sim_page = client.get(f"/stores/birdman_tamachi/simulator?run={run_id}")
    assert sim_page.status_code == 200, sim_page.text
    sim_text = sim_page.text
    idx_mon = sim_text.find("月 ")
    idx_tue = sim_text.find("火 ")
    idx_wed = sim_text.find("水 ")
    idx_thu = sim_text.find("木 ")
    idx_fri = sim_text.find("金 ")
    assert (
        idx_mon != -1
        and idx_tue != -1
        and idx_wed != -1
        and idx_thu != -1
        and idx_fri != -1
    ), "シミュレーター画面に月〜金が表示されること"
    assert idx_mon < idx_tue < idx_wed < idx_thu < idx_fri, (
        "シミュレーター画面で曜日が月・火・水・木・金の順であること"
    )

    # (2) 座席画面
    layout_page = client.get(
        f"/stores/birdman_tamachi/layout?run={run_id}&variant=f2_42"
    )
    assert layout_page.status_code == 200, layout_page.text
    layout_text = layout_page.text
    assert "fri " not in layout_text and "mon " not in layout_text
    l_idx_mon = layout_text.find("月 ")
    l_idx_tue = layout_text.find("火 ")
    l_idx_wed = layout_text.find("水 ")
    l_idx_thu = layout_text.find("木 ")
    l_idx_fri = layout_text.find("金 ")
    assert (
        l_idx_mon != -1
        and l_idx_tue != -1
        and l_idx_wed != -1
        and l_idx_thu != -1
        and l_idx_fri != -1
    ), "座席画面に日本語1文字の月〜金が表示されること"
    assert l_idx_mon < l_idx_tue < l_idx_wed < l_idx_thu < l_idx_fri, (
        "座席画面で曜日が月・火・水・木・金の順であること"
    )


def test_lost_lunch_weighted_daily_average_with_unequal_weekdays() -> None:
    """曜日ごとの昼営業日数が異なる暦（2026年 steady、祝日で日数が違う）で、
    昼の失った需要が曜日の単純平均（29.2人）ではなく営業日数で重み付けされた平均（29.1125人）になること.
    """
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        calendar_mode="steady",
        year=2026,
        replications=1,
        seed=42,
    )
    res = evaluate(DATA, req)[0]
    # 単純平均 (43 + 31 + 16 + 43 + 13) / 5 = 29.2 ではなく、
    # 営業日数 (月:45, 火:47, 水:47, 木:51, 金:50) の重み付け平均 29.1125 であること
    assert round(res.lost_lunch_guests, 2) == 29.11
    assert res.lost_lunch_guests != 29.2


def test_costs_screen_saved_assumptions_immune_to_yaml_edit(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tmp_path に data/ をコピーして run を作り、
    そのあとでコピーの標準仮定の rent_monthly を変えても、
    その run の費用画面の家賃が保存時の値のままになること.
    """
    tmp_data = tmp_path / "data"
    shutil.copytree(DATA, tmp_data)
    monkeypatch.setenv("RRE_DATA_DIR", str(tmp_data))

    # run を作成（birdman_tamachi の standard では家賃 800,000 円/月）
    res = client.post(
        "/api/stores/birdman_tamachi/runs",
        json={"replications": 1},
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]

    page1 = client.get(f"/stores/birdman_tamachi/costs?run={run_id}")
    assert page1.status_code == 200
    assert "800,000" in page1.text

    # コピー側の standard.yaml の rent_monthly を 999,999 に書き換える
    yaml_file = (
        tmp_data / "stores" / "birdman_tamachi" / "assumptions" / "standard.yaml"
    )
    content = yaml_file.read_text(encoding="utf-8")
    assert "rent_monthly:" in content
    new_content = re.sub(
        r"(rent_monthly:.*?value:\s*)\d+",
        r"\g<1>999999",
        content,
        flags=re.DOTALL,
    )
    assert new_content != content
    yaml_file.write_text(new_content, encoding="utf-8")

    # 再度その run の費用画面を開いても、保存時の 800,000 円/月のままで
    # 999,999 にならないこと
    page2 = client.get(f"/stores/birdman_tamachi/costs?run={run_id}")
    assert page2.status_code == 200
    assert "800,000" in page2.text
    assert "999,999" not in page2.text
