"""人件費・費用・損益 (TC-10-xx, TC-11-xx)."""

from datetime import date
from decimal import Decimal

import pytest

from restaurant_revenue_estimator.engine.cost import (
    FixedCosts,
    PartTimeShift,
    StaffRole,
    VariableCosts,
    full_time_cost,
    owner_labor,
    shift_cost,
    simple_monthly_labor,
    statutory_cost,
    variable_costs,
    wage_warnings,
)
from restaurant_revenue_estimator.engine.pnl import (
    break_even_sales,
    build_pnl,
    jfc_comparison,
)


def test_evening_shift_with_late_premium() -> None:
    cost = shift_cost(Decimal(18), Decimal(23), Decimal(1330), Decimal("1.25"))
    assert cost == Decimal("6982.5")


def test_overnight_shift_all_hours_are_premium() -> None:
    cost = shift_cost(Decimal(22), Decimal(1), Decimal(1330), Decimal("1.25"))
    assert cost == Decimal(3) * Decimal(1330) * Decimal("1.25")


def test_full_time_counted_once_even_if_assigned_twice() -> None:
    roles = [StaffRole("manager", 1, 350000), StaffRole("manager", 1, 350000)]
    assert full_time_cost(roles) == Decimal(350000)


def test_statutory_itemized_and_flat_rate_is_an_error() -> None:
    with pytest.raises(ValueError):
        statutory_cost(Decimal(100), itemized=Decimal(10), flat_rate=Decimal("0.1"))


def test_minimum_wage_warning_switches_on_2026_10_01() -> None:
    shift = PartTimeShift(heads=1, hours_per_day=Decimal(5), hourly_wage=Decimal(1250))
    assert wage_warnings(shift, date(2026, 10, 15))
    assert not wage_warnings(shift, date(2026, 9, 15))


def test_owner_hours_change_only_owner_labor() -> None:
    zero = owner_labor(Decimal(0), Decimal(2000))
    full = owner_labor(Decimal(240), Decimal(2000))
    assert zero == 0
    assert full == Decimal(480000)


def test_zero_guests_make_variable_cost_zero() -> None:
    costs = variable_costs(
        ala_revenue=0,
        course_revenue=0,
        cost_rate_ala=Decimal("0.32"),
        cost_rate_course=Decimal("0.32"),
    )
    assert costs.total == 0


def test_simple_monthly_labor_matches_spec() -> None:
    cost = simple_monthly_labor(
        full_time_count=5,
        monthly_salary=350000,
        daily_heads=8,
        hours_per_day=Decimal(5),
        hourly_wage=Decimal(1330),
        days=30,
        overhead_rate=Decimal("0.15"),
    )
    assert cost == Decimal(3847900)


def _pnl(
    *, principal: Decimal = Decimal(0), depreciation: Decimal = Decimal(0)
) -> object:
    return build_pnl(
        gross_inc_tax=75_800_000,
        gross_ex_tax=68_909_091,
        variable=VariableCosts(food_ala=Decimal(22_740_000)),
        labor=Decimal(23_430_000),
        owner=Decimal(5_000_000),
        fixed=FixedCosts(
            rent=Decimal(9_600_000),
            other={"misc": Decimal(7_580_000)},
            depreciation=depreciation,
            principal=principal,
        ),
    )


def test_principal_repayment_changes_only_cash_flow() -> None:
    base = _pnl()
    repaid = _pnl(principal=Decimal(100_000))
    assert base.business_income == repaid.business_income  # type: ignore[attr-defined]
    assert base.cash_flow - repaid.cash_flow == Decimal(100_000)  # type: ignore[attr-defined]


def test_depreciation_reduces_profit_and_is_added_back_in_cash_flow() -> None:
    base = _pnl()
    depreciated = _pnl(depreciation=Decimal(50_000))
    assert base.store_operating_profit - depreciated.store_operating_profit == Decimal(
        50_000
    )  # type: ignore[attr-defined]
    assert base.cash_flow == depreciated.cash_flow  # type: ignore[attr-defined]


def test_owner_labor_is_separate_from_business_income() -> None:
    pnl = _pnl()
    assert not hasattr(pnl, "total_income")
    assert pnl.business_income == pnl.store_operating_profit  # type: ignore[attr-defined]
    assert pnl.owner_labor > 0  # type: ignore[attr-defined]


def test_pnl_stages_match_spec_arithmetic() -> None:
    pnl = _pnl()
    assert pnl.gross_ex_tax == 68_909_091  # type: ignore[attr-defined]
    assert pnl.yen(pnl.store_operating_profit) == 5_559_091  # type: ignore[attr-defined]


def test_jfc_labor_ratio_includes_owner_labor() -> None:
    pnl = build_pnl(
        gross_inc_tax=110,
        gross_ex_tax=100,
        variable=VariableCosts(),
        labor=Decimal(30),
        owner=Decimal(5),
        fixed=FixedCosts(),
    )
    comparison = jfc_comparison(pnl)
    assert round(comparison.labor_ratio, 3) == 0.318


def test_break_even_sales() -> None:
    assert break_even_sales(Decimal(2_000_000), Decimal("0.4")) == 3_333_333
