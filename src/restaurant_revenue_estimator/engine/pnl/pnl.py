"""損益段階・CF・公庫照合・損益分岐点."""

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
