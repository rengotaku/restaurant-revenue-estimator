"""CLI (rre) のテスト."""

import json
from pathlib import Path

from typer.testing import CliRunner

from restaurant_revenue_estimator import __version__
from restaurant_revenue_estimator.cli import app

runner = CliRunner()


class TestVersionOption:
    """`rre --version` のテスト."""

    def test_version_exits_zero(self) -> None:
        """終了コード 0 で終わる."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0

    def test_version_prints_version_string(self) -> None:
        """バージョン文字列を出す."""
        result = runner.invoke(app, ["--version"])
        assert __version__ in result.stdout

    def test_no_args_shows_help(self) -> None:
        """引数なしではヘルプを表示する (Typer は usage エラー扱いで終了する)."""
        result = runner.invoke(app, [])
        assert result.exit_code in (0, 2)
        assert "Usage" in result.stdout


class TestBacktestCommand:
    """`rre backtest` のテスト."""

    @staticmethod
    def make_inputs(tmp_path: Path) -> tuple[Path, Path]:
        store = tmp_path / "s1"
        actuals = store / "actuals"
        actuals.mkdir(parents=True)
        (actuals / "actuals.meta.yaml").write_text(
            "source_id: S99\nlicense: CC BY 4.0\ncoverage_note: テスト用\n",
            encoding="utf-8",
        )
        (actuals / "2023-01.csv").write_text(
            "date,revenue_inc_tax\n2023-01-01,1000\n2023-01-02,2000\n",
            encoding="utf-8",
        )
        run_json = tmp_path / "result.json"
        run_json.write_text(
            json.dumps(
                {
                    "days": [
                        {"date": "2023-01-01", "total": 1100},
                        {"date": "2023-01-02", "total": 1900},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return store, run_json

    def test_prints_metrics(self, tmp_path: Path) -> None:
        """Markdown で MAE を出す."""
        store, run_json = self.make_inputs(tmp_path)
        result = runner.invoke(app, ["backtest", str(store), "--run", str(run_json)])
        assert result.exit_code == 0
        assert "MAE" in result.stdout
        assert "100" in result.stdout

    def test_json_output(self, tmp_path: Path) -> None:
        """--json は機械可読."""
        store, run_json = self.make_inputs(tmp_path)
        result = runner.invoke(
            app, ["backtest", str(store), "--run", str(run_json), "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["compared_days"] == 2
        assert payload["mae"] == 100

    def test_missing_actuals_exits_one(self, tmp_path: Path) -> None:
        """実績が無ければ終了コード 1 で理由を出す."""
        store, run_json = self.make_inputs(tmp_path)
        (store / "actuals").rename(tmp_path / "moved")
        result = runner.invoke(app, ["backtest", str(store), "--run", str(run_json)])
        assert result.exit_code == 1
        assert "実績ディレクトリがありません" in result.stderr

    def test_writes_markdown(self, tmp_path: Path) -> None:
        """--out に Markdown を書く."""
        store, run_json = self.make_inputs(tmp_path)
        out = tmp_path / "backtest.md"
        result = runner.invoke(
            app, ["backtest", str(store), "--run", str(run_json), "--out", str(out)]
        )
        assert result.exit_code == 0
        assert "実績との比較" in out.read_text(encoding="utf-8")
