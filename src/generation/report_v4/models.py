"""Typed structures for the report v4 contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "report_v4.0"
METHOD_VERSION = "report_v4.0"
SUPPORTED_LOCALES: tuple[str, str] = ("zh", "en")
DEFAULT_LOCALE = "zh"

Locale = Literal["zh", "en"]
DeathReportingStatus = Literal[
    "reported_zero",
    "reported_positive",
    "not_reported",
    "partial",
    "unknown",
]


@dataclass(frozen=True)
class LocalizedText:
    zh: str
    en: str

    def to_dict(self) -> dict[str, str]:
        return {"zh": self.zh, "en": self.en}


@dataclass
class DeathReporting:
    status: DeathReportingStatus
    total_deaths: int | None
    observed_periods: int
    missing_periods: int
    reported_zero_periods: int
    source_policy: dict[str, Any] = field(default_factory=dict)
    display_note: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Section:
    id: str
    type: str
    order: int
    title: LocalizedText
    body: LocalizedText
    evidence_refs: list[str] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "order": self.order,
            "title": self.title.to_dict(),
            "body": self.body.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "figures": list(self.figures),
            "quality_flags": list(self.quality_flags),
        }


@dataclass
class DiseaseDirectoryItem:
    disease_id: str
    slug: str
    name_zh: str
    name_en: str
    category: str | None
    latest_cases: int
    previous_cases: int | None
    total_cases: int
    mom_change_pct: float | None
    yoy_change_pct: float | None
    recent_change_pct: float | None
    long_window_change_pct: float | None
    trend: dict[str, Any]
    risk_score: float | int | None
    risk_level: str | None
    current_movement: dict[str, Any] = field(default_factory=dict)
    seasonal_position: dict[str, Any] = field(default_factory=dict)
    trend_basis: dict[str, Any] = field(default_factory=dict)
    analysis_sections: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportDocument:
    title: LocalizedText
    summary: LocalizedText
    key_findings: dict[str, list[str]]
    sections: list[Section]
    metrics: dict[str, Any]
    death_reporting: DeathReporting
    data_quality: dict[str, Any]
    disease_directory: list[dict[str, Any] | DiseaseDirectoryItem] = field(default_factory=list)
    risk_ranking: list[dict[str, Any]] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    evidence_index: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    default_locale: str = DEFAULT_LOCALE
    locales: tuple[str, str] = SUPPORTED_LOCALES

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "default_locale": self.default_locale,
            "locales": list(self.locales),
            "title": self.title.to_dict(),
            "summary": self.summary.to_dict(),
            "key_findings": self.key_findings,
            "sections": [section.to_dict() for section in self.sections],
            "metrics": self.metrics,
            "death_reporting": self.death_reporting.to_dict(),
            "data_quality": self.data_quality,
            "disease_directory": [
                item.to_dict() if hasattr(item, "to_dict") else dict(item)
                for item in self.disease_directory
            ],
            "risk_ranking": self.risk_ranking,
            "figures": self.figures,
            "references": self.references,
            "evidence_index": self.evidence_index,
        }
