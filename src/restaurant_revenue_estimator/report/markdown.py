"""評価レポート (Markdown / JSON)."""

from pathlib import Path
from typing import Any

import yaml

from restaurant_revenue_estimator.engine.pnl import jfc_comparison
from restaurant_revenue_estimator.engine.scenario import SensitivityRow
from restaurant_revenue_estimator.evidence.models import ProvenancedValue
from restaurant_revenue_estimator.inputs import StoreInputs
from restaurant_revenue_estimator.services.evaluation import EvaluationResult, plain

TEN = 1.1


def result_to_dict(result: EvaluationResult) -> dict[str, Any]:
    pnl = result.pnl
    return {
        "store_id": result.store_id,
        "variant": result.variant,
        "year": result.year,
        "calendar_mode": result.calendar_mode,
        "assumption_set": result.assumption_set,
        "open_days": result.open_days,
        "revenue": {
            "total_inc_tax": result.revenue.total_inc_tax,
            "total_ex_tax": result.revenue.total_ex_tax,
            "lunch_inc_tax": result.revenue.lunch_inc_tax,
            "dinner_inc_tax": result.revenue.dinner_inc_tax,
            "ala_inc_tax": result.revenue.ala_inc_tax,
            "course_inc_tax": result.revenue.course_inc_tax,
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
            "dinner_guests": result.dinner_guests,
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
        },
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
        source_text = ", ".join(value.sources) if value.sources else "—"
        lines.append(
            f"| `{entry.path}` | {value.status.value} | {_display(value)} | {source_text} | {value.note or ''} |"
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


def render_markdown(  # noqa: PLR0915
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
    lines: list[str] = []

    lines.append(f"# {name} の評価レポート")
    lines.append("")
    lines.append(
        "> これはシナリオであって実績でも予測区間でもない。実績データで誤差を測るまでは、出力を予測として扱わない。"
    )
    lines.append("")
    lines.append("## 1. 未確定の入力")
    lines.append("")
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

    lines.append("## 2. 店舗と根拠の時点")
    lines.append("")
    lines.append(f"- 店舗: {name}")
    lines.append(f"- 住所: {plain(store.get('address'))}")
    lines.append(f"- 出典の取得日: {oldest} 〜 {newest}")
    lines.append(f"- {_related_note(inputs)}")
    lines.append("")

    lines.append("## 3. 年間・月別の売上")
    lines.append("")
    lines.append("| 月 | 昼（税込） | 夜（税込） | 合計（税込） | 税抜 |")
    lines.append("|---|---:|---:|---:|---:|")
    for month in result.revenue.months:
        lines.append(
            f"| {month.month} 月 | {_yen(month.lunch_inc_tax)} | {_yen(month.dinner_inc_tax)} | "
            f"{_yen(month.total_inc_tax)} | {_yen(month.total_ex_tax)} |"
        )
    lines.append(
        f"| 年 | {_yen(result.revenue.lunch_inc_tax)} | {_yen(result.revenue.dinner_inc_tax)} | "
        f"{_yen(result.revenue.total_inc_tax)} | {_yen(result.revenue.total_ex_tax)} |"
    )
    lines.append("")
    lines.append("ステータス: 売上は DERIVED（元入力のうち最も弱いのは ASSUMPTION）。")
    lines.append("")
    lines.append(f"- アラカルト: {_yen(result.revenue.ala_inc_tax)}")
    lines.append(f"- コース: {_yen(result.revenue.course_inc_tax)}")
    lines.append("")

    lines.append("## 4. 卓シミュレーション")
    lines.append("")
    lines.append(f"- 営業日: {result.open_days} 日")
    lines.append(
        f"- 卓: {len(result.tables)} 卓 / {sum(table.seats for table in result.tables)} 席"
    )
    lunch_text = ", ".join(
        f"{weekday} {guests} 人"
        for weekday, guests in result.lunch_guests_by_weekday.items()
    )
    lines.append(f"- 昼の入店客数（曜日別の代表日）: {lunch_text or '—'}")
    lines.append(f"- 昼の失った需要: {result.lost_lunch_guests:.1f} 人/日")
    lines.append(f"- 夜の入店客数: {result.dinner_guests:,} 人/年")
    lines.append(f"- 2 階の候補: {result.variant}")
    lines.append("")

    lines.append("## 5. 費用・損益・CF")
    lines.append("")
    pnl = result.pnl
    lines.append("| 段階 | 金額 |")
    lines.append("|---|---:|")
    lines.append(f"| 税込売上 | {_yen(pnl.gross_inc_tax)} |")
    lines.append(f"| 税抜売上 | {_yen(pnl.gross_ex_tax)} |")
    lines.append(f"| 粗利 | {_yen(pnl.yen(pnl.gross_profit))} |")
    lines.append(f"| 店舗営業利益 | {_yen(pnl.yen(pnl.store_operating_profit))} |")
    lines.append(f"| 事業所得相当 | {_yen(pnl.yen(pnl.business_income))} |")
    lines.append(f"| CF | {_yen(pnl.yen(pnl.cash_flow))} |")
    lines.append(f"| 店主の労働対価（別列） | {_yen(pnl.yen(pnl.owner_labor))} |")
    lines.append("")
    lines.append(
        "店主の労働対価は事業所得相当に含めていない。両者を足した値は出さない。"
    )
    lines.append("")

    lines.append("## 6. 公庫ベンチマークとの照合")
    lines.append("")
    if benchmark:
        definition = benchmark.get("definition_note", "")
        lines.append(f"- 定義: {definition}")
        lines.append("- 売上は税込、人件費に店主の労働対価を含めて換算した。")
        lines.append("")
        comparison = jfc_comparison(pnl)
        lines.append("| 指標 | 推計 | 公庫平均 |")
        lines.append("|---|---:|---:|")
        metrics = benchmark.get("metrics", {})
        lines.append(
            f"| 総利益率 | {comparison.gross_profit_ratio:.1%} | {metrics.get('gross_profit_ratio', {}).get('average', '—')} |"
        )
        lines.append(
            f"| 人件費率 | {comparison.labor_ratio:.1%} | {metrics.get('labor_ratio', {}).get('average', '—')} |"
        )
        lines.append(
            f"| 営業利益率 | {comparison.operating_margin:.1%} | {metrics.get('operating_margin', {}).get('average', '—')} |"
        )
        lines.append("")
        lines.append("外れていても誤りとは扱わない。分母と範囲の定義が違う。")
    else:
        lines.append("- ベンチマークが無い。")
    lines.append("")

    lines.append("## 7. 感度と調査の優先順位")
    lines.append("")
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

    lines.append("## 8. 根拠一覧")
    lines.append("")
    lines.append("| キー | ステータス | 値 | 出典 | 注記 |")
    lines.append("|---|---|---|---|---|")
    lines.extend(_value_lines(inputs))
    lines.append("")

    if legacy_lines:
        lines.append("## 付録: 過去の試算との差")
        lines.append("")
        lines.extend(f"- {line}" for line in legacy_lines)
        lines.append("")

    return "\n".join(lines)


def load_benchmark(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
