"""売上画面の「実績との比較」の組み立てのテスト."""

from pathlib import Path

from restaurant_revenue_estimator.services.backtest_view import build_backtest_view

META = "source_id: S99\nlicense: CC BY 4.0\ncoverage_note: テスト用\n"


def write_store(tmp_path: Path, csv_text: str) -> Path:
    store = tmp_path / "s1"
    actuals = store / "actuals"
    actuals.mkdir(parents=True)
    (actuals / "actuals.meta.yaml").write_text(META, encoding="utf-8")
    (actuals / "2023-01.csv").write_text(csv_text, encoding="utf-8")
    return store


def days(*values: tuple[str, int]) -> list[dict[str, object]]:
    return [{"date": day, "total": total} for day, total in values]


class TestBuildBacktestView:
    """実績があるときだけ比較を返す."""

    def test_returns_none_without_actuals(self, tmp_path: Path) -> None:
        assert (
            build_backtest_view(
                store_dir=tmp_path,
                days=days(("2023-01-01", 1000)),
                scenario="standard",
                variant="v1",
                store_id="s1",
            )
            is None
        )

    def test_builds_metrics_and_worst_days(self, tmp_path: Path) -> None:
        store = write_store(
            tmp_path,
            "date,revenue_inc_tax\n2023-01-01,1000\n2023-01-02,2000\n2023-01-03,3000\n",
        )
        view = build_backtest_view(
            store_dir=store,
            days=days(
                ("2023-01-01", 1100),
                ("2023-01-02", 1900),
                ("2023-01-03", 3600),
            ),
            scenario="standard",
            variant="v1",
            store_id="s1",
        )
        assert view is not None
        assert view["note"] is None
        result = view["result"]
        assert result.mae == 267
        assert result.bias == 200
        assert [row.day.isoformat() for row in view["worst"]] == [
            "2023-01-03",
            "2023-01-01",
            "2023-01-02",
        ]

    def test_note_when_no_days(self, tmp_path: Path) -> None:
        store = write_store(tmp_path, "date,revenue_inc_tax\n2023-01-01,1000\n")
        view = build_backtest_view(
            store_dir=store,
            days=[],
            scenario="standard",
            variant="v1",
            store_id="s1",
        )
        assert view is not None
        assert view["result"] is None
        assert "日次売上" in view["note"]

    def test_note_when_store_differs(self, tmp_path: Path) -> None:
        store = write_store(tmp_path, "date,revenue_inc_tax\n2023-01-01,1000\n")
        view = build_backtest_view(
            store_dir=store,
            days=days(("2023-01-01", 1000)),
            scenario="standard",
            variant="v1",
            store_id="other",
        )
        assert view is not None
        assert view["result"] is None
        assert "店舗" in view["note"]

    def test_note_when_no_overlap(self, tmp_path: Path) -> None:
        store = write_store(tmp_path, "date,revenue_inc_tax\n2023-01-01,1000\n")
        view = build_backtest_view(
            store_dir=store,
            days=days(("2024-01-01", 1000)),
            scenario="standard",
            variant="v1",
            store_id="s1",
        )
        assert view is not None
        assert view["result"] is None
        assert "重なる日" in view["note"]

    def test_note_when_meta_is_broken(self, tmp_path: Path) -> None:
        store = write_store(tmp_path, "date,revenue_inc_tax\n2023-01-01,1000\n")
        (store / "actuals" / "actuals.meta.yaml").write_text(
            "source_id: S99\n", encoding="utf-8"
        )
        view = build_backtest_view(
            store_dir=store,
            days=days(("2023-01-01", 1000)),
            scenario="standard",
            variant="v1",
            store_id="s1",
        )
        assert view is not None
        assert view["result"] is None
        assert "メタ情報" in view["note"]
