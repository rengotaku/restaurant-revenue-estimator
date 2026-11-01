"""画面表示用の行構築・仮定展開サービス."""

from typing import Any

from restaurant_revenue_estimator.engine.revenue import allocate_ex_tax


def build_monthly_table_rows(
    months: list[dict[str, Any]], tax: str, has_other: bool
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if tax == "inc":
        for m in months:
            rows.append(
                {
                    "month": m["month"],
                    "lunch": m["lunch_inc_tax"],
                    "dinner": m["dinner_inc_tax"],
                    "ala": m["ala_inc_tax"],
                    "course": m["course_inc_tax"],
                    "appetizer": m.get("appetizer_inc_tax", 0),
                    "other": m.get("other_inc_tax", 0),
                    "total": m["total_inc_tax"],
                }
            )
    else:
        for m in months:
            total_ex = m["total_ex_tax"]
            lunch_ex, dinner_ex = allocate_ex_tax(
                [m["lunch_inc_tax"], m["dinner_inc_tax"]],
                total_ex,
            )
            if has_other:
                ala_ex, course_ex, app_ex, other_ex = allocate_ex_tax(
                    [
                        m["ala_inc_tax"],
                        m["course_inc_tax"],
                        m.get("appetizer_inc_tax", 0),
                        m.get("other_inc_tax", 0),
                    ],
                    total_ex,
                )
            else:
                ala_ex, course_ex, app_ex = allocate_ex_tax(
                    [
                        m["ala_inc_tax"],
                        m["course_inc_tax"],
                        m.get("appetizer_inc_tax", 0),
                    ],
                    total_ex,
                )
                other_ex = 0
            rows.append(
                {
                    "month": m["month"],
                    "lunch": lunch_ex,
                    "dinner": dinner_ex,
                    "ala": ala_ex,
                    "course": course_ex,
                    "appetizer": app_ex,
                    "other": other_ex,
                    "total": total_ex,
                }
            )
    return rows


def build_daily_table_rows(
    m_days: list[dict[str, Any]],
    months: list[dict[str, Any]],
    month: int,
    tax: str,
    daily_has_other: bool,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    if tax == "inc":
        for d in m_days:
            date_str = d.get("date") or d.get("day", "")
            rows.append(
                {
                    "date": date_str,
                    "lunch": d["lunch"],
                    "dinner": d["dinner"],
                    "ala": d["ala"],
                    "course": d["course"],
                    "appetizer": d.get("appetizer", 0),
                    "other": d.get("other", 0),
                    "total": d["total"],
                }
            )
    else:
        m_info = next((m for m in months if m["month"] == month), None)
        total_m_ex = (
            m_info["total_ex_tax"] if m_info else sum(d["total"] for d in m_days)
        )
        day_totals_inc = [d["total"] for d in m_days]
        day_totals_ex = allocate_ex_tax(day_totals_inc, total_m_ex)
        for i, d in enumerate(m_days):
            date_str = d.get("date") or d.get("day", "")
            d_total_ex = day_totals_ex[i]
            d_lunch_ex, d_dinner_ex = allocate_ex_tax(
                [d["lunch"], d["dinner"]],
                d_total_ex,
            )
            if daily_has_other:
                d_ala_ex, d_course_ex, d_app_ex, d_other_ex = allocate_ex_tax(
                    [
                        d["ala"],
                        d["course"],
                        d.get("appetizer", 0),
                        d.get("other", 0),
                    ],
                    d_total_ex,
                )
            else:
                d_ala_ex, d_course_ex, d_app_ex = allocate_ex_tax(
                    [
                        d["ala"],
                        d["course"],
                        d.get("appetizer", 0),
                    ],
                    d_total_ex,
                )
                d_other_ex = 0
            rows.append(
                {
                    "date": date_str,
                    "lunch": d_lunch_ex,
                    "dinner": d_dinner_ex,
                    "ala": d_ala_ex,
                    "course": d_course_ex,
                    "appetizer": d_app_ex,
                    "other": d_other_ex,
                    "total": d_total_ex,
                }
            )
    return rows, sum(r["total"] for r in rows)


def parse_time_to_minutes(t_str: str) -> int | None:
    """HH:MM 形式の時刻文字列を 00:00 からの経過分数（0〜）に変換する."""
    t_str = t_str.strip()
    if not t_str or ":" not in t_str:
        return None
    parts = t_str.split(":", maxsplit=1)
    try:
        h = int(parts[0])
        m = int(parts[1])
        return h * 60 + m
    except ValueError:
        return None


def compute_late_night_minutes(start_str: str, end_str: str) -> int:
    """開始・終了時刻（HH:MM）から深夜時間帯（22:00〜05:00）と重複する分数を計算する.

    日をまたぐ場合（終了時刻 <= 開始時刻、または 24:00 以降の表記）も補正する.
    """
    start_m = parse_time_to_minutes(start_str)
    end_m = parse_time_to_minutes(end_str)
    if start_m is None or end_m is None:
        return 0

    if end_m <= start_m:
        end_m += 24 * 60

    late_windows = [
        (0, 5 * 60),
        (22 * 60, 29 * 60),
        (46 * 60, 53 * 60),
    ]

    total_late_minutes = 0
    for w_start, w_end in late_windows:
        overlap = max(0, min(end_m, w_end) - max(start_m, w_start))
        total_late_minutes += overlap

    return total_late_minutes


def format_late_night_duration(minutes: int) -> str:
    """深夜分数を「1 時間 20 分」「1 時間」「30 分」「—」の形式で整形する."""
    if minutes <= 0:
        return "—"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours > 0 and rem_minutes > 0:
        return f"{hours} 時間 {rem_minutes} 分"
    if hours > 0:
        return f"{hours} 時間"
    return f"{rem_minutes} 分"


def late_night_text(start_str: str, end_str: str) -> str:
    """開始・終了時刻から深夜労働時間の表示文字列を生成する."""
    mins = compute_late_night_minutes(start_str, end_str)
    return format_late_night_duration(mins)


def build_cost_breakdown(assumptions: dict[str, Any]) -> dict[str, Any]:
    """上書き適用後の仮定辞書から、費用内訳表示用のデータを構築する."""
    raw_shifts = assumptions.get("part_time_shifts", {}).get("value", [])
    if isinstance(raw_shifts, tuple):
        raw_shifts = list(raw_shifts)
    part_time_shifts: list[dict[str, Any]] = []
    for s in raw_shifts:
        start_str = str(s.get("start", ""))
        end_str = str(s.get("end", ""))
        shift_late_night_text = late_night_text(start_str, end_str)
        part_time_shifts.append(
            {
                **s,
                "service_label": "昼" if s.get("service") == "lunch" else "夜",
                "late_night_text": shift_late_night_text,
            }
        )

    independence_monthly = int(
        assumptions.get("independence_cost_monthly", {}).get("value", 0) or 0
    )
    raw_ft = assumptions.get("full_time_staff", {}).get("value", [])
    if isinstance(raw_ft, tuple):
        raw_ft = list(raw_ft)

    other_exp = assumptions.get("other_expenses_monthly", {}).get("value", {}) or {}
    return {
        "full_time_staff": [dict(staff) for staff in raw_ft],
        "part_time_shifts": part_time_shifts,
        "cost_rate_ala": assumptions.get("cost_rate_ala", {}).get("value"),
        "cost_rate_course": assumptions.get("cost_rate_course", {}).get("value"),
        "late_night_premium": assumptions.get("late_night_premium", {}).get("value"),
        "statutory_benefit_rate": assumptions.get("statutory_benefit_rate", {}).get(
            "value"
        ),
        "bonus_months": assumptions.get("bonus_months_per_year", {}).get("value"),
        "commuting_full_time": assumptions.get(
            "commuting_monthly_per_full_time", {}
        ).get("value"),
        "commuting_part_time": assumptions.get("commuting_per_part_time_shift", {}).get(
            "value"
        ),
        "rent_monthly": assumptions.get("rent_monthly", {}).get("value"),
        "other_expenses_monthly": dict(other_exp),
        "independence_cost_monthly": independence_monthly,
        "independence_cost_annual": independence_monthly * 12,
        "depreciation_monthly": assumptions.get("depreciation_monthly", {}).get(
            "value"
        ),
        "loan_principal_monthly": assumptions.get("loan_principal_monthly", {}).get(
            "value"
        ),
        "loan_interest_monthly": assumptions.get("loan_interest_monthly", {}).get(
            "value"
        ),
    }


def extract_cost_assumptions(
    target_or_inputs: Any = None,
    raw: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """画面表示用の費用仮定内訳を取得する.

    保存済みの cost_breakdown があればそれを返し、
    無ければ旧形式の run として内訳の欄に「未記録」が出るよう空値を返す.
    今の YAML からは読まない.
    """
    cost_breakdown: dict[str, Any] | None = None
    if isinstance(result, dict) and "cost_breakdown" in result:
        cost_breakdown = result.get("cost_breakdown")
    elif isinstance(target_or_inputs, dict):
        if "cost_breakdown" in target_or_inputs:
            cost_breakdown = target_or_inputs.get("cost_breakdown")
        elif raw and "cost_breakdown" in raw:
            cost_breakdown = raw.get("cost_breakdown")
    elif isinstance(raw, dict) and "cost_breakdown" in raw:
        cost_breakdown = raw.get("cost_breakdown")

    if cost_breakdown and isinstance(cost_breakdown, dict):
        return {
            "cost_breakdown": cost_breakdown,
            "full_time_staff": cost_breakdown.get("full_time_staff", []),
            "part_time_shifts": cost_breakdown.get("part_time_shifts", []),
            "cost_rate_ala": cost_breakdown.get("cost_rate_ala"),
            "cost_rate_course": cost_breakdown.get("cost_rate_course"),
            "late_night_premium": cost_breakdown.get("late_night_premium"),
            "statutory_benefit_rate": cost_breakdown.get("statutory_benefit_rate"),
            "bonus_months": cost_breakdown.get("bonus_months"),
            "commuting_full_time": cost_breakdown.get("commuting_full_time"),
            "commuting_part_time": cost_breakdown.get("commuting_part_time"),
            "rent_monthly": cost_breakdown.get("rent_monthly"),
            "other_expenses_monthly": cost_breakdown.get(
                "other_expenses_monthly", {}
            ),
            "independence_cost_monthly": cost_breakdown.get(
                "independence_cost_monthly"
            ),
            "independence_cost_annual": cost_breakdown.get("independence_cost_annual"),
            "depreciation_monthly": cost_breakdown.get("depreciation_monthly"),
            "loan_principal_monthly": cost_breakdown.get("loan_principal_monthly"),
            "loan_interest_monthly": cost_breakdown.get("loan_interest_monthly"),
        }

    return {
        "cost_breakdown": None,
        "full_time_staff": [],
        "part_time_shifts": [],
        "cost_rate_ala": None,
        "cost_rate_course": None,
        "late_night_premium": None,
        "statutory_benefit_rate": None,
        "bonus_months": None,
        "commuting_full_time": None,
        "commuting_part_time": None,
        "rent_monthly": None,
        "other_expenses_monthly": {},
        "independence_cost_monthly": None,
        "independence_cost_annual": None,
        "depreciation_monthly": None,
        "loan_principal_monthly": None,
        "loan_interest_monthly": None,
    }
