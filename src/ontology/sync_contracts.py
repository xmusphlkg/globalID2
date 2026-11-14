"""Immutable contracts for ontology synchronization and audited remaps."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactRemap:
    old_id: str
    new_id: str
    reason: str
    country_code: str | None = None
    source_id: str | None = None
    mapping_local_name: str | None = None
    evidence_field: str | None = None
    evidence_value: str | None = None
    action: str = "remap_legacy"
    # Compatibility-only diagnostic field.  The migration query never uses
    # this fuzzy pattern; source-scoped remaps use exact structured evidence.
    raw_label_pattern: str | None = None



@dataclass(frozen=True)
class MappingTransition:
    country_code: str
    source_id: str
    local_name: str
    old_id: str
    new_id: str
    action: str
    evidence_field: str | None
    evidence_value: str | None
    reason: str



@dataclass(frozen=True)
class SeriesGeographyRemap:
    series_pattern: str
    old_key: str
    new_key: str
    reason: str
    source_system: str | None = None
    evidence_fields: tuple[str, ...] = ()
    evidence_values: tuple[str, ...] = ()
