"""YAML 入力の読み込みと検査."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from restaurant_revenue_estimator.evidence.models import (
    ProvenancedValue,
    Source,
    parse_provenanced,
)
from restaurant_revenue_estimator.evidence.validation import (
    ValidationIssue,
    validate_value,
    walk_values,
)

STORE_FILES = (
    "store.yaml",
    "sources.yaml",
    "layout.yaml",
    "menu.yaml",
    "operations.yaml",
    "observations.yaml",
)


class InputError(Exception):
    """入力の読み込み・検査に失敗した."""


@dataclass(frozen=True)
class ValueEntry:
    """どのファイルのどのキーにある根拠付きの値か."""

    file: str
    path: str
    value: ProvenancedValue[Any]


@dataclass(frozen=True)
class StoreInputs:
    """1 店舗分の入力一式."""

    store_id: str
    directory: Path
    files: dict[str, Any]
    sources: dict[str, Source]
    observation_ids: frozenset[str]
    values: tuple[ValueEntry, ...]

    def get(self, path: str) -> Any:
        """ドット区切りのキーで生の値を引く."""
        node: Any = self.files
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(path)
            node = node[part]
        return node

    def value(self, path: str) -> ProvenancedValue[Any]:
        """ドット区切りのキーで根拠付きの値を引く."""
        found = self.get(path)
        parsed = parse_provenanced(found)
        if parsed is None:
            raise KeyError(path)
        return parsed

    def value_paths(self) -> list[str]:
        """根拠付きの値のパス一覧."""
        return [entry.path for entry in self.values]


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"{path.name}: YAML を読めない: {exc}") from exc


def _load_sources(path: Path) -> dict[str, Source]:
    if not path.exists():
        return {}
    raw = _read_yaml(path) or {}
    sources: dict[str, Source] = {}
    for item in raw.get("sources", []):
        try:
            source = Source.model_validate(item)
        except ValidationError as exc:
            raise InputError(f"{path.name}: 出典の形が不正: {exc}") from exc
        sources[source.id] = source
    return sources


def _load_observation_ids(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    raw = _read_yaml(path) or {}
    ids = {
        item.get("id") for item in raw.get("observations", []) if isinstance(item, dict)
    }
    return frozenset(i for i in ids if isinstance(i, str))


def _load_files(directory: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for name in STORE_FILES:
        path = directory / name
        if path.exists():
            files[name] = _read_yaml(path) or {}
    assumptions = directory / "assumptions"
    if assumptions.exists():
        for path in sorted(assumptions.glob("*.yaml")):
            files[f"assumptions/{path.name}"] = _read_yaml(path) or {}
    # legacy は実績でも入力でもないので、新しい計算の入力に混ぜない（#7）。
    return files


def load_store(directory: Path) -> StoreInputs:
    """店舗ディレクトリを読む. 検査はしない."""
    if not (directory / "store.yaml").exists():
        raise InputError(f"{directory}: store.yaml が無い")
    store = _read_yaml(directory / "store.yaml") or {}
    store_id = store.get("store_id")
    if not isinstance(store_id, str) or not store_id:
        raise InputError(f"{directory}/store.yaml: store_id が無い")
    files = _load_files(directory)
    sources = _load_sources(directory / "sources.yaml")
    observation_ids = _load_observation_ids(directory / "observations.yaml")
    values: list[ValueEntry] = []
    for name, raw in files.items():
        for path, value in walk_values(raw):
            values.append(ValueEntry(file=name, path=path, value=value))
    return StoreInputs(
        store_id=store_id,
        directory=directory,
        files=files,
        sources=sources,
        observation_ids=observation_ids,
        values=tuple(values),
    )


def _excluded_external_ids(store: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for related in store.get("related_stores", []) or []:
        if related.get("included") is False:
            for value in (related.get("external_ids") or {}).values():
                if value:
                    ids.add(str(value))
    return ids


def _related_source_issues(inputs: StoreInputs) -> list[ValidationIssue]:
    store = inputs.files.get("store.yaml", {})
    excluded = _excluded_external_ids(store)
    issues: list[ValidationIssue] = []
    if not excluded:
        return issues
    for entry in inputs.values:
        for source_id in entry.value.sources:
            source = inputs.sources.get(source_id)
            if source is None:
                continue
            if any(external in source.url for external in excluded):
                issues.append(
                    ValidationIssue(
                        file=entry.file,
                        path=entry.path,
                        reason=(
                            f"出典 {source_id} は姉妹店（included: false）のページで、"
                            "この店舗の値の根拠に使えない"
                        ),
                    )
                )
    return issues


def _validate_late_night_premium(entry: ValueEntry) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    reason_msg = "late_night_premium は 1.25 以上（深夜割増 25% 以上）"
    if entry.value.value is not None:
        try:
            if Decimal(str(entry.value.value)) < Decimal("1.25"):
                issues.append(
                    ValidationIssue(file=entry.file, path=entry.path, reason=reason_msg)
                )
        except (ValueError, ArithmeticError):
            issues.append(
                ValidationIssue(
                    file=entry.file,
                    path=entry.path,
                    reason="late_night_premium の値が数値ではない",
                )
            )
    if isinstance(entry.value.range, dict):
        for rk, rval in entry.value.range.items():
            if rval is not None:
                try:
                    if Decimal(str(rval)) < Decimal("1.25"):
                        issues.append(
                            ValidationIssue(
                                file=entry.file,
                                path=f"{entry.path}.range.{rk}",
                                reason=reason_msg,
                            )
                        )
                except (ValueError, ArithmeticError):
                    pass
    return issues


VALID_WEEKDAYS: frozenset[str] = frozenset(
    {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
)
REQUIRED_SHIFT_FIELDS: tuple[str, ...] = (
    "id",
    "weekdays",
    "service",
    "start",
    "end",
    "heads",
    "hourly_wage",
)
TIME_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def _check_time_and_heads(shift_id: str, shift: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for tf in ("start", "end"):
        if tf in shift:
            tval = str(shift[tf])
            if not TIME_PATTERN.match(tval):
                errors.append(
                    f"シフト '{shift_id}': {tf} は HH:MM 形式（00:00〜23:59）"
                    f"である必要があります（指定値: '{tval}'）"
                )
    if "heads" in shift:
        heads = shift["heads"]
        if isinstance(heads, bool) or not isinstance(heads, int) or heads < 0:
            errors.append(
                f"シフト '{shift_id}': heads は 0 以上の整数である必要があります"
                f"（指定値: '{heads}'）"
            )
    return errors


def _check_wage_and_service(shift_id: str, shift: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "service" in shift:
        svc = shift["service"]
        if svc not in ("lunch", "dinner"):
            errors.append(
                f"シフト '{shift_id}': service は 'lunch' または 'dinner' のみ"
                f"（指定値: '{svc}'）"
            )
    if "hourly_wage" in shift:
        wage_raw = shift["hourly_wage"]
        try:
            wage = Decimal(str(wage_raw))
            if wage.is_nan() or wage.is_infinite() or wage <= 0:
                raise ValueError
        except (ValueError, ArithmeticError, TypeError):
            errors.append(
                f"シフト '{shift_id}': hourly_wage は 0 より大きい有限の数値"
                f"である必要があります（指定値: '{wage_raw}'）"
            )
    return errors


def _validate_single_shift(idx: int, shift: Any, seen_ids: set[str]) -> list[str]:
    if not isinstance(shift, dict):
        return [f"シフト要素 [{idx}] は辞書である必要があります"]

    errors: list[str] = []
    shift_id = str(shift.get("id") or f"[{idx}]")

    for req_field in REQUIRED_SHIFT_FIELDS:
        if req_field not in shift:
            errors.append(
                f"シフト '{shift_id}': 必須フィールド '{req_field}' がありません"
            )

    if "id" in shift:
        sid = str(shift["id"])
        if sid in seen_ids:
            errors.append(f"シフト id '{sid}' が重複しています")
        else:
            seen_ids.add(sid)

    if "weekdays" in shift:
        wds = shift["weekdays"]
        if not isinstance(wds, (list, tuple, set)) or not wds:
            errors.append(
                f"シフト '{shift_id}': weekdays は曜日のリストである必要があります"
            )
        else:
            for wd in wds:
                if wd not in VALID_WEEKDAYS:
                    errors.append(
                        f"シフト '{shift_id}': 不正な曜日 '{wd}' "
                        f"(mon, tue, wed, thu, fri, sat, sun のいずれか)"
                    )

    errors.extend(_check_time_and_heads(shift_id, shift))
    errors.extend(_check_wage_and_service(shift_id, shift))
    return errors


def validate_shift_specs(shifts: Any) -> list[str]:
    """シフト仕様のリストを検査し、問題の文言のリストを返す純粋関数."""
    if not isinstance(shifts, list):
        return ["part_time_shifts はリストである必要があります"]

    errors: list[str] = []
    seen_ids: set[str] = set()
    for idx, shift in enumerate(shifts):
        errors.extend(_validate_single_shift(idx, shift, seen_ids))
    return errors


def _validate_part_time_shifts(entry: ValueEntry) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    shift_groups: list[tuple[str, Any]] = [("", entry.value.value)]
    if isinstance(entry.value.range, dict):
        for rk, rshifts in entry.value.range.items():
            shift_groups.append((f".range.{rk}", rshifts))
    for sub_path, shifts in shift_groups:
        for err in validate_shift_specs(shifts):
            issues.append(
                ValidationIssue(
                    file=entry.file,
                    path=f"{entry.path}{sub_path}",
                    reason=err,
                )
            )
    return issues


def _is_valid_stay_ratio_map(val: Any) -> bool:
    if not isinstance(val, dict) or not val:
        return False
    for k, v in val.items():
        try:
            int(k)
            if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
                return False
            fv = float(v)
            if not math.isfinite(fv) or fv <= 0:
                return False
        except (ValueError, TypeError, OverflowError):
            return False
    return True


def _validate_lunch_stay_ratio_by_group_size(
    entry: ValueEntry,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    reason_msg = (
        f"{entry.path} はキーが整数に直せて、値が正の有限数の対応表である必要があります"
    )
    if entry.value.value is not None and not _is_valid_stay_ratio_map(
        entry.value.value
    ):
        issues.append(
            ValidationIssue(file=entry.file, path=entry.path, reason=reason_msg)
        )
    if entry.value.range is not None:
        for rk in ("low", "base", "high"):
            rval = getattr(entry.value.range, rk, None)
            if rval is not None and not _is_valid_stay_ratio_map(rval):
                issues.append(
                    ValidationIssue(
                        file=entry.file,
                        path=f"{entry.path}.range.{rk}",
                        reason=reason_msg,
                    )
                )
    return issues


VALID_WEEKDAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})


def _is_valid_weekday_factor_map(val: Any) -> bool:
    if not isinstance(val, dict) or not val:
        return False
    for k, v in val.items():
        if not isinstance(k, str) or k not in VALID_WEEKDAYS:
            return False
        if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
            return False
        try:
            fv = float(v)
            if not math.isfinite(fv) or fv <= 0:
                return False
        except (ValueError, TypeError, OverflowError):
            return False
    return True


def _validate_lunch_arrival_weekday_factor(
    entry: ValueEntry,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    reason_msg = (
        f"{entry.path} はキーが mon〜sun で、値が正の有限数の対応表である必要があります"
    )
    if entry.value.value is not None and not _is_valid_weekday_factor_map(
        entry.value.value
    ):
        issues.append(
            ValidationIssue(file=entry.file, path=entry.path, reason=reason_msg)
        )
    if entry.value.range is not None:
        for rk in ("low", "base", "high"):
            rval = (
                entry.value.range.get(rk)
                if isinstance(entry.value.range, dict)
                else getattr(entry.value.range, rk, None)
            )
            if not _is_valid_weekday_factor_map(rval):
                issues.append(
                    ValidationIssue(
                        file=entry.file,
                        path=f"{entry.path}.range.{rk}",
                        reason=reason_msg,
                    )
                )
    return issues


def _validate_labor_shifts_and_premium(
    inputs: StoreInputs,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for entry in inputs.values:
        if entry.path == "late_night_premium":
            issues.extend(_validate_late_night_premium(entry))
        elif entry.path == "part_time_shifts":
            issues.extend(_validate_part_time_shifts(entry))
        elif entry.path == "lunch_stay_ratio_by_group_size":
            issues.extend(_validate_lunch_stay_ratio_by_group_size(entry))
        elif entry.path == "lunch_arrival_weekday_factor":
            issues.extend(_validate_lunch_arrival_weekday_factor(entry))
    return issues


def validate_store(directory: Path) -> list[ValidationIssue]:
    """1 店舗分を検査する."""
    try:
        inputs = load_store(directory)
    except InputError as exc:
        return [ValidationIssue(file=directory.name, path="-", reason=str(exc))]

    issues: list[ValidationIssue] = []

    raw_sources = (inputs.files.get("sources.yaml") or {}).get("sources", []) or []
    for item in raw_sources:
        if isinstance(item, dict) and item.get("body_stored"):
            issues.append(
                ValidationIssue(
                    file="sources.yaml",
                    path=f"sources.{item.get('id')}.body_stored",
                    reason="body_stored は常に false（ページ本文は保存しない）",
                )
            )

    for entry in inputs.values:
        issues.extend(
            validate_value(
                entry.value,
                file=entry.file,
                path=entry.path,
                sources=inputs.sources,
                observation_ids=set(inputs.observation_ids),
            )
        )

    issues.extend(_related_source_issues(inputs))
    issues.extend(_validate_labor_shifts_and_premium(inputs))
    return issues


def load_all_stores(data_dir: Path) -> list[StoreInputs]:
    """data/stores 配下の店舗を全部読む."""
    stores: list[StoreInputs] = []
    root = data_dir / "stores"
    if not root.exists():
        return stores
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        stores.append(load_store(directory))
    return stores


def _plain(value: Any) -> Any:
    """ProvenancedValue の dict なら value を返す."""
    if isinstance(value, dict) and "value" in value and "status" in value:
        return value["value"]
    return value


def validate_identity(stores: list[StoreInputs]) -> list[ValidationIssue]:
    """外部 ID・電話番号・住所の一致で別店舗を検出する (spec 2.3)."""
    issues: list[ValidationIssue] = []
    seen: dict[str, tuple[str, str]] = {}

    def check(kind: str, key: str, store_id: str) -> None:
        normalized = key.strip().replace("-", "").replace(" ", "")
        if not normalized:
            return
        if normalized in seen:
            other, other_kind = seen[normalized]
            if other != store_id:
                issues.append(
                    ValidationIssue(
                        file="store.yaml",
                        path=store_id,
                        reason=f"{kind} が {other} と一致（{other_kind}）: {store_id}",
                    )
                )
            return
        seen[normalized] = (store_id, kind)

    for inputs in stores:
        store = inputs.files.get("store.yaml", {}) or {}
        for name, value in (store.get("external_ids") or {}).items():
            plain = _plain(value)
            if plain:
                check(str(name), str(plain), inputs.store_id)
        phone = _plain(store.get("phone"))
        if isinstance(phone, str):
            check("phone", phone, inputs.store_id)
        address = _plain(store.get("address"))
        if isinstance(address, str):
            check("address", address, inputs.store_id)
    return issues


def validate_all(data_dir: Path) -> list[ValidationIssue]:
    """全店舗を検査する."""
    issues: list[ValidationIssue] = []
    stores: list[StoreInputs] = []
    root = data_dir / "stores"
    if not root.exists():
        return issues
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        issues.extend(validate_store(directory))
        try:
            stores.append(load_store(directory))
        except InputError:
            continue
    issues.extend(validate_identity(stores))
    return issues


def _normalize(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            str(key): _normalize(value)
            for key, value in sorted(data.items(), key=lambda i: str(i[0]))
        }
    if isinstance(data, list):
        return [_normalize(item) for item in data]
    return data


def input_hash(inputs: StoreInputs, overrides: dict[str, Any] | None = None) -> str:
    """キー順に依存しない入力ハッシュ."""
    payload = {
        "files": _normalize(inputs.files),
        "overrides": _normalize(overrides or {}),
    }
    text = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
