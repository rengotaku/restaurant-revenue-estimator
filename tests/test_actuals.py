"""実績（POS）の読み込みのテスト."""

from pathlib import Path

import pytest

from restaurant_revenue_estimator.actuals import ActualsError, load_actuals

META = (
    "source_id: S99\n"
    "license: CC BY 4.0\n"
    "coverage_note: テスト用。モバイルオーダーのみ。\n"
)


def write_actuals(
    store_dir: Path, csv_text: str, meta: str = META, name: str = "2023-01.csv"
) -> Path:
    actuals = store_dir / "actuals"
    actuals.mkdir(parents=True, exist_ok=True)
    (actuals / "actuals.meta.yaml").write_text(meta, encoding="utf-8")
    (actuals / name).write_text(csv_text, encoding="utf-8")
    return store_dir


class TestLoadActuals:
    """正常系."""

    def test_reads_days_sorted(self, tmp_path: Path) -> None:
        store = write_actuals(
            tmp_path / "s1",
            "date,revenue_inc_tax,orders\n2023-01-02,1200,3\n2023-01-01,1000,2\n",
        )
        series = load_actuals(store)
        assert [day.day.isoformat() for day in series.days] == [
            "2023-01-01",
            "2023-01-02",
        ]
        assert series.total_inc_tax == 2200
        assert series.days[0].orders == 2
        assert series.store_id == "s1"

    def test_concatenates_multiple_files(self, tmp_path: Path) -> None:
        store = write_actuals(
            tmp_path / "s1", "date,revenue_inc_tax\n2023-01-01,1000\n", name="a.csv"
        )
        (store / "actuals" / "b.csv").write_text(
            "date,revenue_inc_tax\n2023-01-02,2000\n", encoding="utf-8"
        )
        series = load_actuals(store)
        assert series.total_inc_tax == 3000

    def test_accepts_thousands_separator(self, tmp_path: Path) -> None:
        store = write_actuals(
            tmp_path / "s1", 'date,revenue_inc_tax\n2023-01-01,"1,000"\n'
        )
        assert load_actuals(store).total_inc_tax == 1000


class TestRejects:
    """壊れた実績を受け入れない."""

    def test_missing_meta(self, tmp_path: Path) -> None:
        store = write_actuals(tmp_path / "s1", "date,revenue_inc_tax\n2023-01-01,1\n")
        (store / "actuals" / "actuals.meta.yaml").unlink()
        with pytest.raises(ActualsError, match="メタ情報がありません"):
            load_actuals(store)

    def test_empty_meta_key(self, tmp_path: Path) -> None:
        store = write_actuals(
            tmp_path / "s1",
            "date,revenue_inc_tax\n2023-01-01,1\n",
            meta="source_id: S99\nlicense: ''\ncoverage_note: x\n",
        )
        with pytest.raises(ActualsError, match="license"):
            load_actuals(store)

    def test_duplicate_date(self, tmp_path: Path) -> None:
        store = write_actuals(
            tmp_path / "s1", "date,revenue_inc_tax\n2023-01-01,1\n", name="a.csv"
        )
        (store / "actuals" / "b.csv").write_text(
            "date,revenue_inc_tax\n2023-01-01,2\n", encoding="utf-8"
        )
        with pytest.raises(ActualsError, match="日付が重複"):
            load_actuals(store)

    def test_negative_revenue(self, tmp_path: Path) -> None:
        store = write_actuals(tmp_path / "s1", "date,revenue_inc_tax\n2023-01-01,-1\n")
        with pytest.raises(ActualsError, match="負の値"):
            load_actuals(store)

    def test_unknown_column(self, tmp_path: Path) -> None:
        store = write_actuals(
            tmp_path / "s1", "date,revenue_inc_tax,note\n2023-01-01,1,x\n"
        )
        with pytest.raises(ActualsError, match="未知の列"):
            load_actuals(store)

    def test_unquoted_thousands_separator(self, tmp_path: Path) -> None:
        """引用なしの桁区切りを 1 円として読まない."""
        store = write_actuals(
            tmp_path / "s1", "date,revenue_inc_tax\n2023-01-01,1,000\n"
        )
        with pytest.raises(ActualsError, match="列数"):
            load_actuals(store)

    def test_missing_dir(self, tmp_path: Path) -> None:
        with pytest.raises(ActualsError, match="ディレクトリがありません"):
            load_actuals(tmp_path / "nope")
