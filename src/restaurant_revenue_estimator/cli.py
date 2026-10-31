"""rre コマンド: 入力の検証・評価の実行・レポート出力.

サブコマンドは後続の issue で追加する。ここは骨組みだけ。
"""

from typing import Annotated

import typer

from restaurant_revenue_estimator import __version__

app = typer.Typer(
    help="飲食店 1 店舗の収益を根拠付きシナリオで評価する。",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    """`--version` が指定されたらバージョンを出して終了する."""
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="バージョンを表示して終了する。",
        ),
    ] = False,
) -> None:
    """サブコマンドが増えるまでは `--version` だけを提供する."""
