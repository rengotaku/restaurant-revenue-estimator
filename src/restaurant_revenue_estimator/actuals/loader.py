"""`data/stores/<store_id>/actuals/` の実績を読む.

構成:

    actuals/
      actuals.meta.yaml   出所・ライセンス・欠測の説明（必須）
      *.csv               date,revenue_inc_tax[,orders][,guests]

CSV は複数ファイルを連結してよい（期間ごとに分ける等）。日付が重複したら
どちらが正しいか決められないのでエラーにする。
"""

import csv
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from restaurant_revenue_estimator.actuals.models import (
    ActualDay,
    ActualSeries,
    ActualsError,
)

ACTUALS_DIR = "actuals"
META_FILE = "actuals.meta.yaml"
REQUIRED_COLUMNS = ("date", "revenue_inc_tax")
OPTIONAL_COLUMNS = ("orders", "guests")
META_KEYS = ("source_id", "license", "coverage_note")


def load_actuals(store_dir: Path) -> ActualSeries:
    """店舗ディレクトリの実績を読む. 1 件でも不備があれば ActualsError."""
    actuals_dir = store_dir / ACTUALS_DIR
    if not actuals_dir.is_dir():
        raise ActualsError(f"実績ディレクトリがありません: {actuals_dir}")

    meta = _load_meta(actuals_dir / META_FILE)
    csv_paths = sorted(actuals_dir.glob("*.csv"))
    if not csv_paths:
        raise ActualsError(f"実績 CSV がありません: {actuals_dir}")

    days: list[ActualDay] = []
    seen: dict[date, str] = {}
    for path in csv_paths:
        for day, line_no in _read_csv(path):
            if day.day in seen:
                raise ActualsError(
                    f"日付が重複しています: {day.day} "
                    f"({seen[day.day]} と {path.name}:{line_no})。"
                    "同じ日の実績を 2 つ持つと、どちらが正しいか決められません。"
                )
            seen[day.day] = f"{path.name}:{line_no}"
            days.append(day)

    days.sort(key=lambda day: day.day)
    if not days:
        raise ActualsError(f"実績の行がありません: {actuals_dir}")

    return ActualSeries(
        store_id=store_dir.name,
        days=tuple(days),
        source_id=str(meta["source_id"]),
        license=str(meta["license"]),
        coverage_note=str(meta["coverage_note"]),
        url=str(meta["url"]) if meta.get("url") else None,
        accessed_at=str(meta["accessed_at"]) if meta.get("accessed_at") else None,
    )


def _load_meta(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ActualsError(
            f"実績のメタ情報がありません: {path}。"
            f"{', '.join(META_KEYS)} を書いてください。"
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ActualsError(f"メタ情報の YAML を読めません: {path} ({exc})") from exc
    if not isinstance(raw, dict):
        raise ActualsError(f"メタ情報が mapping ではありません: {path}")

    missing = [key for key in META_KEYS if not str(raw.get(key) or "").strip()]
    if missing:
        raise ActualsError(f"メタ情報の必須キーが空です: {', '.join(missing)} ({path})")
    return raw


def _read_csv(path: Path) -> list[tuple[ActualDay, int]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            raise ActualsError(
                f"実績 CSV に必須列がありません: {', '.join(missing)} ({path})。"
                f"列は {', '.join(REQUIRED_COLUMNS)} です。"
            )
        unknown = [
            column
            for column in header
            if column not in REQUIRED_COLUMNS and column not in OPTIONAL_COLUMNS
        ]
        if unknown:
            raise ActualsError(
                f"実績 CSV に未知の列があります: {', '.join(unknown)} ({path})。"
                "列を増やすと集計の意味が変わるため、意図した列かを確認してください。"
            )
        parsed: list[tuple[ActualDay, int]] = []
        for line_no, row in enumerate(reader, 2):
            if None in row:
                raise ActualsError(
                    f"列数がヘッダーより多い行があります（{path}:{line_no}）。"
                    '金額に桁区切りを入れるなら "1,000" と引用してください。'
                )
            parsed.append(_parse_row(row, path, line_no))
        return parsed


def _parse_row(
    row: dict[str, str | None], path: Path, line_no: int
) -> tuple[ActualDay, int]:
    where = f"{path.name}:{line_no}"
    raw_date = (row.get("date") or "").strip()
    try:
        day = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ActualsError(
            f"日付は YYYY-MM-DD で書いてください: {raw_date!r} ({where})"
        ) from exc

    revenue = _parse_int(row.get("revenue_inc_tax"), where, "revenue_inc_tax")
    if revenue < 0:
        raise ActualsError(f"売上が負の値です: {revenue} ({where})")

    orders = _parse_optional_int(row.get("orders"), where, "orders")
    guests = _parse_optional_int(row.get("guests"), where, "guests")

    return ActualDay(
        day=day, revenue_inc_tax=revenue, orders=orders, guests=guests
    ), line_no


def _parse_int(raw: str | None, where: str, name: str) -> int:
    text = (raw or "").strip().replace(",", "")
    if text == "":
        raise ActualsError(f"{name} が空です ({where})")
    try:
        return int(text)
    except ValueError as exc:
        raise ActualsError(f"{name} は整数で書いてください: {raw!r} ({where})") from exc


def _parse_optional_int(raw: str | None, where: str, name: str) -> int | None:
    text = (raw or "").strip()
    if text == "":
        return None
    return _parse_int(text, where, name)
