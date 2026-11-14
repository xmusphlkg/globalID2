"""Stable result and reviewed-identity types for Iceland history imports."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class IcelandWorkbookLayoutError(ValueError):
    """Raised when a workbook no longer matches a reviewed layout."""


@dataclass(frozen=True)
class ReviewedSeries:
    """One manually reviewed source identity and its canonical target."""

    key: str
    token: str
    concept_id: str
    label_is: str
    label_en: str
    aliases: tuple[str, ...] = ()

    def source_series_code(self, source_kind: str) -> str:
        if source_kind == "registry_annual":
            return f"SER_IS_HISTORY_{self.token}_ANNUAL"
        if source_kind == "registry_disease_monthly":
            return f"SER_IS_HISTORY_{self.token}_MONTHLY"
        raise ValueError(f"Unsupported history source kind: {source_kind}")

    def local_code(self, source_kind: str) -> str:
        if source_kind == "registry_annual":
            return f"IS_HISTORY_ANNUAL_{self.token}"
        if source_kind == "registry_disease_monthly":
            return f"IS_HISTORY_MONTHLY_{self.token}"
        raise ValueError(f"Unsupported history source kind: {source_kind}")


@dataclass(frozen=True)
class ReviewedLegacySeries:
    """A legacy diagnosis series identified by ICD-10 text and local label."""

    token: str
    concept_id: str
    icd10: str
    label_is: str
    label_en: str

    @property
    def source_series_code(self) -> str:
        return f"SER_IS_LEGACY_ICD_{self.token}_MONTHLY"

    @property
    def local_code(self) -> str:
        return f"IS_LEGACY_ICD_{self.token}"


@dataclass
class IcelandHistoryPreparedResult:
    """Normalized projections, lossless series rows, and audit artifacts."""

    rows: list[dict[str, Any]]
    series_rows: list[dict[str, Any]]
    quarantine: list[dict[str, Any]]
    manifest: dict[str, Any]
    raw_hashes: dict[str, str]


@dataclass(frozen=True)
class IcelandHistoryImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    source_latest_date: date | None
    skipped_current_precedence: int = 0


@dataclass
class _ParseContext:
    stats: Counter[str] = field(default_factory=Counter)
    quarantine: list[dict[str, Any]] = field(default_factory=list)

    def reject(self, reason: str, **details: Any) -> None:
        self.stats[f"quarantine.{reason}"] += 1
        self.quarantine.append({"reason": reason, **details})
