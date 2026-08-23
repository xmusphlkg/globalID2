"""Strict public and internal contracts for Situation Room v3.

These Pydantic models are the source of truth for JSON Schema, API responses,
static exports, and generated TypeScript types.  Public reports deliberately
separate statistical anomaly evidence from attributable public-health risk.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalizedText(ContractModel):
    en: str
    zh: str


class ReportMetadata(ContractModel):
    report_id: str
    kind: Literal["daily", "weekly", "monthly"]
    period_key: str
    period_start: date
    period_end: date
    as_of: datetime
    revision: int = Field(ge=1)
    status: Literal["draft", "gate_failed", "published", "suppressed"]
    supersedes_report_id: str | None = None


class MethodMetadata(ContractModel):
    version: str
    model: Literal[
        "robust_quasi_poisson_v1",
        "multi_horizon_gamma_poisson_v1",
    ]
    config_hash: str
    code_version: str | None = None
    fdr_method: Literal["benjamini_hochberg"] = "benjamini_hochberg"
    fdr_family: Literal[
        "metric_type_cadence",
        "detector_tier_metric_type_cadence",
    ] = "detector_tier_metric_type_cadence"
    alert_q: float = Field(default=0.05, gt=0, lt=1)
    strong_q: float = Field(default=0.01, gt=0, lt=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class CurrencySlice(ContractModel):
    source_system: str
    cadence: str | None = None
    earliest_data_through: date | None = None
    latest_data_through: date | None = None
    comparable_through: date | None = None
    latest_available_through: date | None = None
    analyzed_series_count: int = Field(default=0, ge=0)
    delayed_series_count: int = Field(default=0, ge=0)
    held_back_series_count: int = Field(default=0, ge=0)
    readiness_ratio: float | None = Field(default=None, ge=0, le=1)
    status: Literal["fresh", "partial", "stale", "failed", "not_checked"]


class DataCurrency(ContractModel):
    earliest_data_through: date | None = None
    latest_data_through: date | None = None
    by_source: list[CurrencySlice] = Field(default_factory=list)


class Coverage(ContractModel):
    registered_series_count: int = Field(ge=0)
    evaluated_series_count: int = Field(ge=0)
    modeled_series_count: int = Field(ge=0)
    rejected_series_count: int = Field(ge=0)
    published_signal_count: int = Field(ge=0)
    jurisdiction_count: int = Field(ge=0)
    disease_count: int = Field(ge=0)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    note: LocalizedText


class ReportSummary(ContractModel):
    unique_signal_count: int = Field(ge=0)
    alert_count: int = Field(ge=0)
    strong_count: int = Field(ge=0)
    official_event_count: int = Field(ge=0)
    new_count: int = Field(default=0, ge=0)
    persistent_count: int = Field(default=0, ge=0)
    resolved_count: int = Field(default=0, ge=0)
    active_at_period_end_count: int = Field(default=0, ge=0)


class SignalIdentity(ContractModel):
    signal_id: str
    disease_id: str
    disease_name: str
    disease_slug: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    canonical_geography_key: str
    source_geography_keys: list[str]
    dimension_key: str
    dimensions: dict[str, Any] = Field(default_factory=dict)
    series_code: str
    source_system: str
    source_label: str | None = None
    metric_type: str
    metric_label: str
    unit: str
    cadence: Literal["daily", "weekly", "monthly"]


class ObservationComparison(ContractModel):
    window_label: str
    window_periods: int = Field(ge=1)
    data_through: date
    latest_available_period: date | None = None
    data_status: Literal["current", "held_back", "delayed"] = "current"
    reporting_lag_days: int = Field(default=0, ge=0)
    analysis_lag_days: int = Field(default=0, ge=0)
    current: float
    previous: float | None = None
    expected: float | None = None
    predictive_upper_95: float | None = None
    absolute_change: float | None = None
    relative_change_pct: float | None = None
    completeness: float = Field(ge=0, le=1)


class AnomalyAssessment(ContractModel):
    model: str
    detector_tier: Literal["common_count", "rare_count", "rate", "context_only"] = (
        "common_count"
    )
    state: Literal["routine", "watch", "alert", "strong", "not_modeled"]
    raw_p_value: float | None = Field(default=None, ge=0, le=1)
    q_value: float | None = Field(default=None, ge=0, le=1)
    standardized_exceedance: float | None = None
    dispersion: float | None = Field(default=None, ge=0)
    fit_status: str
    effect_threshold_passed: bool
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class PublicHealthRisk(ContractModel):
    status: Literal["not_assessed", "assessed"] = "not_assessed"
    level: Literal["low", "moderate", "high", "very_high"] | None = None
    source: Literal["official_agency", "audited_expert"] | None = None
    rationale: str | None = None
    evidence_url: str | None = None

    @model_validator(mode="after")
    def assessment_is_attributable(self) -> "PublicHealthRisk":
        if self.status == "assessed" and not (self.level and self.source and self.rationale):
            raise ValueError("assessed public-health risk requires level, source, and rationale")
        if self.status == "not_assessed" and any((self.level, self.source, self.rationale)):
            raise ValueError("not_assessed public-health risk cannot carry an inferred level")
        return self


class AutomationDecision(ContractModel):
    """Fail-closed, auditable publication-policy result for one run signal."""

    status: Literal[
        "not_evaluated",
        "blocked",
        "shadow",
        "eligible",
        "auto_verified",
    ] = "not_evaluated"
    basis: Literal[
        "not_applicable",
        "calibrated_statistical",
        "official_corroboration",
    ] = "not_applicable"
    policy_version: str | None = None
    calibration_hash: str | None = None
    gate_reasons: list[str] = Field(default_factory=list)
    matched_event_ids: list[str] = Field(default_factory=list)
    decided_at: datetime | None = None


class SignalAssessment(ContractModel):
    review_priority: Literal["routine", "standard", "high"]
    signal_type: Literal["statistical_signal", "officially_correlated_signal"] = (
        "statistical_signal"
    )
    temporal_relevance: Literal["current", "lagged", "historical"] = "current"
    verification_status: Literal[
        "unreviewed",
        "under_review",
        "verified",
        "rejected",
    ] = "unreviewed"
    verification_basis: Literal[
        "not_verified",
        "automated_policy",
        "analyst_review",
    ] = "not_verified"
    verification_policy_version: str | None = None
    verification_note: str | None = None
    verified_by: str | None = None
    verified_at: datetime | None = None
    evidence_gaps: list[str] = Field(default_factory=list)
    automation_decision: AutomationDecision = Field(default_factory=AutomationDecision)
    public_health_risk: PublicHealthRisk = Field(default_factory=PublicHealthRisk)


class RecentPoint(ContractModel):
    period: date
    value: float
    expected: float | None = None
    predictive_upper_95: float | None = None


class EvidenceLink(ContractModel):
    title: str
    url: str
    source: str | None = None


class SignalLifecycle(ContractModel):
    status: Literal["new", "persistent", "resolved", "active", "routine"] = "routine"
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    active_run_count: int = Field(default=0, ge=0)
    peak_q_value: float | None = Field(default=None, ge=0, le=1)


class SituationSignalV3(ContractModel):
    identity: SignalIdentity
    observation: ObservationComparison
    anomaly: AnomalyAssessment
    assessment: SignalAssessment
    tags: list[Literal["increasing", "unusual", "respiratory", "severity", "official_match"]]
    recent_points: list[RecentPoint] = Field(default_factory=list)
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    lifecycle: SignalLifecycle = Field(default_factory=SignalLifecycle)


class EventUpdate(ContractModel):
    update_id: str
    source: str
    title: str
    url: str
    published_at: date


class SituationEventClusterV3(ContractModel):
    cluster_id: str
    disease_id: str
    disease_name: str
    geographies: list[dict[str, str]]
    first_published_at: date
    last_published_at: date
    status: Literal["active", "suppressed", "corrected", "merged"] = "active"
    updates: list[EventUpdate]
    matched_signal_ids: list[str] = Field(default_factory=list)


class ContextMetric(ContractModel):
    metric_type: str
    label: LocalizedText
    value: float | str | None
    unit: str
    data_through: date | None = None
    source_url: str | None = None


class ContextPanel(ContractModel):
    panel_id: str
    topic: str
    disease_id: str | None = None
    disease_name: str | None = None
    geography: str | None = None
    metrics: list[ContextMetric]
    note: LocalizedText | None = None


class SourceStatus(ContractModel):
    source_id: str
    label: str
    status: Literal["fresh", "partial", "stale", "failed", "not_checked"]
    checked_at: datetime | None = None
    last_success_at: datetime | None = None
    item_count: int | None = Field(default=None, ge=0)
    error: str | None = None


class QualityCheck(ContractModel):
    id: str
    passed: bool
    severity: Literal["blocking", "warning"] = "blocking"
    details: dict[str, Any] = Field(default_factory=dict)


class QualityGate(ContractModel):
    status: Literal["passed", "degraded", "failed"]
    passed: bool
    failed_checks: list[str]
    warning_checks: list[str] = Field(default_factory=list)
    checks: list[QualityCheck]


class SituationReportV3(ContractModel):
    schema_version: Literal["situation_room.v3"] = "situation_room.v3"
    public_enabled: bool
    report: ReportMetadata
    method: MethodMetadata
    data_currency: DataCurrency
    coverage: Coverage
    summary: ReportSummary
    signals: list[SituationSignalV3]
    events: list[SituationEventClusterV3]
    context_panels: list[ContextPanel]
    sources: list[SourceStatus]
    narrative: LocalizedText
    limitations: LocalizedText
    quality_gate: QualityGate

    @model_validator(mode="after")
    def unique_signal_identity(self) -> "SituationReportV3":
        ids = [signal.identity.signal_id for signal in self.signals]
        if len(ids) != len(set(ids)):
            raise ValueError("signals must contain unique signal_id values")
        if self.summary.unique_signal_count != len(ids):
            raise ValueError("summary.unique_signal_count must equal len(signals)")
        return self
