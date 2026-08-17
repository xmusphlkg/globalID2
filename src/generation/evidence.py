"""Deterministic evidence packet and rule checks for analytical reports.

The v3 report path keeps epidemiological numbers outside the LLM.  This module
turns report-period records into a compact, auditable evidence packet that
writers and reviewers can reference by stable evidence ids.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from src.core import get_logger
from src.core.missing_values import normalize_rate_columns
from src.generation.data_cleaner import Frequency, infer_frequency, long_to_wide
from src.generation.v3_historical import build_historical_context

logger = get_logger(__name__)

METHOD_VERSION = "report_v4.1"

ATTENTION_SCORE_SEMANTICS: Dict[str, Any] = {
    "type": "surveillance_attention_priority",
    "labels": {"zh": "监测关注优先级", "en": "Surveillance attention priority"},
    "description": {
        "zh": "用于安排监测信号复核顺序的确定性复合分，不是公共卫生风险估计。",
        "en": "A deterministic composite used to order surveillance review; it is not a public-health risk estimate.",
    },
    "scale": {"minimum": 0, "maximum": 100, "higher_means": "review_sooner"},
    "inputs": [
        "reported_case_burden",
        "reported_mortality_signal_when_available",
        "short_term_change",
        "deterministic_anomaly_marker",
        "historical_position",
        "data_quality_penalty",
    ],
    "limitations": {
        "zh": "该分值未经校准，不能解释为感染、重症、死亡或暴发的概率，也不能替代流行病学研判。",
        "en": "The score is uncalibrated and must not be read as the probability of infection, severe disease, death, or an outbreak, nor as a substitute for epidemiological assessment.",
    },
    "legacy_aliases": {
        "risk_score": "attention_score",
        "risk_level": "attention_level",
        "risk_ranking": "attention_ranking",
    },
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 4)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    number = _num(value)
    return int(number) if number is not None else 0


def _optional_int(value: Any) -> Optional[int]:
    number = _num(value)
    return int(number) if number is not None else None


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    if previous == 0:
        return 0.0 if current == 0 else None
    return round(((current - previous) / previous) * 100.0, 2)


def _fmt_date(value: Any) -> str:
    if value is None:
        return ""
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def _trend_label(change_pct: Optional[float]) -> str:
    if change_pct is None:
        return "new_or_reappearing"
    if change_pct >= 25:
        return "increasing"
    if change_pct <= -25:
        return "decreasing"
    return "stable"


def _confidence_from_quality(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def _metric_int(value: Any) -> int:
    number = _num(value)
    return int(number) if number is not None else 0


@dataclass
class DatasetBuilder:
    """Normalize report records before analytical calculations."""

    period_start: datetime
    period_end: datetime

    def build(self, data: pd.DataFrame) -> pd.DataFrame:
        if data is None or data.empty:
            return pd.DataFrame()

        df = data.copy()
        df = normalize_rate_columns(df, copy=False)
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)

        for column in ("cases", "deaths", "new_cases", "new_deaths", "recoveries"):
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

        for column in (
            "incidence_rate",
            "mortality_rate",
            "recovery_rate",
            "confidence_score",
            "population_denominator",
        ):
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

        if "time" in df.columns:
            df = df[df["time"].notna()].sort_values(["disease_id", "time"])

        return df.reset_index(drop=True)


class EvidenceAnalyzer:
    """Generate a deterministic AnalysisEvidencePacket."""

    def __init__(self, *, method_version: str = METHOD_VERSION):
        self.method_version = method_version

    def build_packet(
        self,
        *,
        data: pd.DataFrame,
        historical_data: Optional[pd.DataFrame] = None,
        country: Dict[str, Any],
        diseases: Dict[int, Dict[str, Any]],
        period_start: datetime,
        period_end: datetime,
        raw_sources: Optional[List[Dict[str, Any]]] = None,
        knowledge_status: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        builder = DatasetBuilder(period_start=period_start, period_end=period_end)
        df = builder.build(data)
        history_df = builder.build(historical_data) if historical_data is not None else df
        frequency_input = df.drop_duplicates("time") if not df.empty and "time" in df.columns else df
        frequency = infer_frequency(frequency_input, "time") if not df.empty else "monthly"
        disease_packets = self._build_diseases(df, diseases, frequency, knowledge_status or {}, history_df)
        self._rank_diseases(disease_packets)

        summary_metrics = self._summary_metrics(df, disease_packets)
        data_quality = self._data_quality(df, period_end)
        sources = self._sources(df, raw_sources or [])
        attention_ranking = self._attention_ranking(disease_packets)
        packet = {
            "method_version": self.method_version,
            "country": country,
            "period": {
                "start": _fmt_date(period_start),
                "end": _fmt_date(period_end),
            },
            "reporting_cadence": frequency,
            "data_signature": self._signature(df, country, period_start, period_end),
            "summary_metrics": summary_metrics,
            "score_semantics": ATTENTION_SCORE_SEMANTICS,
            "attention_ranking": attention_ranking,
            # Deprecated compatibility alias. New consumers must use
            # ``attention_ranking`` and the explicit semantics above.
            "risk_ranking": attention_ranking,
            "diseases": disease_packets,
            "data_quality": data_quality,
            "sources": sources,
            "evidence_index": self._evidence_index(summary_metrics, disease_packets, data_quality),
        }
        packet["analysis_summary"] = self._analysis_summary(packet)
        return _json_safe(packet)

    def _build_diseases(
        self,
        df: pd.DataFrame,
        diseases: Dict[int, Dict[str, Any]],
        frequency: Frequency,
        knowledge_status: Dict[str, Dict[str, Any]],
        historical_df: Optional[pd.DataFrame] = None,
    ) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        if df.empty or "disease_id" not in df.columns:
            return packets
        historical_groups = {}
        if historical_df is not None and not historical_df.empty and "disease_id" in historical_df.columns:
            historical_groups = {
                int(history_disease_id): history_group.copy()
                for history_disease_id, history_group in historical_df.groupby("disease_id")
            }

        for disease_id, group in df.groupby("disease_id"):
            meta = diseases.get(int(disease_id), {})
            disease_code = str(meta.get("code") or meta.get("name") or disease_id)
            wide = long_to_wide(group.copy(), frequency, time_col="time")
            if wide.empty:
                continue
            wide = wide.sort_values("_period").reset_index(drop=True)
            latest = wide.iloc[-1]
            previous = wide.iloc[-2] if len(wide) >= 2 else None
            baseline = wide.iloc[:-1] if len(wide) > 1 else wide.iloc[0:0]

            latest_cases = _int(latest.get("cases"))
            previous_cases = _int(previous.get("cases")) if previous is not None else None
            latest_incidence = _num(latest.get("incidence_rate"))
            previous_incidence = _num(previous.get("incidence_rate")) if previous is not None else None
            incidence_change_pct = _pct_change(latest_incidence, previous_incidence)
            latest_population = _num(latest.get("population_denominator"))
            total_cases = int(pd.to_numeric(wide.get("cases", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
            death_reporting = self._death_reporting(group, wide)
            latest_deaths = death_reporting.get("latest_deaths")
            previous_deaths = death_reporting.get("previous_deaths")
            total_deaths = death_reporting.get("total_deaths")
            period_crude_incidence = (
                round((total_cases / latest_population) * 100000.0, 6)
                if latest_population and latest_population > 0
                else None
            )
            change_pct = _pct_change(latest_cases, previous_cases)
            death_change_pct = _pct_change(latest_deaths, previous_deaths)
            yoy_cases = self._prior_year_change(wide, "cases", frequency)
            rolling_cases = self._rolling_mean(wide, "cases")
            anomaly = self._latest_anomaly(wide, "cases")
            fatality_rate = (
                round((_metric_int(total_deaths) / total_cases) * 100.0, 4)
                if total_cases > 0 and total_deaths is not None
                else None
            )
            missing_rates = self._missing_rates(group)
            quality_score = self._disease_quality_score(group, missing_rates)
            incidence_sources = self._incidence_sources(group)
            population_years = sorted(
                {
                    int(value)
                    for value in pd.to_numeric(group.get("population_year", pd.Series(dtype=float)), errors="coerce").dropna().tolist()
                }
            )
            population_sources = sorted(
                [str(value) for value in group.get("population_source", pd.Series(dtype=str)).dropna().unique()]
            )
            visual_diagnostics = self._visual_diagnostics(
                wide,
                death_unavailable=death_reporting.get("status") == "unavailable",
            )
            history_group = historical_groups.get(int(disease_id), group)
            historical_wide = long_to_wide(history_group.copy(), frequency, time_col="time")
            if not historical_wide.empty:
                historical_wide = historical_wide.sort_values("_period").reset_index(drop=True)
            historical_context = build_historical_context(historical_wide if not historical_wide.empty else wide, frequency)
            attention_score = self._attention_score(
                latest_cases=latest_cases,
                total_cases=total_cases,
                latest_deaths=_metric_int(latest_deaths),
                total_deaths=_metric_int(total_deaths),
                change_pct=change_pct,
                anomaly=anomaly,
                quality_score=quality_score,
                historical_context=historical_context,
            )
            status = knowledge_status.get(disease_code, {})

            packets.append(
                {
                    "evidence_id": f"disease:{disease_code}",
                    "disease_id": disease_code,
                    "db_disease_id": int(disease_id),
                    "name_en": meta.get("name_en") or disease_code,
                    "name_zh": meta.get("name_zh") or meta.get("name_en") or disease_code,
                    "category": meta.get("category"),
                    "period_start": _fmt_date(wide["_period"].min()),
                    "period_end": _fmt_date(wide["_period"].max()),
                    "record_count": int(len(group)),
                    "observation_count": int(len(wide)),
                    "latest_period": str(latest.get("_period_str") or _fmt_date(latest.get("_period"))),
                    "previous_period": str(previous.get("_period_str") or _fmt_date(previous.get("_period"))) if previous is not None else None,
                    "metrics": {
                        "total_cases": total_cases,
                        "total_deaths": total_deaths,
                        "total_deaths_reported": _metric_int(total_deaths),
                        "latest_cases": latest_cases,
                        "latest_deaths": latest_deaths,
                        "previous_cases": previous_cases,
                        "previous_deaths": previous_deaths,
                        "change_pct": change_pct,
                        "death_change_pct": death_change_pct,
                        "death_reporting_status": death_reporting.get("status"),
                        "death_reporting": death_reporting,
                        "latest_incidence_rate_per_100k": latest_incidence,
                        "previous_incidence_rate_per_100k": previous_incidence,
                        "incidence_change_pct": incidence_change_pct,
                        "period_crude_incidence_per_100k": period_crude_incidence,
                        "population_denominator": latest_population,
                        "population_years": population_years,
                        "population_sources": population_sources,
                        "incidence_rate_sources": incidence_sources,
                        "yoy_change_pct": yoy_cases,
                        "rolling_mean_cases": rolling_cases,
                        "fatality_rate_pct": fatality_rate,
                    },
                    "baseline": self._baseline_stats(baseline),
                    "trend": {
                        "label": _trend_label(change_pct),
                        "change_pct": change_pct,
                        "yoy_change_pct": yoy_cases,
                    },
                    "anomaly": anomaly,
                    "visual_diagnostics": visual_diagnostics,
                    "historical_context": historical_context,
                    "attention": {
                        "score": attention_score,
                        "level": self._attention_level(attention_score),
                        "semantic_type": "surveillance_attention_priority",
                        "drivers": self._attention_drivers(latest_cases, _metric_int(latest_deaths), change_pct, anomaly, quality_score, historical_context),
                    },
                    # Deprecated compatibility object. This is intentionally an
                    # alias, not a claim about public-health risk.
                    "risk": {
                        "score": attention_score,
                        "level": self._attention_level(attention_score),
                        "drivers": self._attention_drivers(latest_cases, _metric_int(latest_deaths), change_pct, anomaly, quality_score, historical_context),
                        "deprecated": True,
                        "alias_for": "attention",
                    },
                    "data_quality": {
                        "score": quality_score,
                        "confidence": _confidence_from_quality(quality_score),
                        "missing_rates": missing_rates,
                        "source_count": int(group["data_source"].nunique()) if "data_source" in group.columns else 0,
                    },
                    "limitations": self._limitations(group, missing_rates, incidence_sources, death_reporting),
                    "source_coverage": sorted(
                        [str(s) for s in group.get("data_source", pd.Series(dtype=str)).dropna().unique()]
                    )[:8],
                    "knowledge_status": status.get("status", "missing"),
                    "knowledge_updated_at": status.get("updated_at"),
                    "knowledge_context": self._knowledge_context(status),
                }
            )
        return packets

    def _rank_diseases(self, packets: List[Dict[str, Any]]) -> None:
        by_burden = sorted(packets, key=lambda p: p["metrics"]["total_cases"], reverse=True)
        by_attention = sorted(packets, key=lambda p: p["attention"]["score"], reverse=True)
        for rank, packet in enumerate(by_burden, 1):
            packet["burden_rank"] = rank
        for rank, packet in enumerate(by_attention, 1):
            packet["attention_rank"] = rank
            packet["risk_rank"] = rank  # Deprecated compatibility alias.

    @staticmethod
    def _summary_metrics(df: pd.DataFrame, disease_packets: List[Dict[str, Any]]) -> Dict[str, Any]:
        active_latest = sum(1 for d in disease_packets if _metric_int(d["metrics"].get("latest_cases")) > 0 or _metric_int(d["metrics"].get("latest_deaths")) > 0)
        high_attention = sum(1 for d in disease_packets if d["attention"]["level"] in {"critical", "high"})
        death_status_counts: Dict[str, int] = {}
        for disease in disease_packets:
            status = str((disease.get("metrics") or {}).get("death_reporting_status") or "unknown")
            death_status_counts[status] = death_status_counts.get(status, 0) + 1
        if disease_packets and death_status_counts.get("unavailable", 0) == len(disease_packets):
            death_status = "unavailable"
        elif any(status in death_status_counts for status in ("reported_nonzero", "partial_reported_nonzero")):
            death_status = "reported_nonzero"
        elif death_status_counts.get("partial_reported_zero"):
            death_status = "partial_reported_zero"
        elif death_status_counts.get("reported_zero"):
            death_status = "reported_zero"
        else:
            death_status = "unknown"
        return {
            "record_count": int(len(df)),
            "disease_count": len(disease_packets),
            "active_latest_diseases": active_latest,
            "high_attention_diseases": high_attention,
            "high_risk_diseases": high_attention,  # Deprecated compatibility alias.
            "total_cases": int(sum(_metric_int(d["metrics"].get("total_cases")) for d in disease_packets)),
            "total_deaths": int(sum(_metric_int(d["metrics"].get("total_deaths")) for d in disease_packets)),
            "latest_cases": int(sum(_metric_int(d["metrics"].get("latest_cases")) for d in disease_packets)),
            "latest_deaths": int(sum(_metric_int(d["metrics"].get("latest_deaths")) for d in disease_packets)),
            "death_reporting": {
                "status": death_status,
                "status_counts": death_status_counts,
                "unavailable_diseases": int(death_status_counts.get("unavailable", 0)),
                "reported_zero_diseases": int(death_status_counts.get("reported_zero", 0)),
                "reported_nonzero_diseases": int(
                    death_status_counts.get("reported_nonzero", 0)
                    + death_status_counts.get("partial_reported_nonzero", 0)
                ),
            },
        }

    @staticmethod
    def _data_quality(df: pd.DataFrame, period_end: datetime) -> Dict[str, Any]:
        if df.empty:
            return {
                "score": 0.0,
                "confidence": "low",
                "issues": ["No records available"],
                "missing_rates": {},
            }

        issues: List[str] = []
        missing_rates = {}
        death_unavailable = EvidenceAnalyzer._source_likely_no_deaths(df)
        death_values = pd.to_numeric(df.get("deaths", pd.Series(dtype=float)), errors="coerce")
        if death_unavailable and not (death_values.dropna() > 0).any():
            issues.append("Death count field is not provided by this source; legacy zero values should be treated as unavailable.")

        for column in ("cases", "deaths", "incidence_rate", "mortality_rate"):
            if column in df.columns:
                missing_rate = float(df[column].isna().mean())
                if column == "deaths" and death_unavailable and not (death_values.dropna() > 0).any():
                    missing_rate = 1.0
                missing_rates[column] = round(missing_rate, 4)
            elif column in {"cases", "deaths"}:
                missing_rates[column] = 1.0
                issues.append(f"{column.title()} field is unavailable in the extracted records.")
        negative_count = 0
        for column in ("cases", "deaths", "new_cases", "new_deaths"):
            if column in df.columns:
                negative_count += int((pd.to_numeric(df[column], errors="coerce") < 0).sum())
        if negative_count:
            issues.append(f"{negative_count} negative surveillance values detected")

        future_count = 0
        if "time" in df.columns:
            end = pd.Timestamp(period_end)
            if end.tzinfo is None:
                end = end.tz_localize("UTC")
            else:
                end = end.tz_convert("UTC")
            future_count = int((df["time"] > end).sum())
            if future_count:
                issues.append(f"{future_count} records are after the report period end")

        score = 1.0 - (0.45 * missing_rates.get("cases", 1.0) + 0.25 * missing_rates.get("deaths", 1.0))
        optional_rate_missing = [
            missing_rates[column]
            for column in ("incidence_rate", "mortality_rate")
            if column in missing_rates
        ]
        if optional_rate_missing and sum(optional_rate_missing) / len(optional_rate_missing) >= 0.95:
            issues.append("Incidence or mortality rate fields are largely unavailable; interpretation is count-based.")
        incidence_sources = {}
        if "incidence_rate_source" in df.columns:
            incidence_sources = {
                str(source): int(count)
                for source, count in df["incidence_rate_source"].fillna("missing_population").value_counts().items()
            }
            if incidence_sources.get("wpp_computed_crude"):
                issues.append(
                    "Some incidence rates are WPP-computed crude population rates, not source-provided rates."
                )
            data_source_text = " ".join(
                str(value).lower()
                for value in df.get("data_source", pd.Series(dtype=str)).dropna().unique()
            )
            if incidence_sources.get("wpp_computed_crude") and "sentinel" in data_source_text:
                issues.append(
                    "Sentinel surveillance data are present; crude population incidence should be interpreted as contextual, not as a sentinel per-site rate."
                )
        score -= 0.1 * (sum(optional_rate_missing) / max(len(optional_rate_missing), 1)) if optional_rate_missing else 0.0
        score -= min(0.3, negative_count * 0.02)
        score -= min(0.4, future_count * 0.05)
        score = round(max(0.0, min(1.0, score)), 3)

        return {
            "score": score,
            "confidence": _confidence_from_quality(score),
            "issues": issues,
            "missing_rates": missing_rates,
            "negative_value_count": negative_count,
            "future_record_count": future_count,
            "source_count": int(df["data_source"].nunique()) if "data_source" in df.columns else 0,
            "incidence_rate_sources": incidence_sources,
        }

    @staticmethod
    def _sources(df: pd.DataFrame, raw_sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        if "data_source" in df.columns:
            for source, count in df["data_source"].fillna("unknown").value_counts().head(10).items():
                sources.append({"title": str(source), "record_count": int(count), "url": None, "type": "database"})
        for raw in raw_sources[:5]:
            sources.append(
                {
                    "title": raw.get("title") or raw.get("source") or "Raw source",
                    "url": raw.get("url"),
                    "type": raw.get("source") or "raw_page",
                    "fetched_at": raw.get("fetched_at"),
                }
            )
        return sources

    @staticmethod
    def _signature(
        df: pd.DataFrame,
        country: Dict[str, Any],
        period_start: datetime,
        period_end: datetime,
    ) -> str:
        if df.empty:
            rows: List[Dict[str, Any]] = []
        else:
            cols = [
                c
                for c in (
                    "time",
                    "disease_id",
                    "cases",
                    "deaths",
                    "new_cases",
                    "new_deaths",
                    "incidence_rate",
                    "incidence_rate_source",
                    "population_denominator",
                    "data_source",
                    "metadata",
                )
                if c in df.columns
            ]
            rows = [
                {col: _json_safe(row[col]) for col in cols}
                for _, row in df[cols].sort_values(cols[:2] if len(cols) >= 2 else cols).iterrows()
            ]
        payload = {
            "country": country.get("code") or country.get("id"),
            "period_start": _fmt_date(period_start),
            "period_end": _fmt_date(period_end),
            "rows": rows,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _evidence_index(
        summary_metrics: Dict[str, Any],
        disease_packets: List[Dict[str, Any]],
        data_quality: Dict[str, Any],
    ) -> Dict[str, Any]:
        index = {
            "summary:total_cases": summary_metrics.get("total_cases"),
            "summary:total_deaths": summary_metrics.get("total_deaths"),
            "summary:latest_cases": summary_metrics.get("latest_cases"),
            "summary:high_risk_diseases": summary_metrics.get("high_risk_diseases"),
            "summary:high_attention_diseases": summary_metrics.get("high_attention_diseases"),
            "quality:score": data_quality.get("score"),
        }
        for disease in disease_packets:
            prefix = f"disease:{disease['disease_id']}"
            index[f"{prefix}.latest_cases"] = disease["metrics"]["latest_cases"]
            index[f"{prefix}.total_cases"] = disease["metrics"]["total_cases"]
            index[f"{prefix}.latest_deaths"] = disease["metrics"].get("latest_deaths")
            index[f"{prefix}.total_deaths"] = disease["metrics"].get("total_deaths")
            index[f"{prefix}.change_pct"] = disease["metrics"]["change_pct"]
            index[f"{prefix}.latest_incidence_rate_per_100k"] = disease["metrics"].get("latest_incidence_rate_per_100k")
            index[f"{prefix}.period_crude_incidence_per_100k"] = disease["metrics"].get("period_crude_incidence_per_100k")
            index[f"{prefix}.attention_score"] = disease["attention"]["score"]
            index[f"{prefix}.risk_score"] = disease["attention"]["score"]  # Deprecated alias.
            visual = disease.get("visual_diagnostics") or {}
            history = disease.get("historical_context") or {}
            index[f"{prefix}.latest_4_period_cases"] = visual.get("latest_4_period_cases")
            index[f"{prefix}.last4_change_pct"] = visual.get("last4_change_pct")
            index[f"{prefix}.peak_cases"] = visual.get("peak_cases")
            index[f"{prefix}.latest_percentile_prior"] = history.get("latest_percentile_prior")
            index[f"{prefix}.long_window_change_pct"] = history.get("long_window_change_pct")
            index[f"{prefix}.latest_to_same_season_median_ratio"] = history.get("latest_to_same_season_median_ratio")
        return index

    @staticmethod
    def _analysis_summary(packet: Dict[str, Any]) -> Dict[str, Any]:
        ranking = packet.get("attention_ranking") or packet.get("risk_ranking") or []
        return {
            "top_attention_diseases": ranking[:5],
            "high_attention_count": packet.get("summary_metrics", {}).get("high_attention_diseases", 0),
            # Deprecated aliases retained for older prompt/report consumers.
            "top_risk_diseases": ranking[:5],
            "high_risk_count": packet.get("summary_metrics", {}).get("high_attention_diseases", 0),
            "data_quality_score": packet.get("data_quality", {}).get("score"),
            "data_signature": packet.get("data_signature"),
        }

    @staticmethod
    def _source_likely_no_deaths(group: pd.DataFrame) -> bool:
        """Detect case-only feeds that historically stored missing deaths as zero."""
        if group is None or group.empty:
            return False

        text_parts: List[str] = []
        if "data_source" in group.columns:
            text_parts.extend(str(value) for value in group["data_source"].dropna().unique())
        if "metadata" in group.columns:
            for value in group["metadata"].dropna().head(20):
                if isinstance(value, dict):
                    text_parts.append(json.dumps(value, ensure_ascii=False))
                else:
                    text_parts.append(str(value))
        source_text = " ".join(text_parts).lower()
        explicit_markers = (
            "not_provided_by_source",
            "source_does_not_report_deaths",
            "death_reporting\": \"not_provided",
            "death_reporting: not_provided",
        )
        if any(marker in source_text for marker in explicit_markers):
            return True
        case_only_markers = (
            "japan niid weekly sentinel",
            "idwr",
            "nndss",
            "australia national notifiable",
            "australia nndss",
            "hong kong",
            "new zealand",
            "switzerland idd",
            "brazil sinan",
            "korea kdca",
            "taiwan cdc",
        )
        return any(marker in source_text for marker in case_only_markers)

    @staticmethod
    def _death_reporting(group: pd.DataFrame, wide: pd.DataFrame) -> Dict[str, Any]:
        record_count = int(len(group)) if group is not None else 0
        source_likely_unavailable = EvidenceAnalyzer._source_likely_no_deaths(group)
        if group is None or group.empty or "deaths" not in group.columns:
            raw_values = pd.Series([math.nan] * record_count, dtype=float)
        else:
            raw_values = pd.to_numeric(group["deaths"], errors="coerce")

        nonzero_reported = bool((raw_values.dropna() > 0).any())
        treat_as_unavailable = bool(source_likely_unavailable and not nonzero_reported)
        effective_values = pd.Series([math.nan] * record_count, dtype=float) if treat_as_unavailable else raw_values
        observed = effective_values.dropna()
        observed_count = int(observed.count())
        missing_count = int(record_count - observed_count)
        zero_count = int((observed == 0).sum()) if observed_count else 0
        nonzero_count = int((observed > 0).sum()) if observed_count else 0

        wide_deaths = pd.to_numeric(wide.get("deaths", pd.Series(dtype=float)), errors="coerce")
        if treat_as_unavailable:
            wide_deaths = pd.Series([math.nan] * len(wide), dtype=float)
        latest_deaths = _optional_int(wide_deaths.iloc[-1]) if len(wide_deaths) else None
        previous_deaths = _optional_int(wide_deaths.iloc[-2]) if len(wide_deaths) >= 2 else None

        if observed_count == 0:
            status = "unavailable"
        elif nonzero_count > 0 and missing_count > 0:
            status = "partial_reported_nonzero"
        elif nonzero_count > 0:
            status = "reported_nonzero"
        elif missing_count > 0:
            status = "partial_reported_zero"
        else:
            status = "reported_zero"

        return {
            "status": status,
            "source_likely_case_only": treat_as_unavailable,
            "record_count": record_count,
            "observed_count": observed_count,
            "missing_count": missing_count,
            "zero_count": zero_count,
            "nonzero_count": nonzero_count,
            "all_reported_values_zero": bool(observed_count > 0 and nonzero_count == 0),
            "has_reported_deaths": bool(nonzero_count > 0),
            "total_deaths": int(observed.sum()) if observed_count else None,
            "latest_deaths": latest_deaths,
            "previous_deaths": previous_deaths,
            "latest_is_missing": latest_deaths is None,
            "interpretation": (
                "death_counts_not_reported_by_source"
                if status == "unavailable"
                else "reported_death_values_are_all_zero"
                if status in {"reported_zero", "partial_reported_zero"}
                else "reported_deaths_present"
            ),
        }

    @staticmethod
    def _missing_rates(group: pd.DataFrame) -> Dict[str, float]:
        rates = {}
        for column in ("cases", "deaths", "incidence_rate", "mortality_rate"):
            if column in group.columns:
                missing_rate = float(group[column].isna().mean())
                if column == "deaths" and EvidenceAnalyzer._source_likely_no_deaths(group):
                    values = pd.to_numeric(group[column], errors="coerce")
                    if not (values.dropna() > 0).any():
                        missing_rate = 1.0
                rates[column] = round(missing_rate, 4)
            elif column in {"cases", "deaths"}:
                rates[column] = 1.0
        return rates

    @staticmethod
    def _incidence_sources(group: pd.DataFrame) -> Dict[str, int]:
        if "incidence_rate_source" not in group.columns:
            return {}
        return {
            str(source): int(count)
            for source, count in group["incidence_rate_source"].fillna("missing_population").value_counts().items()
        }

    @staticmethod
    def _disease_quality_score(group: pd.DataFrame, missing_rates: Dict[str, float]) -> float:
        score = 1.0
        score -= 0.35 * missing_rates.get("cases", 0.0)
        score -= 0.2 * missing_rates.get("deaths", 0.0)
        if len(group) < 3:
            score -= 0.15
        for column in ("cases", "deaths"):
            if column in group.columns and (pd.to_numeric(group[column], errors="coerce") < 0).any():
                score -= 0.25
        return round(max(0.0, min(1.0, score)), 3)

    @staticmethod
    def _baseline_stats(baseline: pd.DataFrame) -> Dict[str, Any]:
        if baseline is None or baseline.empty:
            return {"period_count": 0}
        cases = pd.to_numeric(baseline.get("cases", pd.Series(dtype=float)), errors="coerce").dropna()
        deaths = pd.to_numeric(baseline.get("deaths", pd.Series(dtype=float)), errors="coerce").dropna()
        return {
            "period_count": int(len(baseline)),
            "mean_cases": round(float(cases.mean()), 2) if not cases.empty else None,
            "median_cases": round(float(cases.median()), 2) if not cases.empty else None,
            "max_cases": int(cases.max()) if not cases.empty else None,
            "mean_deaths": round(float(deaths.mean()), 2) if not deaths.empty else None,
        }

    @staticmethod
    def _rolling_mean(wide: pd.DataFrame, column: str, window: int = 3) -> Optional[float]:
        if column not in wide.columns or wide.empty:
            return None
        values = pd.to_numeric(wide[column], errors="coerce").dropna()
        if values.empty:
            return None
        return round(float(values.tail(window).mean()), 2)

    @staticmethod
    def _latest_anomaly(wide: pd.DataFrame, column: str) -> Dict[str, Any]:
        if column not in wide.columns or len(wide) < 4:
            return {
                "is_anomaly": False,
                "method": "robust_mad",
                "reason": "insufficient_baseline",
            }
        values = pd.to_numeric(wide[column], errors="coerce").dropna()
        if len(values) < 4:
            return {
                "is_anomaly": False,
                "method": "robust_mad",
                "reason": "insufficient_numeric_values",
            }
        latest = float(values.iloc[-1])
        baseline = values.iloc[:-1]
        median = float(baseline.median())
        mad = float((baseline - median).abs().median())
        std = float(baseline.std()) if len(baseline) > 1 else 0.0
        robust_z = round(0.6745 * (latest - median) / mad, 3) if mad else None
        z_score = round((latest - float(baseline.mean())) / std, 3) if std else None
        is_anomaly = bool((robust_z is not None and abs(robust_z) >= 3.5) or (z_score is not None and abs(z_score) >= 3.0))
        severity = "none"
        if is_anomaly:
            max_score = max(abs(robust_z or 0), abs(z_score or 0))
            severity = "high" if max_score >= 5 else "medium"
        return {
            "is_anomaly": is_anomaly,
            "method": "robust_mad",
            "metric": column,
            "latest_value": latest,
            "baseline_median": round(median, 2),
            "robust_z": robust_z,
            "z_score": z_score,
            "severity": severity,
        }

    @staticmethod
    def _visual_diagnostics(wide: pd.DataFrame, *, death_unavailable: bool = False) -> Dict[str, Any]:
        """Build chart-ready time-series diagnostics for AI and UI review."""
        if wide.empty:
            return {
                "chart_kind": "time_series_cases",
                "chart_spec": {"x": "period", "y": ["cases"]},
                "series": [],
                "observations": ["No chartable observations are available."],
            }

        values = pd.to_numeric(wide.get("cases", pd.Series(dtype=float)), errors="coerce").fillna(0)
        death_raw = pd.to_numeric(wide.get("deaths", pd.Series(dtype=float)), errors="coerce")
        if death_unavailable:
            death_raw = pd.Series([math.nan] * len(wide), dtype=float)
        deaths = death_raw.fillna(0)
        incidence = pd.to_numeric(wide.get("incidence_rate", pd.Series(dtype=float)), errors="coerce")
        periods = wide.get("_period_str", pd.Series([str(i) for i in range(len(wide))]))
        series = [
            {
                "period": str(periods.iloc[i]),
                "cases": int(values.iloc[i]),
                "deaths": int(deaths.iloc[i]) if i < len(deaths) and pd.notna(death_raw.iloc[i]) else None,
                "deaths_observed": bool(i < len(death_raw) and pd.notna(death_raw.iloc[i])),
                "incidence_rate_per_100k": (
                    round(float(incidence.iloc[i]), 6)
                    if i < len(incidence) and pd.notna(incidence.iloc[i])
                    else None
                ),
            }
            for i in range(len(wide))
        ][-52:]

        peak_idx = int(values.idxmax()) if len(values) else 0
        latest = float(values.iloc[-1]) if len(values) else 0.0
        latest_4 = float(values.tail(4).sum()) if len(values) else 0.0
        previous_4 = float(values.iloc[-8:-4].sum()) if len(values) >= 8 else None
        last4_change = _pct_change(latest_4, previous_4)
        baseline = values.iloc[:-1]
        baseline_median = float(baseline.median()) if len(baseline) else None
        above_baseline_ratio = None
        if baseline_median is not None and baseline_median > 0:
            above_baseline_ratio = round(latest / baseline_median, 3)
        total_cases = float(values.sum())
        last4_share = round((latest_4 / total_cases) * 100.0, 2) if total_cases > 0 else 0.0
        recent_slope = EvidenceAnalyzer._recent_slope(values.tail(6))
        consecutive_increases = EvidenceAnalyzer._consecutive_increases(values)

        observations = []
        if previous_4 is not None:
            if last4_change is None:
                observations.append("Last 4 periods cannot be compared because the previous window was zero.")
            else:
                observations.append(f"Last 4 periods changed {last4_change:+.1f}% versus the preceding 4 periods.")
        if above_baseline_ratio is not None:
            observations.append(f"Latest value is {above_baseline_ratio:.1f}x the pre-latest median.")
        else:
            observations.append("Latest-to-baseline ratio is not available.")
        latest_incidence = _num(incidence.iloc[-1]) if len(incidence) else None
        if latest_incidence is not None:
            observations.append(f"Latest crude incidence is {latest_incidence:.3f} per 100,000 population.")
        observations.append(f"Peak observed period is {str(periods.iloc[peak_idx])} with {int(values.iloc[peak_idx]):,} cases.")

        return {
            "chart_kind": "time_series_cases",
            "chart_spec": {
                "x": "period",
                "y": ["cases"],
                "baseline": "pre_latest_median_cases",
                "recommended_marks": ["line", "points", "latest_marker", "peak_marker"],
            },
            "series": series,
            "peak_period": str(periods.iloc[peak_idx]),
            "peak_cases": int(values.iloc[peak_idx]),
            "latest_4_period_cases": int(latest_4),
            "previous_4_period_cases": int(previous_4) if previous_4 is not None else None,
            "last4_change_pct": last4_change,
            "last4_share_pct": last4_share,
            "pre_latest_median_cases": round(baseline_median, 2) if baseline_median is not None else None,
            "latest_to_baseline_ratio": above_baseline_ratio,
            "recent_slope_cases_per_period": recent_slope,
            "consecutive_increase_periods": consecutive_increases,
            "observations": observations,
        }

    @staticmethod
    def _recent_slope(values: pd.Series) -> Optional[float]:
        if len(values) < 2:
            return None
        y = [float(value) for value in values.tolist()]
        x_mean = (len(y) - 1) / 2
        y_mean = sum(y) / len(y)
        denom = sum((i - x_mean) ** 2 for i in range(len(y)))
        if denom == 0:
            return None
        slope = sum((i - x_mean) * (value - y_mean) for i, value in enumerate(y)) / denom
        return round(float(slope), 3)

    @staticmethod
    def _consecutive_increases(values: pd.Series) -> int:
        if len(values) < 2:
            return 0
        count = 0
        cleaned = [float(value) for value in values.tolist()]
        for index in range(len(cleaned) - 1, 0, -1):
            if cleaned[index] > cleaned[index - 1]:
                count += 1
            else:
                break
        return count

    @staticmethod
    def _knowledge_context(status: Dict[str, Any]) -> Dict[str, Any]:
        fields = (
            "brief",
            "definition",
            "clinical_features",
            "epidemiology",
            "transmission",
            "prevention",
            "surveillance_note",
            "risk_groups",
            "source_confidence",
            "disclaimer",
        )
        context = {field: status.get(field) for field in fields if status.get(field)}
        source_attribution = status.get("source_attribution")
        if isinstance(source_attribution, list):
            context["source_attribution"] = source_attribution[:5]
        return context

    @staticmethod
    def _prior_year_change(wide: pd.DataFrame, column: str, frequency: Frequency) -> Optional[float]:
        if column not in wide.columns or "_period" not in wide.columns or len(wide) < 2:
            return None
        latest_period = pd.Timestamp(wide["_period"].iloc[-1])
        latest_value = _num(wide[column].iloc[-1])
        if latest_value is None:
            return None
        target = latest_period - pd.DateOffset(years=1)
        if frequency == "weekly":
            tolerance_days = 14
        elif frequency == "monthly":
            tolerance_days = 35
        else:
            tolerance_days = 3
        candidates = wide.iloc[:-1].copy()
        candidates["_distance"] = candidates["_period"].apply(lambda value: abs((pd.Timestamp(value) - target).days))
        nearest = candidates[candidates["_distance"] <= tolerance_days].sort_values("_distance").head(1)
        if nearest.empty:
            return None
        return _pct_change(latest_value, _num(nearest[column].iloc[0]))

    @staticmethod
    def _attention_score(
        *,
        latest_cases: int,
        total_cases: int,
        latest_deaths: int,
        total_deaths: int,
        change_pct: Optional[float],
        anomaly: Dict[str, Any],
        quality_score: float,
        historical_context: Optional[Dict[str, Any]] = None,
    ) -> int:
        latest_cases = max(0, latest_cases)
        total_cases = max(0, total_cases)
        latest_deaths = max(0, latest_deaths)
        total_deaths = max(0, total_deaths)
        burden = min(35.0, math.log1p(max(latest_cases, total_cases * 0.05)) * 4.5)
        mortality = min(25.0, math.log1p(max(latest_deaths, total_deaths * 0.25)) * 7.0)
        change = 0.0 if change_pct is None else min(20.0, max(0.0, change_pct) / 5.0)
        anomaly_score = 15.0 if anomaly.get("is_anomaly") else 0.0
        historical_context = historical_context or {}
        historical_percentile = _num(historical_context.get("latest_percentile_prior"))
        historical_score = 0.0
        if historical_percentile is not None and historical_percentile >= 90:
            historical_score += 7.0
        elif historical_percentile is not None and historical_percentile >= 75:
            historical_score += 4.0
        long_window_change = _num(historical_context.get("long_window_change_pct"))
        if long_window_change is not None and long_window_change > 0:
            historical_score += min(8.0, long_window_change / 12.5)
        same_season_ratio = _num(historical_context.get("latest_to_same_season_median_ratio"))
        if same_season_ratio is not None and same_season_ratio >= 2:
            historical_score += min(5.0, same_season_ratio)
        quality_penalty = (1.0 - quality_score) * 10.0
        return int(round(max(0.0, min(100.0, burden + mortality + change + anomaly_score + historical_score - quality_penalty))))

    @staticmethod
    def _attention_level(score: int) -> str:
        if score >= 80:
            return "critical"
        if score >= 60:
            return "high"
        if score >= 35:
            return "moderate"
        return "low"

    @staticmethod
    def _attention_drivers(
        latest_cases: int,
        latest_deaths: int,
        change_pct: Optional[float],
        anomaly: Dict[str, Any],
        quality_score: float,
        historical_context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        drivers: List[str] = []
        if latest_cases > 0:
            drivers.append("latest_cases_present")
        if latest_deaths > 0:
            drivers.append("latest_deaths_present")
        if change_pct is None:
            drivers.append("new_or_reappearing_signal")
        elif change_pct >= 25:
            drivers.append("recent_increase")
        if anomaly.get("is_anomaly"):
            drivers.append("statistical_anomaly")
        historical_context = historical_context or {}
        historical_percentile = _num(historical_context.get("latest_percentile_prior"))
        if historical_percentile is not None and historical_percentile >= 90:
            drivers.append("high_historical_percentile")
        long_window_change = _num(historical_context.get("long_window_change_pct"))
        if long_window_change is not None and long_window_change >= 25:
            drivers.append("long_window_increase")
        if quality_score < 0.75:
            drivers.append("lower_data_confidence")
        return drivers or ["low_current_signal"]

    @staticmethod
    def _limitations(
        group: pd.DataFrame,
        missing_rates: Dict[str, float],
        incidence_sources: Optional[Dict[str, int]] = None,
        death_reporting: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        limitations: List[str] = []
        if len(group) < 3:
            limitations.append("Short time series limits trend confidence.")
        if missing_rates.get("incidence_rate", 0) > 0:
            limitations.append("Incidence rates are missing for some records.")
        incidence_sources = incidence_sources or {}
        if incidence_sources.get("wpp_computed_crude"):
            limitations.append(
                "Crude incidence rates were computed from WPP annual population denominators and are not source-provided rates."
            )
        data_source_text = " ".join(
            str(value).lower()
            for value in group.get("data_source", pd.Series(dtype=str)).dropna().unique()
        )
        if incidence_sources.get("wpp_computed_crude") and "sentinel" in data_source_text:
            limitations.append(
                "The source is sentinel surveillance; crude population incidence is contextual and should not be read as an official sentinel per-site rate."
            )
        death_reporting = death_reporting or {}
        death_status = death_reporting.get("status")
        if death_status == "unavailable":
            limitations.append("Death counts are not source-reported; zero should not be interpreted as no deaths.")
        elif death_status == "partial_reported_zero":
            limitations.append("Some death records are missing; reported death values are zero where available.")
        elif death_status == "reported_zero":
            limitations.append("Death values are source-reported as zero in the available records.")
        elif missing_rates.get("deaths", 0) > 0:
            limitations.append("Death values are missing for some records.")
        if "data_source" in group.columns and group["data_source"].nunique() > 1:
            limitations.append("Multiple source labels are present; reporting definitions may differ.")
        return limitations or ["No major structural limitation detected in the available time series."]

    @staticmethod
    def _attention_ranking(disease_packets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "disease_id": item["disease_id"],
                "name_en": item["name_en"],
                "name_zh": item["name_zh"],
                "attention_score": item["attention"]["score"],
                "attention_level": item["attention"]["level"],
                "semantic_type": "surveillance_attention_priority",
                # Deprecated aliases retained for stored report compatibility.
                "risk_score": item["attention"]["score"],
                "risk_level": item["attention"]["level"],
                "latest_cases": item["metrics"]["latest_cases"],
                "latest_deaths": item["metrics"].get("latest_deaths"),
                "change_pct": item["metrics"]["change_pct"],
                "evidence_refs": [
                    f"disease:{item['disease_id']}.attention_score",
                    f"disease:{item['disease_id']}.latest_cases",
                    f"disease:{item['disease_id']}.change_pct",
                    f"disease:{item['disease_id']}.latest_incidence_rate_per_100k",
                    f"disease:{item['disease_id']}.latest_percentile_prior",
                    f"disease:{item['disease_id']}.long_window_change_pct",
                ],
            }
            for item in sorted(disease_packets, key=lambda packet: packet["attention"]["score"], reverse=True)
        ]


class ReportFactChecker:
    """Rule-based checks for v3 report sections."""

    DATE_RE = re.compile(r"\b(20\d{2})(?:-(0[1-9]|1[0-2]))?(?:-([0-2]\d|3[01]))?\b")
    NUMBER_RE = re.compile(
        r"(?<![A-Za-z0-9])[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
        r"|(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?%?"
    )
    CAUSAL_RE = re.compile(r"\b(caused by|due to|driven by|resulted from|attributable to)\b|由于|导致|归因于", re.I)
    CAUTION_RE = re.compile(
        r"\b(may|might|could|hypothesis|requires|should be interpreted|uncertain|not establish|unavailable|not reported)\b"
        r"|不能|可能|需|不等同|不应|未提供",
        re.I,
    )
    ZERO_DEATH_RE = re.compile(r"\b(?:0|zero|no)\s+(?:reported\s+)?deaths?\b|零死亡|0\s*死亡|未记录死亡|无死亡", re.I)

    def check_report(
        self,
        *,
        sections: List[Dict[str, Any]],
        evidence_packet: Dict[str, Any],
        quality_threshold: float = 0.85,
        deep_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        issues: List[Dict[str, Any]] = []
        for section in sections:
            issues.extend(self.check_section(section, evidence_packet))

        critical = [i for i in issues if i.get("severity") == "critical"]
        major = [i for i in issues if i.get("severity") == "major"]
        evidence_quality = float(evidence_packet.get("data_quality", {}).get("score") or 0.0)
        fact_score = max(0.0, 1.0 - len(critical) * 0.4 - len(major) * 0.18 - (len(issues) - len(critical) - len(major)) * 0.05)
        confidence = 0.85 if deep_confidence is None else max(0.0, min(1.0, deep_confidence))
        overall = round(0.45 * evidence_quality + 0.35 * fact_score + 0.2 * confidence, 3)
        passed = bool(overall >= quality_threshold and not critical)
        return {
            "passed": passed,
            "threshold": quality_threshold,
            "overall_score": overall,
            "evidence_quality_score": evidence_quality,
            "fact_score": round(fact_score, 3),
            "deep_analysis_confidence": round(confidence, 3),
            "issues": issues[:50],
            "critical_count": len(critical),
            "major_count": len(major),
        }

    def check_section(self, section: Dict[str, Any], evidence_packet: Dict[str, Any]) -> List[Dict[str, Any]]:
        content = str(section.get("content") or "")
        metadata = section.get("metadata") or {}
        title = str(section.get("title") or section.get("section_type") or "section")
        issues: List[Dict[str, Any]] = []

        refs = metadata.get("evidence_refs") or []
        if not refs:
            issues.append(
                {
                    "severity": "major",
                    "section": title,
                    "code": "missing_evidence_refs",
                    "message": "Section has no evidence references.",
                }
            )

        period_end = self._parse_period_end(evidence_packet)
        for match in self.DATE_RE.finditer(content):
            date_text = match.group(0)
            parsed = self._parse_date(date_text)
            if parsed is not None and period_end is not None and parsed > period_end:
                issues.append(
                    {
                        "severity": "critical",
                        "section": title,
                        "code": "date_after_report_period",
                        "message": f"Date {date_text} is after report period end {period_end.date().isoformat()}.",
                    }
                )

        allowed_numbers = self._allowed_numbers(evidence_packet)
        content_for_numbers = re.sub(r"`[^`]*`", "", content)
        for raw in self.NUMBER_RE.findall(content_for_numbers):
            text = raw[0] if isinstance(raw, tuple) else raw
            normalized = str(text).replace(",", "").replace("%", "")
            try:
                value = float(normalized)
            except ValueError:
                continue
            if self._ignore_number(value, str(text)):
                continue
            if not self._matches_allowed(value, allowed_numbers):
                issues.append(
                    {
                        "severity": "major",
                        "section": title,
                        "code": "number_not_in_evidence",
                        "message": f"Number {text} is not present in the evidence packet.",
                    }
                )
                if len([i for i in issues if i.get("code") == "number_not_in_evidence"]) >= 8:
                    break

        if self.CAUSAL_RE.search(content) and not self.CAUTION_RE.search(content):
            issues.append(
                {
                    "severity": "major",
                    "section": title,
                    "code": "unsupported_causal_language",
                    "message": "Strong causal language appears without uncertainty or corroboration framing.",
                }
            )

        if self._death_counts_unavailable(evidence_packet) and self.ZERO_DEATH_RE.search(content) and not self.CAUTION_RE.search(content):
            issues.append(
                {
                    "severity": "major",
                    "section": title,
                    "code": "zero_deaths_from_unavailable_field",
                    "message": "Content describes zero deaths even though death counts are unavailable in the evidence packet.",
                }
            )

        return issues

    @staticmethod
    def _parse_period_end(packet: Dict[str, Any]) -> Optional[pd.Timestamp]:
        raw = (packet.get("period") or {}).get("end")
        if not raw:
            return None
        try:
            return pd.Timestamp(raw).tz_localize(None)
        except Exception:
            return None

    @staticmethod
    def _parse_date(text: str) -> Optional[pd.Timestamp]:
        try:
            parts = text.split("-")
            if len(parts) == 1:
                return None
            if len(parts) == 2:
                return pd.Timestamp(f"{text}-28")
            return pd.Timestamp(text)
        except Exception:
            return None

    @staticmethod
    def _allowed_numbers(packet: Dict[str, Any]) -> List[float]:
        numbers: List[float] = []

        def walk(value: Any) -> None:
            if isinstance(value, bool):
                return
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isnan(float(value)) and not math.isinf(float(value)):
                    numbers.append(float(value))
                    if 0.0 <= float(value) <= 1.0:
                        numbers.append(round(float(value) * 100.0, 2))
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(packet.get("summary_metrics") or {})
        walk(packet.get("attention_ranking") or packet.get("risk_ranking") or [])
        walk(packet.get("diseases") or [])
        walk(packet.get("data_quality") or {})
        return numbers

    @staticmethod
    def _death_counts_unavailable(packet: Dict[str, Any]) -> bool:
        summary_death_reporting = ((packet.get("summary_metrics") or {}).get("death_reporting") or {})
        if summary_death_reporting.get("status") == "unavailable":
            return True
        diseases = packet.get("diseases") or []
        return bool(diseases) and all(
            ((item.get("metrics") or {}).get("death_reporting_status") == "unavailable")
            for item in diseases
        )

    @staticmethod
    def _ignore_number(value: float, raw_text: str) -> bool:
        if 1900 <= value <= 2100 and "." not in raw_text and "%" not in raw_text:
            return True
        if value in {0, 1, 2, 3, 4, 5, 10, 20, 25, 35, 60, 80, 100, 100000}:
            return True
        return value < 100 and "," not in raw_text and "%" not in raw_text

    @staticmethod
    def _matches_allowed(value: float, allowed: Iterable[float]) -> bool:
        for candidate in allowed:
            tolerance = max(0.51, abs(candidate) * 0.01)
            if abs(value - candidate) <= tolerance:
                return True
        return False
