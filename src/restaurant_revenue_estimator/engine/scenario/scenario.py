"""シナリオ・感度分析・調査の優先順位."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.services.evaluation import (
    EvaluationRequest,
    EvaluationResult,
    evaluate,
    plain,
)

SCENARIOS = ("pessimistic", "standard", "optimistic")


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    gross_inc_tax: int
    gross_ex_tax: int
    business_income: int
    result: EvaluationResult


@dataclass(frozen=True)
class SensitivityRow:
    key: str
    value_name: str
    value: Any
    delta_revenue: int
    delta_income: int
    how_to_verify: str

    @property
    def magnitude(self) -> int:
        return max(abs(self.delta_revenue), abs(self.delta_income))

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value_name": self.value_name,
            "value": self.value,
            "delta_revenue": self.delta_revenue,
            "delta_income": self.delta_income,
            "how_to_verify": self.how_to_verify,
        }


def compare_assumption_sets(
    data_dir: Path, base: EvaluationRequest
) -> list[ScenarioResult]:
    """悲観・標準・楽観で回す."""
    results: list[ScenarioResult] = []
    for name in SCENARIOS:
        request = EvaluationRequest(
            store_id=base.store_id,
            assumption_set=name,
            year=base.year,
            calendar_mode=base.calendar_mode,
            seed=base.seed,
            replications=base.replications,
            overrides=base.overrides,
            saturday_closure=base.saturday_closure,
        )
        result = evaluate(data_dir, request)[0]
        results.append(
            ScenarioResult(
                name=name,
                gross_inc_tax=result.gross_inc_tax,
                gross_ex_tax=result.gross_ex_tax,
                business_income=result.pnl.yen(result.pnl.business_income),
                result=result,
            )
        )
    return results


def sensitivity(
    data_dir: Path,
    request: EvaluationRequest,
    *,
    max_rows: int | None = None,
) -> list[SensitivityRow]:
    """ASSUMPTION は low/high、UNRESOLVED は候補ごとに振る. 同じ seed を使う."""
    small = EvaluationRequest(
        store_id=request.store_id,
        assumption_set=request.assumption_set,
        year=request.year,
        calendar_mode=request.calendar_mode,
        seed=request.seed,
        replications=max(1, min(request.replications, 5)),
        overrides=request.overrides,
        saturday_closure=request.saturday_closure,
        saturday_hours_candidate=request.saturday_hours_candidate,
    )
    baseline = evaluate(data_dir, small)[0]
    inputs = load_store(data_dir / "stores" / request.store_id)
    assumptions = (
        inputs.files.get(f"assumptions/{request.assumption_set}.yaml", {}) or {}
    )

    floor2_choice = baseline.choices.get("layout.floor2") or baseline.variant
    closure_choice = small.saturday_closure or baseline.choices.get(
        "saturday_closure_start"
    )
    hours_choice = small.saturday_hours_candidate or baseline.choices.get(
        "saturday_hours_before_closure"
    )

    row_overrides = dict(small.overrides or {})
    if "layout.floor2" not in row_overrides and floor2_choice:
        row_overrides["layout.floor2"] = floor2_choice

    row_small = EvaluationRequest(
        store_id=small.store_id,
        assumption_set=small.assumption_set,
        year=small.year,
        calendar_mode=small.calendar_mode,
        seed=small.seed,
        replications=small.replications,
        overrides=row_overrides,
        saturday_closure=closure_choice,
        saturday_hours_candidate=hours_choice,
    )

    rows: list[SensitivityRow] = []
    for key, raw in assumptions.items():
        status = raw.get("status")
        how_to_verify = str(raw.get("how_to_verify") or "YAML の注記を確認する")
        if status == "ASSUMPTION" and isinstance(raw.get("range"), dict):
            for name in ("low", "high"):
                value = raw["range"].get(name)
                rows.append(
                    _row(data_dir, row_small, baseline, key, name, value, how_to_verify)
                )
        elif status == "UNRESOLVED":
            for name, value in (raw.get("candidates") or {}).items():
                rows.append(
                    _row(
                        data_dir,
                        row_small,
                        baseline,
                        key,
                        str(name),
                        value,
                        how_to_verify,
                    )
                )

    layout = inputs.files.get("layout.yaml", {}) or {}
    floors = layout.get("floors")
    if isinstance(floors, list):
        for floor in floors:
            tables = floor.get("tables")
            if isinstance(tables, dict) and tables.get("status") == "UNRESOLVED":
                for name in tables.get("candidates") or {}:
                    override = dict(request.overrides or {})
                    override["layout.floor2"] = name
                    candidate_request = EvaluationRequest(
                        store_id=small.store_id,
                        assumption_set=small.assumption_set,
                        year=small.year,
                        calendar_mode=small.calendar_mode,
                        seed=small.seed,
                        replications=small.replications,
                        overrides=override,
                        saturday_closure=closure_choice,
                        saturday_hours_candidate=hours_choice,
                    )
                    result = evaluate(data_dir, candidate_request)[0]
                    rows.append(
                        SensitivityRow(
                            key="layout.floor2",
                            value_name=str(name),
                            value=name,
                            delta_revenue=result.gross_inc_tax - baseline.gross_inc_tax,
                            delta_income=result.pnl.yen(result.pnl.business_income)
                            - baseline.pnl.yen(baseline.pnl.business_income),
                            how_to_verify=str(
                                tables.get("how_to_verify")
                                or "2 階の卓構成を現地で数える"
                            ),
                        )
                    )

    rows.sort(key=lambda row: row.magnitude, reverse=True)
    return rows[:max_rows] if max_rows else rows


def _row(
    data_dir: Path,
    request: EvaluationRequest,
    baseline: EvaluationResult,
    key: str,
    name: str,
    value: Any,
    how_to_verify: str,
) -> SensitivityRow:
    overrides = dict(request.overrides or {})
    floor2_choice = baseline.choices.get("layout.floor2") or baseline.variant
    if "layout.floor2" not in overrides and floor2_choice:
        overrides["layout.floor2"] = floor2_choice
    overrides[key] = value
    closure_choice = request.saturday_closure or baseline.choices.get(
        "saturday_closure_start"
    )
    hours_choice = request.saturday_hours_candidate or baseline.choices.get(
        "saturday_hours_before_closure"
    )
    candidate = EvaluationRequest(
        store_id=request.store_id,
        assumption_set=request.assumption_set,
        year=request.year,
        calendar_mode=request.calendar_mode,
        seed=request.seed,
        replications=request.replications,
        overrides=overrides,
        saturday_closure=closure_choice,
        saturday_hours_candidate=hours_choice,
    )
    result = evaluate(data_dir, candidate)[0]
    return SensitivityRow(
        key=key,
        value_name=name,
        value=plain(value),
        delta_revenue=result.gross_inc_tax - baseline.gross_inc_tax,
        delta_income=result.pnl.yen(result.pnl.business_income)
        - baseline.pnl.yen(baseline.pnl.business_income),
        how_to_verify=how_to_verify,
    )


def research_priorities(rows: list[SensitivityRow]) -> list[SensitivityRow]:
    """感度の上位から、確かめ方つきで並べる."""
    return [row for row in rows if row.how_to_verify]
