"""入力に書かれた根拠付きの値を検査する.

検査はここ 1 か所に集める。ステータスは enum で比較する。
"""

from dataclasses import dataclass

from restaurant_revenue_estimator.evidence.models import (
    ProvenancedValue,
    Source,
    SourceStatus,
    parse_provenanced,
)


@dataclass(frozen=True)
class ValidationIssue:
    """1 件のエラー. 出力は「ファイル:キーのパス: 理由」の形にする."""

    file: str
    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.file}:{self.path}: {self.reason}"


def validate_value(  # noqa: PLR0912
    value: ProvenancedValue[object],
    *,
    file: str,
    path: str,
    sources: dict[str, Source],
    observation_ids: set[str],
) -> list[ValidationIssue]:
    """ARCHITECTURE 4.1 節の表 1 を検査する."""
    issues: list[ValidationIssue] = []

    def add(reason: str) -> None:
        issues.append(ValidationIssue(file=file, path=path, reason=reason))

    if value.status is SourceStatus.DERIVED:
        add("status DERIVED は入力に書けない（計算結果にだけ付く）")

    for source_id in value.sources:
        source = sources.get(source_id)
        if source is None:
            add(f"出典 {source_id} が sources.yaml に無い")
            continue
        if source.body_stored:
            add(f"出典 {source_id} の body_stored が true（ページ本文は保存しない）")

    if value.status in (
        SourceStatus.PUBLIC_VERIFIED,
        SourceStatus.PUBLIC_REPORTED,
    ):
        if not value.sources:
            add("PUBLIC_VERIFIED/PUBLIC_REPORTED は sources が 1 件以上必要")
        for source_id in value.sources:
            source = sources.get(source_id)
            if source is not None and source.accessed_at is None:
                add(f"出典 {source_id} に accessed_at が無い")

    if value.status is SourceStatus.OBSERVED:
        if not value.observation_id:
            add("OBSERVED は observation_id が必要")
        elif value.observation_id not in observation_ids:
            add(f"observation_id {value.observation_id} が observations.yaml に無い")

    if value.status is SourceStatus.ASSUMPTION:
        if not value.justification:
            add("ASSUMPTION は justification が必要")
        if value.range is None:
            add("ASSUMPTION は range (low/base/high) が必要")

    if value.status is SourceStatus.UNRESOLVED:
        if value.value is not None:
            add("UNRESOLVED は value を null にする")
        if not value.candidates:
            add("UNRESOLVED は candidates が 1 件以上必要")
    elif value.value is None:
        add(f"status {value.status} なのに value が null（null を既定値で埋めない）")

    return issues


def walk_values(
    data: object, path: str = ""
) -> list[tuple[str, ProvenancedValue[object]]]:
    """dict / list をたどって ProvenancedValue を全部集める."""
    found: list[tuple[str, ProvenancedValue[object]]] = []
    if isinstance(data, dict):
        parsed = parse_provenanced(data)
        if parsed is not None:
            return [(path, parsed)]
        for key, child in data.items():
            child_path = f"{path}.{key}" if path else str(key)
            found.extend(walk_values(child, child_path))
    elif isinstance(data, list):
        for index, child in enumerate(data):
            found.extend(walk_values(child, f"{path}[{index}]"))
    return found
