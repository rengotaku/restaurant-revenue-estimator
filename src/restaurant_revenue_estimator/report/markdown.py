"""評価レポート (Markdown / JSON)."""

from pathlib import Path
from typing import Any

import yaml

from restaurant_revenue_estimator.engine.pnl import jfc_benchmark_rows
from restaurant_revenue_estimator.engine.scenario import SensitivityRow
from restaurant_revenue_estimator.evidence.models import ProvenancedValue
from restaurant_revenue_estimator.inputs import StoreInputs
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationResult,
    RunDocument,
    plain,
)

TEN = 1.1


def result_to_dict(result: EvaluationResult) -> dict[str, Any]:
    pnl = result.pnl
    days_data = [
        {
            "date": day.day.isoformat(),
            "lunch": day.lunch.total_inc_tax,
            "dinner": day.dinner.total_inc_tax,
            "ala": day.lunch.ala_revenue + day.dinner.ala_revenue,
            "course": day.lunch.course_revenue + day.dinner.course_revenue,
            "appetizer": day.lunch.appetizer_revenue + day.dinner.appetizer_revenue,
            "other": day.lunch.other_revenue + day.dinner.other_revenue,
            "total": day.total_inc_tax,
        }
        for day in getattr(result, "days", ())
    ]
    physical_seats = sum(table.seats for table in result.tables)
    occupied_list = list(getattr(result, "occupied_people", ()))
    max_occ = getattr(result, "lunch_occupancy_max", 0.0)
    avg_occ_12_13 = getattr(result, "lunch_occupancy_12_13_avg", 0.0)
    wd_used = getattr(result, "lunch_weekday_used", "月曜")
    return {
        "store_id": result.store_id,
        "variant": result.variant,
        "name": result.variant,
        "year": result.year,
        "calendar_mode": result.calendar_mode,
        "assumption_set": result.assumption_set,
        "open_days": result.open_days,
        "days": days_data,
        "physical_seats": physical_seats,
        "occupied_people": occupied_list,
        "lunch_occupancy_max": max_occ,
        "lunch_occupancy_12_13_avg": avg_occ_12_13,
        "lunch_weekday_used": wd_used,
        "cost_breakdown": getattr(result, "cost_breakdown", None),
        "revenue": {
            "total_inc_tax": result.revenue.total_inc_tax,
            "total_ex_tax": result.revenue.total_ex_tax,
            "lunch_inc_tax": result.revenue.lunch_inc_tax,
            "dinner_inc_tax": result.revenue.dinner_inc_tax,
            "ala_inc_tax": result.revenue.ala_inc_tax,
            "course_inc_tax": result.revenue.course_inc_tax,
            "days": days_data,
            "months": [
                {
                    "year": month.year,
                    "month": month.month,
                    "total_inc_tax": month.total_inc_tax,
                    "total_ex_tax": month.total_ex_tax,
                    "lunch_inc_tax": month.lunch_inc_tax,
                    "dinner_inc_tax": month.dinner_inc_tax,
                    "ala_inc_tax": month.ala_inc_tax,
                    "course_inc_tax": month.course_inc_tax,
                    "appetizer_inc_tax": month.appetizer_inc_tax,
                    "other_inc_tax": month.other_inc_tax,
                }
                for month in result.revenue.months
            ],
        },
        "simulation": {
            "lunch_guests_by_weekday": result.lunch_guests_by_weekday,
            "lost_lunch_guests": result.lost_lunch_guests,
            "physical_seats": physical_seats,
            "occupied_people": occupied_list,
            "lunch_occupancy_max": max_occ,
            "lunch_occupancy_12_13_avg": avg_occ_12_13,
            "lunch_weekday_used": wd_used,
            "dinner_desired_guests": result.dinner_desired_guests,
            "dinner_guests": result.dinner_guests,
            "dinner_lost_guests": result.dinner_lost_guests,
            "dinner_lost_by_reason": result.dinner_lost_by_reason,
            "tables": [
                {"id": table.id, "seats": table.seats, "floor": table.floor}
                for table in result.tables
            ],
        },
        "pnl": {
            "gross_inc_tax": pnl.gross_inc_tax,
            "gross_ex_tax": pnl.gross_ex_tax,
            "cogs": pnl.yen(pnl.cogs),
            "gross_profit": pnl.yen(pnl.gross_profit),
            "labor": pnl.yen(pnl.labor),
            "store_operating_profit": pnl.yen(pnl.store_operating_profit),
            "business_income": pnl.yen(pnl.business_income),
            "owner_labor": pnl.yen(pnl.owner_labor),
            "cash_flow": pnl.yen(pnl.cash_flow),
            "rent": pnl.yen(pnl.fixed.rent),
            "other_expenses": pnl.yen(pnl.fixed.other_total),
            "depreciation": pnl.yen(pnl.fixed.depreciation),
            "interest": pnl.yen(pnl.fixed.interest),
            "principal": pnl.yen(pnl.fixed.principal),
            "capex": pnl.yen(pnl.fixed.capex),
            "months": [
                {
                    "year": m.year,
                    "month": m.month,
                    "gross_inc_tax": m.gross_inc_tax,
                    "gross_ex_tax": m.gross_ex_tax,
                    "cogs": m.yen(m.cogs),
                    "gross_profit": m.yen(m.gross_profit),
                    "labor": m.yen(m.labor),
                    "store_operating_profit": m.yen(m.store_operating_profit),
                    "business_income": m.yen(m.business_income),
                    "owner_labor": m.yen(m.owner_labor),
                    "cash_flow": m.yen(m.cash_flow),
                    "rent": m.yen(m.fixed.rent),
                    "other_expenses": m.yen(m.fixed.other_total),
                    "depreciation": m.yen(m.fixed.depreciation),
                    "interest": m.yen(m.fixed.interest),
                    "principal": m.yen(m.fixed.principal),
                    "capex": m.yen(m.fixed.capex),
                }
                for m in getattr(pnl, "months", ())
            ],
        },
        "labor_breakdown": (
            {
                "full_time": pnl.yen(result.labor_breakdown.full_time),
                "bonus": pnl.yen(result.labor_breakdown.bonus),
                "part_time": pnl.yen(result.labor_breakdown.part_time),
                "statutory": pnl.yen(result.labor_breakdown.statutory),
                "commute": pnl.yen(result.labor_breakdown.commute),
            }
            if result.labor_breakdown is not None
            else {}
        ),
        "warnings": list(result.warnings),
        "meta": result.meta.as_dict(),
        "unresolved": list(result.unresolved),
        "overrides": [
            {
                "key": key,
                "status": "ASSUMPTION",
                "source": f"scenario:{key}",
            }
            for key in result.meta.overrides
        ],
    }


def _yen(value: int) -> str:
    return f"{value:,} 円"


def _value_lines(inputs: StoreInputs) -> list[str]:
    lines = []
    for entry in inputs.values:
        value = entry.value
        status_str = value.status.value
        if status_str == "ASSUMPTION":
            url_text = "—"
            date_text = "—"
            note_text = value.justification or value.note or ""
        else:
            urls = []
            dates = []
            for source_id in value.sources:
                source = inputs.sources.get(source_id)
                if source:
                    urls.append(source.url)
                    if source.accessed_at:
                        dates.append(source.accessed_at.isoformat())
                    else:
                        dates.append("—")
                else:
                    urls.append(source_id)
                    dates.append("—")
            url_text = ", ".join(urls) if urls else "—"
            date_text = ", ".join(dates) if dates else "—"
            note_text = value.note or ""
        lines.append(
            f"| `{entry.path}` | {status_str} | {_display(value)} | {url_text} | {date_text} | {note_text} |"
        )
    return lines


def _display(value: ProvenancedValue[Any]) -> str:
    if value.value is None:
        candidates = ", ".join(str(name) for name in value.candidates)
        return f"null（候補: {candidates}）"
    return str(value.value)


def _source_dates(inputs: StoreInputs) -> tuple[str, str]:
    dates = sorted(
        source.accessed_at for source in inputs.sources.values() if source.accessed_at
    )
    if not dates:
        return "—", "—"
    return dates[0].isoformat(), dates[-1].isoformat()


def _related_note(inputs: StoreInputs) -> str:
    store = inputs.files.get("store.yaml", {}) or {}
    names = [
        str(item.get("name"))
        for item in store.get("related_stores", []) or []
        if item.get("included") is False
    ]
    if not names:
        return "姉妹店の混入は無い。"
    return f"姉妹店（{'、'.join(names)}）を含めていない。"


def _external_url_lines(inputs: StoreInputs) -> list[str]:
    store = inputs.files.get("store.yaml", {}) or {}
    lines = []
    official_url = plain(store.get("official_url") or store.get("url"))
    if official_url:
        lines.append(f"- 公式 URL: {official_url}")

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
                if name in src.url and val in src.url:
                    url = src.url
                    break
        if not url and name == "tabelog":
            for src in inputs.sources.values():
                if "tabelog.com" in src.url and val in src.url:
                    url = src.url
                    break
        if url:
            label = "食べログ" if name == "tabelog" else name
            lines.append(f"- {label}: {url}")
    return lines


def _sales_status(
    choices: dict[str, str],
    overrides: dict[str, Any] | None,
) -> str:
    """UNRESOLVED の候補を選んで計算した variant は UNRESOLVED、上書きで固定した値は ASSUMPTION."""
    overrides = overrides or {}
    if any(key not in overrides for key in choices):
        return "UNRESOLVED"
    return "ASSUMPTION"


SCENARIO_DISPLAY_NAMES = {
    "pessimistic": "悲観",
    "standard": "標準",
    "optimistic": "楽観",
}


def _render_scenarios_table(document: RunDocument) -> list[str]:
    lines = [
        "| 候補 | シナリオ | 年間税込売上 | 年間税抜売上 | 事業所得相当 |",
        "|---|---|---:|---:|---:|",
    ]
    by_variant_scenario = {
        (s["variant"], s["assumption_set"]): s for s in document.scenarios
    }
    for var in document.variants:
        var_name = var["name"]
        for sc_key in ("pessimistic", "standard", "optimistic"):
            sc = by_variant_scenario.get((var_name, sc_key))
            if sc:
                sc_name = SCENARIO_DISPLAY_NAMES.get(sc_key, sc_key)
                lines.append(
                    f"| {var_name} | {sc_name} | {_yen(sc['gross_inc_tax'])} | "
                    f"{_yen(sc['gross_ex_tax'])} | {_yen(sc['business_income'])} |"
                )
    lines.append("")
    return lines


def _render_variant_revenue(
    result: EvaluationResult,
    overrides: dict[str, Any] | None,
    *,
    with_subheading: bool = True,
) -> list[str]:
    lines: list[str] = []
    if with_subheading:
        lines.extend([f"### {result.variant}", ""])
    lines.extend(
        [
            "| 月 | ステータス | 昼（税込） | 夜（税込） | 合計（税込） | 税抜 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for month in result.revenue.months:
        lines.append(
            f"| {month.month} 月 | DERIVED | {_yen(month.lunch_inc_tax)} | {_yen(month.dinner_inc_tax)} | "
            f"{_yen(month.total_inc_tax)} | {_yen(month.total_ex_tax)} |"
        )
    lines.append(
        f"| 年 | DERIVED | {_yen(result.revenue.lunch_inc_tax)} | {_yen(result.revenue.dinner_inc_tax)} | "
        f"{_yen(result.revenue.total_inc_tax)} | {_yen(result.revenue.total_ex_tax)} |"
    )
    lines.append("")
    status = _sales_status(result.choices, overrides)
    lines.append(f"ステータス: 売上は DERIVED（元入力のうち最も弱いのは {status}）。")
    lines.append("")
    lines.append(f"- アラカルト: {_yen(result.revenue.ala_inc_tax)}")
    lines.append(f"- コース: {_yen(result.revenue.course_inc_tax)}")
    lines.append("")
    return lines


def _render_variant_tables(
    result: EvaluationResult,
    *,
    with_subheading: bool = True,
) -> list[str]:
    lines: list[str] = []
    if with_subheading:
        lines.extend([f"### {result.variant}", ""])
    lines.extend(
        [
            f"- 営業日: {result.open_days} 日",
            f"- 卓: {len(result.tables)} 卓 / {sum(table.seats for table in result.tables)} 席",
        ]
    )
    lunch_text = ", ".join(
        f"{weekday} {guests} 人"
        for weekday, guests in result.lunch_guests_by_weekday.items()
    )
    lines.append(f"- 昼の入店客数（曜日別の代表日）: {lunch_text or '—'}")
    lines.append(f"- 昼の失った需要: {result.lost_lunch_guests:.1f} 人/日")
    lines.append(f"- 夜の来店希望人数: {result.dinner_desired_guests:,} 人/年")
    lines.append(f"- 夜の入店客数: {result.dinner_guests:,} 人/年")
    if result.dinner_lost_by_reason:
        reasons_text = ", ".join(
            f"{reason} {count:,} 人"
            for reason, count in sorted(result.dinner_lost_by_reason.items())
        )
        lines.append(
            f"- 夜の失った需要: {result.dinner_lost_guests:,} 人/年（{reasons_text}）"
        )
    else:
        lines.append(f"- 夜の失った需要: {result.dinner_lost_guests:,} 人/年")
    lines.append(f"- 2 階の候補: {result.variant}")
    lines.append("")
    return lines


def _render_variant_pnl(
    result: EvaluationResult,
    *,
    with_subheading: bool = True,
) -> list[str]:
    lines: list[str] = []
    if with_subheading:
        lines.extend([f"### {result.variant}", ""])
    pnl = result.pnl
    lines.extend(
        [
            "| 段階 | 金額 |",
            "|---|---:|",
            f"| 税込売上 | {_yen(pnl.gross_inc_tax)} |",
            f"| 税抜売上 | {_yen(pnl.gross_ex_tax)} |",
            f"| 粗利 | {_yen(pnl.yen(pnl.gross_profit))} |",
            f"| 店舗営業利益 | {_yen(pnl.yen(pnl.store_operating_profit))} |",
            f"| 事業所得相当 | {_yen(pnl.yen(pnl.business_income))} |",
            f"| CF | {_yen(pnl.yen(pnl.cash_flow))} |",
            f"| 店主の労働対価（別列） | {_yen(pnl.yen(pnl.owner_labor))} |",
            "",
            "店主の労働対価は事業所得相当に含めていない。両者を足した値は出さない。",
            "",
        ]
    )
    return lines


def _render_benchmark_section(
    pnl: Any,
    benchmark: dict[str, Any] | None,
) -> list[str]:
    lines = ["## 6. 公庫ベンチマークとの照合", ""]
    if benchmark and pnl:
        definition = benchmark.get("definition_note", "")
        lines.append(f"- 定義: {definition}")
        lines.append("- 売上は税込、人件費に店主の労働対価を含めて換算した。")
        lines.append("")
        lines.append("| 指標 | 推計 | 公庫平均 | 黒字企業平均 |")
        lines.append("|---|---:|---:|---:|")
        for row in jfc_benchmark_rows(pnl, benchmark, null_label="—"):
            lines.append(
                f"| {row.label} | {row.estimate} | {row.average} | {row.profitable_average} |"
            )
        lines.append("")
        lines.append("外れていても誤りとは扱わない。分母と範囲の定義が違う。")
    else:
        lines.append("- ベンチマークが無い。")
    lines.append("")
    return lines


def _render_sensitivity_section(
    sensitivity_rows: list[SensitivityRow] | None,
) -> list[str]:
    lines = ["## 7. 感度と調査の優先順位", ""]
    if sensitivity_rows:
        lines.append(
            "| 入力 | 振った値 | 年間税込売上の差 | 事業所得相当の差 | 確かめ方 |"
        )
        lines.append("|---|---|---:|---:|---|")
        for row in sensitivity_rows[:10]:
            lines.append(
                f"| `{row.key}` | {row.value_name} | {_yen(row.delta_revenue)} | {_yen(row.delta_income)} | {row.how_to_verify} |"
            )
    else:
        lines.append("- 感度分析を実行していない。")
    lines.append("")
    return lines


def render_markdown(
    result: EvaluationResult,
    inputs: StoreInputs,
    *,
    sensitivity_rows: list[SensitivityRow] | None = None,
    legacy_lines: list[str] | None = None,
    benchmark: dict[str, Any] | None = None,
) -> str:
    store = inputs.files.get("store.yaml", {}) or {}
    name = plain(store.get("name")) or result.store_id
    oldest, newest = _source_dates(inputs)
    lines: list[str] = [
        f"# {name} の評価レポート",
        "",
        "> これはシナリオであって実績でも予測区間でもない。実績データで誤差を測るまでは、出力を予測として扱わない。",
        "",
        "## 1. 未確定の入力",
        "",
    ]

    unresolved = [
        entry for entry in inputs.values if entry.value.status.value == "UNRESOLVED"
    ]
    if unresolved:
        for entry in unresolved:
            note = f"（{entry.value.note}）" if entry.value.note else ""
            lines.append(
                f"- `{entry.path}`: 候補 {', '.join(entry.value.candidates)}{note}"
            )
    else:
        lines.append("- なし")
    lines.append("")

    lines.extend(
        [
            "## 2. 店舗と根拠の時点",
            "",
            f"- 店舗: {name}",
            f"- 住所: {plain(store.get('address'))}",
        ]
    )
    lines.extend(_external_url_lines(inputs))
    lines.extend(
        [
            f"- 出典の取得日: {oldest} 〜 {newest}",
            f"- {_related_note(inputs)}",
            "",
            "## 3. 年間・月別の売上",
            "",
        ]
    )
    lines.extend(
        _render_variant_revenue(result, result.meta.overrides, with_subheading=False)
    )

    lines.extend(
        [
            "## 4. 卓シミュレーション",
            "",
        ]
    )
    lines.extend(_render_variant_tables(result, with_subheading=False))

    lines.extend(
        [
            "## 5. 費用・損益・CF",
            "",
        ]
    )
    lines.extend(_render_variant_pnl(result, with_subheading=False))

    lines.extend(_render_benchmark_section(result.pnl, benchmark))
    lines.extend(_render_sensitivity_section(sensitivity_rows))

    lines.extend(
        [
            "## 8. 根拠一覧",
            "",
            "| キー | ステータス | 値 | 出典 URL | 取得日 | 注記 |",
            "|---|---|---|---|---|---|",
        ]
    )
    lines.extend(_value_lines(inputs))
    lines.append("")

    if legacy_lines:
        lines.append("## 付録: 過去の試算との差")
        lines.append("")
        lines.extend(f"- {line}" for line in legacy_lines)
        lines.append("")

    return "\n".join(lines)


def render_run_markdown(
    document: RunDocument,
    inputs: StoreInputs,
    *,
    sensitivity_rows: list[SensitivityRow] | None = None,
    legacy_lines: list[str] | None = None,
    benchmark: dict[str, Any] | None = None,
) -> str:
    store = inputs.files.get("store.yaml", {}) or {}
    name = plain(store.get("name")) or document.store_id
    oldest, newest = _source_dates(inputs)
    lines: list[str] = [
        f"# {name} の評価レポート",
        "",
        "> これはシナリオであって実績でも予測区間でもない。実績データで誤差を測るまでは、出力を予測として扱わない。",
        "",
        "## 1. 未確定の入力",
        "",
    ]

    unresolved = [
        entry for entry in inputs.values if entry.value.status.value == "UNRESOLVED"
    ]
    if unresolved:
        for entry in unresolved:
            note = f"（{entry.value.note}）" if entry.value.note else ""
            lines.append(
                f"- `{entry.path}`: 候補 {', '.join(entry.value.candidates)}{note}"
            )
        lines.append(f"- 候補の組合せ {len(document.variants)} 通りをすべて計算した。")
    else:
        lines.append("- なし")
    lines.append("")

    lines.extend(
        [
            "## 2. 店舗と根拠の時点",
            "",
            f"- 店舗: {name}",
            f"- 住所: {plain(store.get('address'))}",
        ]
    )
    lines.extend(_external_url_lines(inputs))
    lines.extend(
        [
            f"- 出典の取得日: {oldest} 〜 {newest}",
            f"- {_related_note(inputs)}",
            "",
            "## 3. 年間・月別の売上",
            "",
        ]
    )
    lines.extend(_render_scenarios_table(document))
    for res in document.results:
        lines.extend(
            _render_variant_revenue(res, document.meta.overrides, with_subheading=True)
        )

    lines.extend(
        [
            "## 4. 卓シミュレーション",
            "",
        ]
    )
    for res in document.results:
        lines.extend(_render_variant_tables(res, with_subheading=True))

    lines.extend(
        [
            "## 5. 費用・損益・CF",
            "",
        ]
    )
    for res in document.results:
        lines.extend(_render_variant_pnl(res, with_subheading=True))

    first_pnl = document.results[0].pnl if document.results else None
    lines.extend(_render_benchmark_section(first_pnl, benchmark))
    lines.extend(_render_sensitivity_section(sensitivity_rows))

    lines.extend(
        [
            "## 8. 根拠一覧",
            "",
            "| キー | ステータス | 値 | 出典 URL | 取得日 | 注記 |",
            "|---|---|---|---|---|---|",
        ]
    )
    lines.extend(_value_lines(inputs))
    lines.append("")

    if legacy_lines:
        lines.append("## 付録: 過去の試算との差")
        lines.append("")
        lines.extend(f"- {line}" for line in legacy_lines)
        lines.append("")

    return "\n".join(lines)


def load_benchmark(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader) or {}
