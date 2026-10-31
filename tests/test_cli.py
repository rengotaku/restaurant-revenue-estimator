"""CLI (rre) のテスト."""

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
