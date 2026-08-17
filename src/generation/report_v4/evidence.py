"""Deterministic evidence construction for report v4."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.generation.evidence import EvidenceAnalyzer

from .dataset import SourcePolicy
from .models import METHOD_VERSION, DeathReporting


def build_evidence_packet(
    *,
    data: pd.DataFrame,
    historical_data: pd.DataFrame | None,
    country: dict[str, Any],
    diseases: dict[int, dict[str, Any]],
    period_start,
    period_end,
    raw_sources: list[dict[str, Any]] | None = None,
    knowledge_status: dict[str, dict[str, Any]] | None = None,
    source_policy: SourcePolicy,
) -> dict[str, Any]:
    """Build the canonical v4 evidence packet from normalized input data."""
    packet = EvidenceAnalyzer().build_packet(
        data=data,
        historical_data=historical_data,
        country=country,
        diseases=diseases,
        period_start=period_start,
        period_end=period_end,
        raw_sources=raw_sources or [],
        knowledge_status=knowledge_status or {},
    )
    death_reporting = normalize_death_reporting(packet, source_policy)
    summary_metrics = dict(packet.get("summary_metrics") or {})
    summary_metrics["death_reporting"] = death_reporting.to_dict()
    summary_metrics["total_deaths"] = death_reporting.total_deaths
    packet["summary_metrics"] = summary_metrics
    packet["death_reporting"] = death_reporting.to_dict()
    packet["method_version"] = METHOD_VERSION
    return packet


def normalize_death_reporting(packet: dict[str, Any], source_policy: SourcePolicy) -> DeathReporting:
    """Map raw death observations into the explicit v4 death-reporting taxonomy."""
    observed_periods = 0
    missing_periods = 0
    reported_zero_periods = 0
    total_deaths = 0
    has_reported_positive = False
    has_partial = False

    for disease in packet.get("diseases") or []:
        reporting = ((disease.get("metrics") or {}).get("death_reporting") or {})
        observed_periods += int(reporting.get("observed_count") or 0)
        missing_periods += int(reporting.get("missing_count") or 0)
        reported_zero_periods += int(reporting.get("zero_count") or 0)
        disease_deaths = reporting.get("total_deaths")
        if isinstance(disease_deaths, (int, float)):
            total_deaths += int(disease_deaths)
        raw_status = str(reporting.get("status") or "unknown")
        if raw_status in {"reported_nonzero", "partial_reported_nonzero"}:
            has_reported_positive = True
        if raw_status.startswith("partial_"):
            has_partial = True

    policy = source_policy.to_dict()
    if source_policy.death_counts == "not_reported" and not has_reported_positive:
        status = "not_reported"
        total: int | None = None
    elif has_reported_positive:
        status = "partial" if has_partial or missing_periods > 0 else "reported_positive"
        total = total_deaths
    elif observed_periods > 0 and missing_periods > 0:
        status = "partial"
        total = total_deaths
    elif observed_periods > 0 and reported_zero_periods == observed_periods:
        status = "reported_zero"
        total = 0
    elif observed_periods == 0 and missing_periods > 0:
        status = "not_reported" if source_policy.death_counts == "not_reported" else "unknown"
        total = None
    else:
        status = "unknown"
        total = None

    return DeathReporting(
        status=status,
        total_deaths=total,
        observed_periods=observed_periods,
        missing_periods=missing_periods,
        reported_zero_periods=reported_zero_periods,
        source_policy=policy,
        display_note=death_display_note(status),
    )


def death_display_note(status: str) -> dict[str, str]:
    if status == "not_reported":
        return {
            "zh": "当前来源未提供死亡数字段，因此不能把空值或全空记录解释为死亡负担不存在。",
            "en": "The current source does not provide death-count fields, so missing values should not be interpreted as absence of mortality burden.",
        }
    if status == "unknown":
        return {
            "zh": "死亡数据口径不明确，报告仅展示病例信号，不对死亡负担作结论。",
            "en": "The death-count scope is unclear; this report describes case signals without concluding mortality burden.",
        }
    if status == "partial":
        return {
            "zh": "死亡数字段仅部分期次可用，相关判断需与病例趋势分开阅读。",
            "en": "Death counts are only partially available and should be interpreted separately from case trends.",
        }
    if status == "reported_zero":
        return {
            "zh": "来源已报告的死亡数为零；这只代表本监测口径下未记录死亡。",
            "en": "The source-reported death count is zero; this only describes the current surveillance scope.",
        }
    return {
        "zh": "当前来源报告了死亡数，报告中的死亡指标均来自已存储证据。",
        "en": "The current source reports death counts; mortality metrics in this report are evidence-bound.",
    }
