"""シフト表・人件費内訳・原価・月次損益 (TC-F5-01〜07)."""

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from restaurant_revenue_estimator.engine.calendar import (
    DaySchedule,
    ServiceHours,
    calendars_for_store,
    load_holidays,
)
from restaurant_revenue_estimator.engine.cost import (
    ShiftSpec,
    StaffRole,
    cost_of_goods,
    labor_breakdown,
    labor_warnings,
    part_time_wages,
    shift_cost,
)
from restaurant_revenue_estimator.engine.cost.cost import FixedCosts
from restaurant_revenue_estimator.engine.pnl import (
    PnL,
    build_monthly_pnl,
)
from restaurant_revenue_estimator.engine.revenue import MonthRevenue
from restaurant_revenue_estimator.inputs import load_store, validate_store
from restaurant_revenue_estimator.services.evaluation import EvaluationRequest, evaluate
from restaurant_revenue_estimator.services.evaluation_service import (
    OverrideError,
    RunRequest,
    _validate_overrides,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def test_tc_f5_01_shift_table_counted_for_labor_wages() -> None:
    """TC-F5-01: シフト表の金額が昼夜・人数・割増を含めて正しく積算される."""
    shifts = [
        ShiftSpec(
            id="lunch",
            weekdays=("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
            service="lunch",
            start="11:00",
            end="14:00",
            heads=2,
            hourly_wage=Decimal("1330"),
        ),
        ShiftSpec(
            id="dinner",
            weekdays=("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
            service="dinner",
            start="17:00",
            end="23:00",
            heads=3,
            hourly_wage=Decimal("1330"),
        ),
    ]
    days = [
        DaySchedule(
            day=date(2026, 1, 1),
            weekday="mon",
            holiday=None,
            lunch=ServiceHours("11:00", "14:00", "13:30", "13:30"),
            dinner=ServiceHours("17:00", "23:00", "22:30", "22:30"),
            closed_reason=None,
            status="PUBLIC_VERIFIED",
        )
        for _ in range(240)
    ]
    wages = part_time_wages(shifts, days, late_premium=Decimal("1.25"))
    assert wages.total == Decimal("7900200")
    assert wages.by_shift["lunch"] == Decimal("1915200")
    assert wages.by_shift["dinner"] == Decimal("5985000")


def test_tc_f5_02_overnight_shift_all_hours_late_premium() -> None:
    """TC-F5-02: 22:00〜01:00 の日跨ぎシフトが全時間深夜割増で計算される."""
    shift = ShiftSpec(
        id="overnight",
        weekdays=("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
        service="dinner",
        start="22:00",
        end="01:00",
        heads=1,
        hourly_wage=Decimal("1330"),
    )
    day = DaySchedule(
        day=date(2026, 6, 1),
        weekday="mon",
        holiday=None,
        lunch=None,
        dinner=ServiceHours("17:00", "02:00", "01:30", "01:30"),
        closed_reason=None,
        status="PUBLIC_VERIFIED",
    )
    wages = part_time_wages([shift], [day], late_premium=Decimal("1.25"))
    assert wages.total == Decimal("4987.5")
    assert shift_cost(
        Decimal(22), Decimal(1), Decimal("1330"), Decimal("1.25")
    ) == Decimal("4987.5")


def test_tc_f5_03_weekdays_filter_matches_calendar_mondays() -> None:
    """TC-F5-03: weekdays の指定通りに暦の該当曜日だけが数えられる."""
    holidays = load_holidays(
        DATA_DIR / "calendars" / "jp_holidays_cao.csv",
        DATA_DIR / "calendars" / "jp_holidays_cao.meta.yaml",
        2026,
    )
    inputs = load_store(DATA_DIR / "stores" / "birdman_tamachi")
    cal = calendars_for_store(inputs, year=2026, mode="steady", holidays=holidays)[0]
    shift = ShiftSpec(
        id="mon_dinner",
        weekdays=("mon",),
        service="dinner",
        start="17:00",
        end="23:00",
        heads=1,
        hourly_wage=Decimal("1330"),
    )
    wages = part_time_wages([shift], cal.days, late_premium=Decimal("1.25"))
    expected_mondays = sum(
        1 for d in cal.days if d.weekday == "mon" and d.dinner is not None
    )
    assert wages.count == expected_mondays
    assert wages.counts_by_shift["mon_dinner"] == expected_mondays


def test_tc_f5_04_closed_service_does_not_count_shifts() -> None:
    """TC-F5-04: 昼が休みの日は昼のシフトが数えられず夜だけ数えられる."""
    holidays = load_holidays(
        DATA_DIR / "calendars" / "jp_holidays_cao.csv",
        DATA_DIR / "calendars" / "jp_holidays_cao.meta.yaml",
        2026,
    )
    inputs = load_store(DATA_DIR / "stores" / "birdman_tamachi")
    cal = calendars_for_store(
        inputs,
        year=2026,
        mode="historical",
        holidays=holidays,
        saturday_closure_start="2026-09-01",
        saturday_hours_candidate="dinner_only",
    )[0]
    saturdays_jan_aug = [d for d in cal.days if d.day.month <= 8 and d.weekday == "sat"]
    lunch_shift = ShiftSpec(
        id="lunch",
        weekdays=("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
        service="lunch",
        start="11:00",
        end="14:00",
        heads=1,
        hourly_wage=Decimal("1330"),
    )
    dinner_shift = ShiftSpec(
        id="dinner",
        weekdays=("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
        service="dinner",
        start="17:00",
        end="23:00",
        heads=1,
        hourly_wage=Decimal("1330"),
    )
    wages = part_time_wages(
        [lunch_shift, dinner_shift],
        saturdays_jan_aug,
        late_premium=Decimal("1.25"),
    )
    assert wages.counts_by_shift["lunch"] == 0
    assert wages.counts_by_shift["dinner"] == len(saturdays_jan_aug)
    assert wages.count == len(saturdays_jan_aug)


def test_tc_f5_05_minimum_wage_warning_switches_on_effective_date() -> None:
    """TC-F5-05: 2026-10-01 以降の日付を含むシフトのみ最低賃金警告が出る."""
    shift = ShiftSpec(
        id="part_time",
        weekdays=("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
        service="dinner",
        start="17:00",
        end="23:00",
        heads=1,
        hourly_wage=Decimal("1250"),
    )
    sep_days = [
        DaySchedule(
            day=date(2026, 9, day_num),
            weekday="mon",
            holiday=None,
            lunch=None,
            dinner=ServiceHours("17:00", "23:00", "22:30", "22:30"),
            closed_reason=None,
            status="PUBLIC_VERIFIED",
        )
        for day_num in range(1, 31)
    ]
    oct_days = [
        DaySchedule(
            day=date(2026, 10, day_num),
            weekday="mon",
            holiday=None,
            lunch=None,
            dinner=ServiceHours("17:00", "23:00", "22:30", "22:30"),
            closed_reason=None,
            status="PUBLIC_VERIFIED",
        )
        for day_num in range(1, 32)
    ]
    warnings_sep = labor_warnings([shift], sep_days)
    assert len(warnings_sep) == 0

    warnings_oct = labor_warnings([shift], oct_days)
    assert len(warnings_oct) == 1
    assert "2026-10-01" in warnings_oct[0]
    assert "1,280" in warnings_oct[0] or "1280" in warnings_oct[0]
    assert "1,250" in warnings_oct[0] or "1250" in warnings_oct[0]
    assert "part_time" in warnings_oct[0]


def test_tc_f5_06_statutory_benefits_excludes_commuting_allowance() -> None:
    """TC-F5-06: 法定福利費は給与・賞与・アルバイト賃金に掛かり通勤費には掛からない."""
    staff = [StaffRole(role="manager", count=1, monthly_salary=300000)]
    breakdown = labor_breakdown(
        full_time_staff=staff,
        bonus_months_per_year=1,
        part_time=Decimal("1000000"),
        commute=Decimal("120000"),
        statutory_rate=Decimal("0.15"),
    )
    assert breakdown.full_time == Decimal("3600000")
    assert breakdown.bonus == Decimal("300000")
    assert breakdown.part_time == Decimal("1000000")
    assert breakdown.commute == Decimal("120000")
    assert breakdown.statutory == Decimal("735000")
    assert breakdown.total == Decimal("5755000")


def test_tc_f5_07_cost_of_goods_denominator_is_ex_tax_including_appetizer() -> None:
    """TC-F5-07: 原価率は税抜売上（お通しを含む）に掛ける (OPT-8)."""
    month = MonthRevenue(
        year=2026,
        month=1,
        lunch_inc_tax=0,
        dinner_inc_tax=17600,
        ala_inc_tax=11000,
        course_inc_tax=5500,
        appetizer_inc_tax=1100,
        other_inc_tax=0,
        tax_10_inc=17600,
        tax_8_inc=0,
    )
    cogs = cost_of_goods(
        month,
        cost_rate_ala=Decimal("0.3"),
        cost_rate_course=Decimal("0.4"),
    )
    assert cogs == 5300


def test_monthly_pnl_stages_sum_to_annual() -> None:
    """追加テスト: test_monthly_pnl_stages_sum_to_annual.

    検知バグ: 月次損益の各段階・CFの和と年額の一致.
    """
    months = [
        PnL(
            gross_inc_tax=1_100_000,
            gross_ex_tax=1_000_000,
            cogs=Decimal("300000"),
            labor=Decimal("400000"),
            owner_labor=Decimal("50000"),
            fixed=FixedCosts(
                rent=Decimal("100000"),
                depreciation=Decimal("10000"),
                interest=Decimal("5000"),
                principal=Decimal("20000"),
                capex=Decimal("0"),
                other={"utilities": Decimal("20000")},
            ),
            year=2026,
            month=i,
        )
        for i in range(1, 13)
    ]
    monthly_pnl = build_monthly_pnl(months)
    assert len(monthly_pnl.months) == 12
    assert monthly_pnl.gross_inc_tax == 12 * 1_100_000
    assert monthly_pnl.gross_ex_tax == 12 * 1_000_000
    assert monthly_pnl.cogs == Decimal(12 * 300000)
    assert monthly_pnl.labor == Decimal(12 * 400000)
    assert monthly_pnl.owner_labor == Decimal(12 * 50000)
    assert monthly_pnl.gross_profit == sum(m.gross_profit for m in monthly_pnl.months)
    assert monthly_pnl.store_operating_profit == sum(
        m.store_operating_profit for m in monthly_pnl.months
    )
    assert monthly_pnl.business_income == sum(
        m.business_income for m in monthly_pnl.months
    )
    assert monthly_pnl.cash_flow == sum(m.cash_flow for m in monthly_pnl.months)
    assert monthly_pnl.yen(monthly_pnl.cash_flow) == sum(
        m.yen(m.cash_flow) for m in monthly_pnl.months
    )


def test_tc_f5_08_monthly_pnl_stages_and_cf_sum_to_annual_exact_yen() -> None:
    """TC-F5-08: 実データバードマン標準で月次損益12ヶ月の各段階・CFの和と年額が一致.

    円単位で完全一致することを検証する.
    """
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        year=2026,
        calendar_mode="steady",
        assumption_set="standard",
        replications=2,
        seed=42,
        overrides={"layout.floor2": "f2_42"},
    )
    result = evaluate(DATA_DIR, req)[0]
    pnl = result.pnl
    assert len(pnl.months) == 12
    stages = [
        "gross_profit",
        "store_operating_profit",
        "business_income",
        "cash_flow",
    ]
    for stage in stages:
        annual_val = pnl.yen(getattr(pnl, stage))
        months_sum = sum(m.yen(getattr(m, stage)) for m in pnl.months)
        assert annual_val == months_sum, (
            f"{stage} mismatch: {annual_val} != {months_sum}"
        )


def test_tc_f5_09_labor_breakdown_matches_components_and_pnl_labor() -> None:
    """TC-F5-09: 実データバードマン標準で labor_breakdown の内訳検証.

    内訳の和＝人件費、アルバイト=7,900,200、正社員=7,200,000.
    """
    req = EvaluationRequest(
        store_id="birdman_tamachi",
        year=2026,
        calendar_mode="steady",
        assumption_set="standard",
        replications=2,
        seed=42,
        overrides={"layout.floor2": "f2_42"},
    )
    result = evaluate(DATA_DIR, req)[0]
    assert result.labor_breakdown is not None
    lb = result.labor_breakdown
    assert lb.part_time == Decimal("7900200")
    assert lb.full_time == Decimal("7200000")
    assert lb.total == result.pnl.labor


def test_tc_f5_10_late_night_premium_under_125_rejected(tmp_path: Path) -> None:
    """TC-F5-10: late_night_premium: 1.2 の入力を検査してエラー."""
    data_copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_copy)

    std_path = (
        data_copy / "stores" / "birdman_tamachi" / "assumptions" / "standard.yaml"
    )
    content = yaml.safe_load(std_path.read_text(encoding="utf-8"))
    content["late_night_premium"]["value"] = 1.2
    if "range" in content["late_night_premium"]:
        content["late_night_premium"]["range"]["base"] = 1.2
    std_path.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")

    issues = validate_store(data_copy / "stores" / "birdman_tamachi")
    messages = [str(issue) for issue in issues]
    assert any("standard.yaml" in m and "late_night_premium" in m for m in messages)


def test_part_time_shifts_yaml_invalid_weekday_rejected(tmp_path: Path) -> None:
    """追加テスト: YAML で weekdays に thur が指定されたら validate_store がエラー."""
    data_copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_copy)

    std_path = (
        data_copy / "stores" / "birdman_tamachi" / "assumptions" / "standard.yaml"
    )
    content = yaml.safe_load(std_path.read_text(encoding="utf-8"))
    content["part_time_shifts"]["value"][0]["weekdays"] = ["thur"]
    std_path.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")

    issues = validate_store(data_copy / "stores" / "birdman_tamachi")
    messages = [str(issue) for issue in issues]
    assert any("part_time_shifts" in m and "thur" in m for m in messages)


def test_part_time_shifts_yaml_negative_heads_rejected(tmp_path: Path) -> None:
    """追加テスト: YAML で heads が -1 なら validate_store がエラー."""
    data_copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_copy)

    std_path = (
        data_copy / "stores" / "birdman_tamachi" / "assumptions" / "standard.yaml"
    )
    content = yaml.safe_load(std_path.read_text(encoding="utf-8"))
    content["part_time_shifts"]["value"][0]["heads"] = -1
    std_path.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")

    issues = validate_store(data_copy / "stores" / "birdman_tamachi")
    messages = [str(issue) for issue in issues]
    assert any("part_time_shifts" in m and "heads" in m for m in messages)


def test_part_time_shifts_override_invalid_weekday_raises_override_error() -> None:
    """追加テスト: 上書きで weekdays に thur が指定されたら
    _validate_overrides が OverrideError.
    """
    req = RunRequest(
        overrides={
            "part_time_shifts": [
                {
                    "id": "lunch",
                    "weekdays": ["thur"],
                    "service": "lunch",
                    "start": "11:00",
                    "end": "14:00",
                    "heads": 2,
                    "hourly_wage": 1330,
                }
            ]
        }
    )
    with pytest.raises(OverrideError, match="part_time_shifts"):
        _validate_overrides(DATA_DIR, "birdman_tamachi", req)
