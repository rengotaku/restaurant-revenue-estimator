"""営業日カレンダー (TC-04-xx)."""

from datetime import date
from pathlib import Path

import pytest

from restaurant_revenue_estimator.engine.calendar import (
    CalendarError,
    build_calendars,
    load_holidays,
)

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "calendars" / "jp_holidays_cao.csv"
META = ROOT / "data" / "calendars" / "jp_holidays_cao.meta.yaml"

WEEKDAY_RULE = {
    "id": "current",
    "open_weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "open_on_holidays": False,
    "lunch": {
        "open": "11:30",
        "close": "13:30",
        "last_order": "13:00",
        "last_entry": "13:00",
    },
    "dinner": {
        "open": "17:30",
        "close": "22:30",
        "last_order": "22:00",
        "last_entry": "21:30",
    },
    "status": "PUBLIC_VERIFIED",
}

SATURDAY_RULE = dict(
    WEEKDAY_RULE, open_weekdays=[*WEEKDAY_RULE["open_weekdays"], "sat"]
)


def test_holidays_2026() -> None:
    holidays = load_holidays(CSV, META, 2026)
    assert len(holidays) == 18
    assert date(2026, 5, 6) in holidays
    assert date(2026, 9, 22) in holidays


def test_steady_weekday_rule() -> None:
    holidays = load_holidays(CSV, META, 2026)
    series = build_calendars(
        rules=[WEEKDAY_RULE], year=2026, mode="steady", holidays=holidays
    )[0]
    assert series.monthly_open_days() == [
        20,
        18,
        21,
        21,
        18,
        22,
        22,
        20,
        19,
        21,
        19,
        23,
    ]
    assert len(series.open_days) == 244


def test_exception_days_match_v5() -> None:
    holidays = load_holidays(CSV, META, 2026)
    exceptions = [
        {"date": date(2026, 1, 2), "closed": True, "status": "ASSUMPTION"},
        {"date": date(2026, 12, 29), "closed": True, "status": "ASSUMPTION"},
        {"date": date(2026, 12, 30), "closed": True, "status": "ASSUMPTION"},
        {"date": date(2026, 12, 31), "closed": True, "status": "ASSUMPTION"},
    ]
    series = build_calendars(
        rules=[WEEKDAY_RULE],
        year=2026,
        mode="steady",
        holidays=holidays,
        exceptions=exceptions,
    )[0]
    assert series.monthly_open_days() == [
        19,
        18,
        21,
        21,
        18,
        22,
        22,
        20,
        19,
        21,
        19,
        20,
    ]
    assert len(series.open_days) == 240


def test_historical_saturday_closure_2026() -> None:
    holidays = load_holidays(CSV, META, 2026)
    series = build_calendars(
        rules=[SATURDAY_RULE],
        year=2026,
        mode="historical",
        holidays=holidays,
        saturday_closure_candidates={"2026-09-01": date(2026, 9, 1)},
    )[0]
    assert series.monthly_open_days() == [
        25,
        22,
        25,
        25,
        23,
        26,
        26,
        25,
        19,
        21,
        19,
        23,
    ]
    assert len(series.open_days) == 279


def test_historical_saturday_closure_2025_equals_steady() -> None:
    holidays = load_holidays(CSV, META, 2026)
    series = build_calendars(
        rules=[SATURDAY_RULE],
        year=2026,
        mode="historical",
        holidays=holidays,
        saturday_closure_candidates={"2025-09-01": date(2025, 9, 1)},
    )[0]
    assert series.monthly_open_days() == [
        20,
        18,
        21,
        21,
        18,
        22,
        22,
        20,
        19,
        21,
        19,
        23,
    ]


def test_unresolved_start_creates_two_series() -> None:
    holidays = load_holidays(CSV, META, 2026)
    series = build_calendars(
        rules=[SATURDAY_RULE],
        year=2026,
        mode="historical",
        holidays=holidays,
        saturday_closure_candidates={
            "2025-09-01": date(2025, 9, 1),
            "2026-09-01": date(2026, 9, 1),
        },
    )
    assert len(series) == 2
    assert {item.name for item in series} == {"2025-09-01", "2026-09-01"}


def test_csv_checksum_mismatch(tmp_path: Path) -> None:
    meta = tmp_path / "meta.yaml"
    meta.write_text(
        "sha256: deadbeef\nencoding: Shift_JIS\nyears: [2026]\n", encoding="utf-8"
    )
    with pytest.raises(CalendarError):
        load_holidays(CSV, meta, 2026)


def test_missing_year_is_an_error() -> None:
    with pytest.raises(CalendarError):
        load_holidays(CSV, META, 2030)


def test_torahachi_open_every_day() -> None:
    holidays = load_holidays(CSV, META, 2026)
    rule = {
        "id": "current",
        "open_weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "open_on_holidays": True,
        "dinner": {
            "open": "16:00",
            "close": "23:20",
            "last_order": "22:30",
            "last_entry": "22:00",
        },
        "sunday_hours": {
            "open": "16:00",
            "close": "23:00",
            "last_order": "22:00",
            "last_entry": "21:30",
        },
        "holiday_hours": {
            "open": "16:00",
            "close": "23:00",
            "last_order": "22:00",
            "last_entry": "21:30",
        },
        "status": "PUBLIC_VERIFIED",
    }
    series = build_calendars(rules=[rule], year=2026, mode="steady", holidays=holidays)[
        0
    ]
    assert len(series.open_days) == 365
    short_days = [
        day for day in series.open_days if day.dinner and day.dinner.close == "23:00"
    ]
    assert len(short_days) == 69
