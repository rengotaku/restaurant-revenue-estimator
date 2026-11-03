"""実績とシナリオの突合のテスト."""

import json
from datetime import date
from pathlib import Path

import pytest

from restaurant_revenue_estimator.actuals import (
    ActualDay,
    ActualSeries,
    BacktestError,
    compare,
    load_predicted_days,
    render_markdown,
)


def make_actual(*revenues: int, store_id: str = "s1") -> ActualSeries:
    days = tuple(
        ActualDay(day=date(2023, 1, index + 1), revenue_inc_tax=value)
        for index, value in enumerate(revenues)
    )
    return ActualSeries(
        store_id=store_id,
        days=days,
        source_id="S99",
        license="CC BY 4.0",
        coverage_note="テスト用",
    )


def write_run(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


class TestLoadPredictedDays:
    """run JSON から日次売り上げを取り出す."""

    def test_selects_scenario_and_variant(self, tmp_path: Path) -> None:
        path = write_run(
            tmp_path,
            {
                "scenarios": [
                    {
                        "assumption_set": "pessimistic",
                        "name": "v1",
                        "days": [{"date": "2023-01-01", "total": 1}],
                    },
                    {
                        "assumption_set": "standard",
                        "name": "v1",
                        "days": [{"date": "2023-01-01", "total": 1000}],
                    },
                    {
                        "assumption_set": "standard",
                        "name": "v2",
                        "days": [{"date": "2023-01-01", "total": 2000}],
                    },
                ]
            },
        )
        series = load_predicted_days(path, scenario="standard", variant="v2")
        assert series.days[date(2023, 1, 1)] == 2000
        assert series.variant == "v2"

    def test_minimal_document(self, tmp_path: Path) -> None:
        path = write_run(
            tmp_path, {"days": [{"date": "2023-01-01", "total_inc_tax": 500}]}
        )
        assert load_predicted_days(path).days[date(2023, 1, 1)] == 500

    def test_unknown_scenario_lists_available(self, tmp_path: Path) -> None:
        path = write_run(
            tmp_path,
            {
                "scenarios": [
                    {
                        "assumption_set": "standard",
                        "name": "v1",
                        "days": [{"date": "2023-01-01", "total": 1}],
                    }
                ]
            },
        )
        with pytest.raises(BacktestError, match="standard"):
            load_predicted_days(path, scenario="optimistic")

    def test_minimal_document_rejects_other_scenario(self, tmp_path: Path) -> None:
        """days 直下でも、指定と違うシナリオを黙って返さない."""
        path = write_run(
            tmp_path,
            {
                "assumption_set": "standard",
                "days": [{"date": "2023-01-01", "total": 1}],
            },
        )
        with pytest.raises(BacktestError, match="standard"):
            load_predicted_days(path, scenario="pessimistic")

    def test_duplicate_predicted_date(self, tmp_path: Path) -> None:
        path = write_run(
            tmp_path,
            {
                "days": [
                    {"date": "2023-01-01", "total": 1},
                    {"date": "2023-01-01", "total": 2},
                ]
            },
        )
        with pytest.raises(BacktestError, match="重複"):
            load_predicted_days(path)

    def test_real_run_document_reads_variants(self, tmp_path: Path) -> None:
        """`rre run` の result.json は variants にだけ日次を持つ."""
        path = write_run(
            tmp_path,
            {
                "store_id": "s1",
                "assumption_set": "standard",
                "scenarios": [
                    {
                        "assumption_set": "standard",
                        "variant": "v1",
                        "gross_inc_tax": 1000,
                    }
                ],
                "variants": [
                    {
                        "name": "v1",
                        "assumption_set": "standard",
                        "days": [{"date": "2023-01-01", "total": 1000}],
                    }
                ],
            },
        )
        series = load_predicted_days(path, scenario="standard")
        assert series.days[date(2023, 1, 1)] == 1000
        assert series.store_id == "s1"

    def test_scenario_without_days_is_rejected(self, tmp_path: Path) -> None:
        """年間集計だけの scenario を日次として読まない."""
        path = write_run(
            tmp_path,
            {
                "assumption_set": "standard",
                "scenarios": [
                    {
                        "assumption_set": "pessimistic",
                        "variant": "v1",
                        "gross_inc_tax": 1000,
                    }
                ],
                "variants": [
                    {
                        "name": "v1",
                        "assumption_set": "standard",
                        "days": [{"date": "2023-01-01", "total": 1000}],
                    }
                ],
            },
        )
        with pytest.raises(BacktestError, match="日次"):
            load_predicted_days(path, scenario="pessimistic")


class TestCompare:
    """指標の計算."""

    def test_metrics(self, tmp_path: Path) -> None:
        actual = make_actual(1000, 2000, 3000)
        predicted = load_predicted_days(
            write_run(
                tmp_path,
                {
                    "days": [
                        {"date": "2023-01-01", "total": 1100},
                        {"date": "2023-01-02", "total": 1900},
                        {"date": "2023-01-03", "total": 3600},
                    ]
                },
            )
        )
        result = compare(actual, predicted)
        assert result.compared_days == 3
        assert result.mae == 267
        assert result.median_abs_error == 100
        assert result.bias == 200
        assert result.actual_total == 6000
        assert result.predicted_total == 6600
        assert result.relative_error == pytest.approx(0.1)

    def test_only_overlapping_days_are_compared(self, tmp_path: Path) -> None:
        actual = make_actual(1000, 2000)
        predicted = load_predicted_days(
            write_run(tmp_path, {"days": [{"date": "2023-01-02", "total": 2000}]})
        )
        result = compare(actual, predicted)
        assert result.compared_days == 1
        assert result.actual_only == 1
        assert result.predicted_only == 0

    def test_no_overlap_raises(self, tmp_path: Path) -> None:
        actual = make_actual(1000)
        predicted = load_predicted_days(
            write_run(tmp_path, {"days": [{"date": "2024-01-01", "total": 1}]})
        )
        with pytest.raises(BacktestError, match="重なる日がありません"):
            compare(actual, predicted)

    def test_relative_error_is_none_when_actual_is_zero(self, tmp_path: Path) -> None:
        actual = make_actual(0)
        predicted = load_predicted_days(
            write_run(tmp_path, {"days": [{"date": "2023-01-01", "total": 10}]})
        )
        assert compare(actual, predicted).relative_error is None

    def test_different_store_is_rejected(self, tmp_path: Path) -> None:
        """別店舗の run を実績に突き合わせない."""
        actual = make_actual(1000, store_id="s1")
        predicted = load_predicted_days(
            write_run(
                tmp_path,
                {
                    "store_id": "s2",
                    "days": [{"date": "2023-01-01", "total": 1000}],
                },
            )
        )
        with pytest.raises(BacktestError, match="店舗"):
            compare(actual, predicted)


class TestRender:
    """Markdown の見出しと注記."""

    def test_contains_caveat_and_metrics(self, tmp_path: Path) -> None:
        actual = make_actual(1000)
        predicted = load_predicted_days(
            write_run(tmp_path, {"days": [{"date": "2023-01-01", "total": 1200}]})
        )
        text = render_markdown(compare(actual, predicted))
        assert "実績との比較" in text
        assert "予測精度を主張しない" in text
        assert "区間被覆率" in text
        assert "CC BY 4.0" in text
        assert "テスト用" in text
        assert "200" in text
