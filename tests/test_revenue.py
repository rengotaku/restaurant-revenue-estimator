"""売上恒等式と集計 (TC-05-xx)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from restaurant_revenue_estimator.engine.revenue import (
    CourseSales,
    DaySales,
    PricePeriod,
    SeasonalityError,
    SeasonalityRegistry,
    ServiceSales,
    aggregate,
    price_at,
    realized_course_price,
)

DAY = date(2026, 1, 5)


def _days(count: int, lunch: ServiceSales, dinner: ServiceSales) -> list[DaySales]:
    return [
        DaySales(DAY + timedelta(days=index), lunch, dinner) for index in range(count)
    ]


def test_zero_guests_zero_revenue() -> None:
    result = aggregate(_days(30, ServiceSales(), ServiceSales()))
    assert result.total_inc_tax == 0


def test_lunch_does_not_change_dinner() -> None:
    dinner = ServiceSales(ala_guests=10, ala_ticket=5000)
    before = aggregate(
        [DaySales(DAY, ServiceSales(ala_guests=5, ala_ticket=1100), dinner)]
    )
    after = aggregate(
        [DaySales(DAY, ServiceSales(ala_guests=9, ala_ticket=1100), dinner)]
    )
    assert before.dinner_inc_tax == after.dinner_inc_tax


def test_ala_and_course_guests_do_not_overlap() -> None:
    service = ServiceSales(
        ala_guests=30,
        ala_ticket=4500,
        courses=(CourseSales(guests=12, unit_price=5700),),
    )
    assert service.guests == 42


def test_appetizer_is_waived_for_course_guests() -> None:
    courses = ServiceSales(
        courses=(CourseSales(guests=10, unit_price=5700),),
        appetizer_yen=450,
        appetizer_waived_for_course=True,
    )
    ala = ServiceSales(
        ala_guests=10,
        ala_ticket=4500,
        appetizer_yen=450,
        appetizer_waived_for_course=True,
    )
    assert courses.appetizer_revenue == 0
    assert ala.appetizer_revenue == 4500


def test_drink_is_not_added_to_course() -> None:
    course = ServiceSales(
        courses=(CourseSales(guests=4, unit_price=5700, includes_drink=True),)
    )
    without = ServiceSales(
        courses=(CourseSales(guests=4, unit_price=5700, includes_drink=True),),
        ala_ticket=0,
    )
    assert course.total_inc_tax == without.total_inc_tax == 22800


def test_realized_course_price() -> None:
    assert realized_course_price(6500, 5500, Decimal("0.8")) == 5700


def test_reducing_night_guests_reduces_year_by_expected_amount() -> None:
    base = _days(240, ServiceSales(), ServiceSales(ala_guests=30, ala_ticket=5000))
    reduced = _days(240, ServiceSales(), ServiceSales(ala_guests=20, ala_ticket=5000))
    assert (
        aggregate(base).total_inc_tax - aggregate(reduced).total_inc_tax == 12_000_000
    )


def test_seasonality_on_guests_and_revenue_is_an_error() -> None:
    registry = SeasonalityRegistry()
    registry.apply("summer", Decimal("1.1"), "guests")
    with pytest.raises(SeasonalityError):
        registry.apply("summer", Decimal("1.1"), "revenue")


def test_price_change_mid_month() -> None:
    periods = [
        PricePeriod(date(2026, 1, 1), 1100),
        PricePeriod(date(2026, 6, 15), 1200),
    ]
    assert price_at(periods, date(2026, 6, 14)) == 1100
    assert price_at(periods, date(2026, 6, 15)) == 1200


def test_tax_exclusive_conversion() -> None:
    ten = ServiceSales(ala_guests=1, ala_ticket=11000, tax_rate=Decimal("0.10"))
    eight = ServiceSales(ala_guests=1, ala_ticket=10800, tax_rate=Decimal("0.08"))
    result = aggregate([DaySales(date(2026, 1, 5), ten, eight)])
    assert result.total_ex_tax == 20000
    assert result.months[0].total_ex_tax == 20000


def test_month_sum_equals_year_and_lunch_plus_dinner_equals_total() -> None:
    days = _days(
        60,
        ServiceSales(ala_guests=10, ala_ticket=1100),
        ServiceSales(
            ala_guests=8,
            ala_ticket=4500,
            courses=(CourseSales(guests=4, unit_price=5700),),
        ),
    )
    result = aggregate(days)
    assert sum(month.total_inc_tax for month in result.months) == result.total_inc_tax
    assert (
        sum(month.lunch_inc_tax + month.dinner_inc_tax for month in result.months)
        == result.total_inc_tax
    )
    assert result.lunch_inc_tax + result.dinner_inc_tax == result.total_inc_tax


def test_amounts_are_int() -> None:
    result = aggregate(
        _days(
            3,
            ServiceSales(ala_guests=3, ala_ticket=1100),
            ServiceSales(ala_guests=2, ala_ticket=4500),
        )
    )
    assert isinstance(result.total_inc_tax, int)
    assert isinstance(result.total_ex_tax, int)
    for month in result.months:
        assert isinstance(month.total_inc_tax, int)
        assert isinstance(month.total_ex_tax, int)
