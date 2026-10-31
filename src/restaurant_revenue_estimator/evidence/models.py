"""根拠付きの値 (ProvenancedValue) と出典 (Source) の型."""

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceStatus(StrEnum):
    """値の根拠の強さ. 入力に書けるのは DERIVED 以外."""

    OBSERVED = "OBSERVED"
    PUBLIC_VERIFIED = "PUBLIC_VERIFIED"
    PUBLIC_REPORTED = "PUBLIC_REPORTED"
    ASSUMPTION = "ASSUMPTION"
    DERIVED = "DERIVED"
    UNRESOLVED = "UNRESOLVED"


class LicenseStatus(StrEnum):
    """出典ページの利用条件."""

    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    RESTRICTED = "restricted"


class Range(BaseModel):
    """ASSUMPTION の low / base / high. 分布や表も入るので Any."""

    low: Any
    base: Any
    high: Any


class Source(BaseModel):
    """出典台帳の 1 件. ページ本文は保存しない."""

    id: str
    url: str
    accessed_at: date | None = None
    published_at: date | None = None
    facts: list[str] = Field(default_factory=list)
    license_status: LicenseStatus = LicenseStatus.UNKNOWN
    body_stored: bool = False


class ProvenancedValue[T](BaseModel):
    """すべての入力値の形 (spec 0.1)."""

    value: T | None = None
    unit: str = ""
    status: SourceStatus
    sources: list[str] = Field(default_factory=list)
    observation_id: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    justification: str | None = None
    range: Range | None = None
    candidates: dict[str, Any] = Field(default_factory=dict)
    how_to_verify: str | None = None
    note: str | None = None


def parse_provenanced(data: Any) -> ProvenancedValue[Any] | None:
    """dict が ProvenancedValue の形なら変換する. 違えば None."""
    if not isinstance(data, dict):
        return None
    if "status" not in data or "unit" not in data:
        return None
    return ProvenancedValue[Any].model_validate(data)
