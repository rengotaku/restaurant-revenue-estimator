"""店舗の一覧・サマリー・根拠一覧."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from restaurant_revenue_estimator.evidence.models import ProvenancedValue
from restaurant_revenue_estimator.inputs import StoreInputs, load_store
from restaurant_revenue_estimator.services.evaluation import plain


@dataclass(frozen=True)
class EvidenceRow:
    path: str
    status: str
    value: str
    sources: str
    accessed: str
    note: str


@dataclass(frozen=True)
class StoreSummary:
    store_id: str
    name: str
    address: str
    phone: str
    related_names: list[str]
    source_from: str
    source_to: str
    status_counts: dict[str, int]
    total_keys: int
    rules: list[dict[str, str]]


def list_stores(data_dir: Path) -> list[dict[str, str]]:
    root = data_dir / "stores"
    stores: list[dict[str, str]] = []
    if not root.exists():
        return stores
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            inputs = load_store(directory)
        except Exception:
            continue
        store = inputs.files.get("store.yaml", {}) or {}
        stores.append(
            {
                "store_id": inputs.store_id,
                "name": str(plain(store.get("name")) or inputs.store_id),
                "address": str(plain(store.get("address")) or ""),
            }
        )
    return stores


def _display(value: ProvenancedValue[Any]) -> str:
    if value.value is None:
        return f"null（候補: {', '.join(value.candidates)}）"
    return str(value.value)


def evidence_rows(inputs: StoreInputs) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    for entry in inputs.values:
        value = entry.value
        sources = []
        accessed = []
        for source_id in value.sources:
            source = inputs.sources.get(source_id)
            if source is None:
                sources.append(source_id)
                continue
            sources.append(f"{source.id}: {source.url}")
            if source.accessed_at:
                accessed.append(source.accessed_at.isoformat())
        rows.append(
            EvidenceRow(
                path=entry.path,
                status=value.status.value,
                value=_display(value),
                sources=" / ".join(sources) or "—",
                accessed=", ".join(sorted(set(accessed))) or "—",
                note=value.note or "",
            )
        )
    return rows


def summarize(inputs: StoreInputs) -> StoreSummary:
    store = inputs.files.get("store.yaml", {}) or {}
    dates = sorted(
        source.accessed_at for source in inputs.sources.values() if source.accessed_at
    )
    counts: dict[str, int] = {}
    for entry in inputs.values:
        counts[entry.value.status.value] = counts.get(entry.value.status.value, 0) + 1
    rules_value = (inputs.files.get("operations.yaml", {}) or {}).get("rules", {})
    rules: list[dict[str, str]] = []
    for rule in rules_value.get("value", []) if isinstance(rules_value, dict) else []:
        rules.append(
            {
                "id": str(rule.get("id")),
                "from": str(rule.get("effective_from") or "—"),
                "to": str(rule.get("effective_to") or "—"),
            }
        )
    related = [
        str(item.get("name"))
        for item in store.get("related_stores", []) or []
        if item.get("included") is False
    ]
    return StoreSummary(
        store_id=inputs.store_id,
        name=str(plain(store.get("name")) or inputs.store_id),
        address=str(plain(store.get("address")) or ""),
        phone=str(plain(store.get("phone")) or ""),
        related_names=related,
        source_from=dates[0].isoformat() if dates else "—",
        source_to=dates[-1].isoformat() if dates else "—",
        status_counts=counts,
        total_keys=len(inputs.values),
        rules=rules,
    )
