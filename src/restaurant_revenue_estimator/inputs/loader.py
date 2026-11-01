"""YAML 入力の読み込みと検査."""

import hashlib
import json
from dataclasses import dataclass
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
