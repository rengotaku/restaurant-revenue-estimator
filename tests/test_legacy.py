"""過去の試算 v5 の検査 (TC-06-xx)."""

from pathlib import Path

import pytest

from restaurant_revenue_estimator.inputs import load_store
from restaurant_revenue_estimator.inputs.legacy import (
    LegacyError,
    assert_not_legacy,
    check_legacy,
    format_legacy_check,
    load_legacy,
)

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "stores" / "birdman_tamachi"


def test_row_mismatches_are_only_october_and_december() -> None:
    check = check_legacy(STORE, ROOT / "data")
    assert [(row.month, row.diff) for row in check.row_mismatches] == [(10, 1), (12, 1)]


def test_column_total_diff_is_twenty_thousand_yen() -> None:
    check = check_legacy(STORE, ROOT / "data")
    assert check.total_lunch_dinner == 7582
    assert check.total_reported == 7580
    assert check.total_mismatch == 2


def test_business_day_diff_against_calendar() -> None:
    check = check_legacy(STORE, ROOT / "data")
    diffs = {diff.month: diff.diff for diff in check.day_diffs}
    assert diffs[1] == -1
    assert diffs[12] == -3
    assert all(value == 0 for month, value in diffs.items() if month not in (1, 12))
    assert check.hidden_closed_days == 4


def test_operating_surplus_recalculation() -> None:
    check = check_legacy(STORE, ROOT / "data")
    assert check.arithmetic == 556


def test_legacy_is_not_an_input() -> None:
    inputs = load_store(STORE)
    assert not any(name.startswith("legacy/") for name in inputs.files)
    legacy = load_legacy(STORE)
    with pytest.raises(LegacyError):
        assert_not_legacy(legacy)


def test_output_starts_with_not_actual() -> None:
    lines = format_legacy_check(check_legacy(STORE, ROOT / "data"))
    assert "実績ではない" in lines[0]
    assert any("出典が無い" in line for line in lines)
