"""損益段階・CF・公庫照合・損益分岐点."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from restaurant_revenue_estimator.engine.cost import FixedCosts, VariableCosts


@dataclass(frozen=True)
class PnL:
    gross_inc_tax: int
    gross_ex_tax: int
    cogs: Decimal
    labor: Decimal
    owner_labor: Decimal
    fixed: FixedCosts
    sales_tax: int = 0
    year: int = 0
    month: int = 0

    @property
    def gross_profit(self) -> Decimal:
        return Decimal(self.gross_ex_tax) - self.cogs

    @property
    def store_operating_profit(self) -> Decimal:
        return self.gross_profit - self.labor - self.fixed.operating_total

    @property
    def business_income(self) -> Decimal:
        return self.store_operating_profit - self.fixed.interest

    @property
    def cash_flow(self) -> Decimal:
        return (
            self.business_income
            + self.fixed.depreciation
            - self.fixed.principal
            - self.fixed.capex
        )

    def yen(self, value: Decimal) -> int:
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class MonthlyPnL(PnL):
    """月次損益と年間集計 (OPT-8 / ARCHITECTURE 6.2)."""

    months: tuple[PnL, ...] = ()

    @property
    def annual(self) -> PnL:
        return self


@dataclass(frozen=True)
class JfcComparison:
    gross_profit_ratio: float
    labor_ratio: float
    operating_margin: float


def jfc_comparison(pnl: PnL) -> JfcComparison:
    """公庫の定義（税込売上が分母、人件費に店主の労働対価を含む）に換算する."""
    denominator = Decimal(pnl.gross_inc_tax) or Decimal(1)
    return JfcComparison(
        gross_profit_ratio=float(pnl.gross_profit / denominator),
        labor_ratio=float((pnl.labor + pnl.owner_labor) / denominator),
        operating_margin=float(pnl.store_operating_profit / denominator),
    )


def break_even_sales(fixed_cost: Decimal, variable_rate: Decimal) -> int:
    """損益分岐点（参考値）. 実際の売上の証拠ではない."""
    if not Decimal(0) <= variable_rate < Decimal(1):
        raise ValueError("変動費率は 0〜1 未満")
    return int(
        (fixed_cost / (Decimal(1) - variable_rate)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def build_pnl(
    *,
    gross_inc_tax: int,
    gross_ex_tax: int,
    variable: VariableCosts,
    labor: Decimal,
    owner: Decimal,
    fixed: FixedCosts,
) -> PnL:
    return PnL(
        gross_inc_tax=gross_inc_tax,
        gross_ex_tax=gross_ex_tax,
        cogs=variable.total,
        labor=labor,
        owner_labor=owner,
        fixed=fixed,
    )


def build_monthly_pnl(months: Sequence[PnL]) -> MonthlyPnL:
    """12 か月分の月次損益から年間損益（月の和）を作る."""
    if not months:
        raise ValueError("月次損益が空")
    gross_inc_tax = sum(m.gross_inc_tax for m in months)
    gross_ex_tax = sum(m.gross_ex_tax for m in months)
    cogs = sum((m.cogs for m in months), Decimal(0))
    labor = sum((m.labor for m in months), Decimal(0))
    owner_labor = sum((m.owner_labor for m in months), Decimal(0))
    sales_tax = sum(m.sales_tax for m in months)

    rent = sum((m.fixed.rent for m in months), Decimal(0))
    independence = sum((m.fixed.independence for m in months), Decimal(0))
    depreciation = sum((m.fixed.depreciation for m in months), Decimal(0))
    interest = sum((m.fixed.interest for m in months), Decimal(0))
    principal = sum((m.fixed.principal for m in months), Decimal(0))
    capex = sum((m.fixed.capex for m in months), Decimal(0))
    other_keys = {k for m in months for k in m.fixed.other}
    other = {
        k: sum((m.fixed.other.get(k, Decimal(0)) for m in months), Decimal(0))
        for k in other_keys
    }
    annual_fixed = FixedCosts(
        rent=rent,
        other=other,
        independence=independence,
        depreciation=depreciation,
        interest=interest,
        principal=principal,
        capex=capex,
    )

    year = months[0].year if months else 0
    return MonthlyPnL(
        gross_inc_tax=gross_inc_tax,
        gross_ex_tax=gross_ex_tax,
        cogs=cogs,
        labor=labor,
        owner_labor=owner_labor,
        fixed=annual_fixed,
        sales_tax=sales_tax,
        year=year,
        month=0,
        months=tuple(months),
    )
