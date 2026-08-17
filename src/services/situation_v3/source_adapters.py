"""Database input adapter for Situation Room v3.

Cheap eligibility checks run in PostgreSQL so the model only receives fresh
series with enough, bounded history. Rejected identities still enter the
immutable analysis ledger without transferring every observation.
"""

from __future__ import annotations

import re
import math
from collections import defaultdict
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text

from src.core.database import get_db


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _clean_source_url(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    candidate = str(value).strip()
    if candidate.lower() in {"", "nan", "none", "null", "nat"}:
        return None
    return candidate if candidate.startswith(("https://", "http://")) else None


def _source_evidence_url(
    value: Any,
    source_system: Any,
    configured_urls: dict[str, Any],
) -> str | None:
    return _clean_source_url(value) or _clean_source_url(
        configured_urls.get(str(source_system or ""))
    )


def _metric_literals(config: dict[str, Any]) -> str:
    policy = config.get("metric_policy", {})
    metrics = sorted(
        set(
            list(policy.get("activity_metrics") or ["case_notifications"])
            + list(policy.get("severity_metrics") or ["hospitalized_case_notifications"])
        )
    )
    return ", ".join("'" + value.replace("'", "''") + "'" for value in metrics)


def _eligible_cte(metric_literals: str, *, include_grouped: bool = True) -> str:
    base = fr"""
        WITH eligible AS (
            SELECT s.series_code, s.disease_id, s.country_code,
                   s.source_system, s.source_label, s.metric_type,
                   s.reporting_basis, s.temporal_granularity, s.unit,
                   s.aggregation_policy, s.missing_value_policy,
                   s.metadata AS series_metadata,
                   d.standard_name_en AS disease_name,
                   c.name_en AS country_name
              FROM disease_surveillance_series s
              LEFT JOIN standard_diseases d ON d.disease_id = s.disease_id
              LEFT JOIN countries c ON c.code = s.country_code
             WHERE s.is_active = true
               AND s.disease_id IS NOT NULL
               AND s.metric_type IN ({metric_literals})
               AND s.mapping_relation = 'exact'
               AND s.aggregation_policy IN ('non_additive', 'direct_only', 'reported_aggregate')
               AND s.temporal_granularity IN ('daily', 'weekly', 'monthly')
               AND s.missing_value_policy <> 'missing_is_zero'
        ), normalized AS (
            SELECT o.id, o.updated_at, o.time, o.value, o.quality_status, o.geography_key,
                   CASE
                     WHEN e.country_code IS NOT NULL AND o.geography_key IN (
                       'national',
                       'country\:' || upper(e.country_code) || '\:national',
                       'source\:' || e.source_system || '\:reporting-area\:total'
                     ) THEN 'country\:' || upper(e.country_code) || '\:national'
                     ELSE o.geography_key
                   END AS canonical_geography_key,
                   o.dimension_key, o.dimensions,
                   CASE WHEN lower(e.unit) <> 'count'
                        THEN COALESCE(o.raw_data ->> 'numerator', o.dimensions ->> 'numerator')
                   END AS numerator,
                   CASE WHEN lower(e.unit) <> 'count'
                        THEN COALESCE(o.raw_data ->> 'denominator', o.dimensions ->> 'denominator')
                   END AS denominator,
                   COALESCE(
                     NULLIF(o.raw_data ->> 'source_url', ''),
                     NULLIF(o.raw_data ->> 'SourceURL', ''),
                     NULLIF(o.metadata ->> 'source_url', ''),
                     NULLIF(e.series_metadata ->> 'source_url', ''),
                     NULLIF(e.series_metadata ->> 'source_uri', '')
                   ) AS source_url,
                   e.*
              FROM disease_series_observations o
              JOIN eligible e ON e.series_code = o.series_code
             WHERE o.suppressed = false
               AND o.quality_status <> 'rejected'
        )
    """
    if not include_grouped:
        return base
    return base + """
        , grouped AS (
            SELECT series_code, canonical_geography_key, dimension_key,
                   disease_id, disease_name, country_code, country_name,
                   source_system, source_label, metric_type, unit,
                   temporal_granularity,
                   count(DISTINCT time) AS observation_count,
                   min(time) AS earliest_time,
                   max(time) AS latest_time,
                   array_agg(DISTINCT geography_key ORDER BY geography_key) AS source_geography_keys
              FROM normalized
             GROUP BY series_code, canonical_geography_key, dimension_key,
                      disease_id, disease_name, country_code, country_name,
                      source_system, source_label, metric_type, unit,
                      temporal_granularity
        )
    """


async def fetch_series_inputs_v3(
    config: dict[str, Any],
    *,
    as_of: date,
) -> tuple[pd.DataFrame, list[dict[str, Any]], int]:
    """Return bounded eligible history, SQL-stage rejection ledger, registry count."""

    literals = _metric_literals(config)
    if not literals:
        return pd.DataFrame(), [], 0
    cte = _eligible_cte(literals)
    thresholds = config.get("thresholds", {}).get("minimum_observations", {})
    cadences = config.get("cadences", {})
    latency = config.get("data_latency", {})
    maturity = latency.get("minimum_maturity_days", {})
    params = {
        "as_of": as_of,
        "daily_min": int(thresholds.get("daily", 730)),
        "weekly_min": int(thresholds.get("weekly", 156)),
        "monthly_min": int(thresholds.get("monthly", 36)),
        "daily_fresh": int(cadences.get("daily", {}).get("freshness_days", 14)),
        "weekly_fresh": int(cadences.get("weekly", {}).get("freshness_days", 35)),
        "monthly_fresh": int(cadences.get("monthly", {}).get("freshness_days", 75)),
        "daily_maturity": int(maturity.get("daily", 2)),
        "weekly_maturity": int(maturity.get("weekly", 7)),
        "monthly_maturity": int(maturity.get("monthly", 21)),
        "watermark_quantile": max(
            0.0,
            min(1.0, 1.0 - float(latency.get("minimum_source_period_coverage", 0.8))),
        ),
        "history_years": int(config.get("quality", {}).get("maximum_seasons", 5)),
    }
    stats_query = text(
        cte
        + """
        SELECT *,
               CASE temporal_granularity
                 WHEN 'daily' THEN CAST(:daily_min AS INTEGER)
                 WHEN 'weekly' THEN CAST(:weekly_min AS INTEGER)
                 ELSE CAST(:monthly_min AS INTEGER)
               END AS required_observations,
               CASE temporal_granularity
                 WHEN 'daily' THEN CAST(:daily_fresh AS INTEGER)
                 WHEN 'weekly' THEN CAST(:weekly_fresh AS INTEGER)
                 ELSE CAST(:monthly_fresh AS INTEGER)
               END AS freshness_days,
               CASE temporal_granularity
                 WHEN 'monthly' THEN CAST(:as_of AS date) -
                   (date_trunc('month', latest_time) + interval '1 month - 1 day')::date
                 ELSE CAST(:as_of AS date) - latest_time::date
               END AS period_age_days
          FROM grouped
         ORDER BY series_code, canonical_geography_key, dimension_key
        """
    )
    async with get_db() as db:
        stats = [dict(row) for row in (await db.execute(stats_query, params)).mappings()]
        active_by_source: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        totals: dict[tuple[str, str], int] = defaultdict(int)
        for row in stats:
            key = (str(row["source_system"]), str(row["temporal_granularity"]))
            totals[key] += 1
            if (
                int(row["observation_count"]) >= int(row["required_observations"])
                and int(row["period_age_days"]) <= int(row["freshness_days"])
            ):
                active_by_source[key].append(row)

        qualified: list[dict[str, Any]] = []
        quantile = float(params["watermark_quantile"])
        maturity_days = {
            "daily": int(params["daily_maturity"]),
            "weekly": int(params["weekly_maturity"]),
            "monthly": int(params["monthly_maturity"]),
        }
        for key, active_rows in sorted(active_by_source.items()):
            cadence = key[1]
            ordered = sorted(pd.Timestamp(row["latest_time"]) for row in active_rows)
            watermark_index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
            watermark = ordered[watermark_index]
            if watermark.tzinfo is None:
                watermark = watermark.tz_localize("UTC")
            else:
                watermark = watermark.tz_convert("UTC")
            coverage = sum(pd.Timestamp(row["latest_time"]) >= watermark for row in active_rows) / len(active_rows)
            if cadence == "monthly":
                shifted = pd.Timestamp(as_of) - pd.Timedelta(days=maturity_days[cadence])
                mature_cutoff = (shifted.to_period("M") - 1).start_time.tz_localize("UTC")
            else:
                mature_cutoff = pd.Timestamp(as_of, tz="UTC") - pd.Timedelta(days=maturity_days[cadence])
            analysis_cutoff = min(watermark, mature_cutoff)
            for row in active_rows:
                latest = pd.Timestamp(row["latest_time"])
                if latest.tzinfo is None:
                    latest = latest.tz_localize("UTC")
                else:
                    latest = latest.tz_convert("UTC")
                qualified.append(
                    {
                        "series_code": row["series_code"],
                        "canonical_geography_key": row["canonical_geography_key"],
                        "dimension_key": row["dimension_key"],
                        "latest_available_time": latest.to_pydatetime(),
                        "source_watermark": watermark.to_pydatetime(),
                        "analysis_cutoff": analysis_cutoff.to_pydatetime(),
                        "source_period_coverage": coverage,
                        "source_active_identities": len(active_rows),
                        "source_total_identities": totals[key],
                        "reporting_lag_days": max(0, int(row["period_age_days"])),
                    }
                )

        rows: list[dict[str, Any]] = []
        if qualified:
            value_rows = []
            data_params = dict(params)
            for index, row in enumerate(qualified):
                value_rows.append(
                    "(" + ", ".join(
                        [
                            f"CAST(:q_series_{index} AS VARCHAR)",
                            f"CAST(:q_geo_{index} AS VARCHAR)",
                            f"CAST(:q_dimension_{index} AS VARCHAR)",
                            f"CAST(:q_latest_{index} AS TIMESTAMPTZ)",
                            f"CAST(:q_watermark_{index} AS TIMESTAMPTZ)",
                            f"CAST(:q_cutoff_{index} AS TIMESTAMPTZ)",
                            f"CAST(:q_coverage_{index} AS DOUBLE PRECISION)",
                            f"CAST(:q_active_{index} AS INTEGER)",
                            f"CAST(:q_total_{index} AS INTEGER)",
                            f"CAST(:q_lag_{index} AS INTEGER)",
                        ]
                    ) + ")"
                )
                for name, value in (
                    ("series", row["series_code"]),
                    ("geo", row["canonical_geography_key"]),
                    ("dimension", row["dimension_key"]),
                    ("latest", row["latest_available_time"]),
                    ("watermark", row["source_watermark"]),
                    ("cutoff", row["analysis_cutoff"]),
                    ("coverage", row["source_period_coverage"]),
                    ("active", row["source_active_identities"]),
                    ("total", row["source_total_identities"]),
                    ("lag", row["reporting_lag_days"]),
                ):
                    data_params[f"q_{name}_{index}"] = value
            data_query = text(
                _eligible_cte(literals, include_grouped=False)
                + ", qualified (series_code, canonical_geography_key, dimension_key, "
                "latest_available_time, source_watermark, analysis_cutoff, "
                "source_period_coverage, source_active_identities, "
                "source_total_identities, reporting_lag_days) AS (VALUES "
                + ", ".join(value_rows)
                + """
                )
                SELECT n.id, n.updated_at, n.time, n.value, n.quality_status, n.geography_key,
                       n.canonical_geography_key, n.dimension_key, n.dimensions,
                       n.numerator, n.denominator, n.series_code, n.disease_id,
                       n.country_code, n.source_system, n.source_label,
                       n.metric_type, n.reporting_basis, n.temporal_granularity,
                       n.unit, n.aggregation_policy, n.missing_value_policy,
                       n.series_metadata, n.source_url, n.disease_name, n.country_name,
                       q.latest_available_time, q.source_watermark,
                       q.analysis_cutoff, q.source_period_coverage,
                       q.source_active_identities, q.source_total_identities,
                       q.reporting_lag_days
                  FROM normalized n
                  JOIN qualified q
                    ON q.series_code = n.series_code
                   AND q.canonical_geography_key = n.canonical_geography_key
                   AND q.dimension_key IS NOT DISTINCT FROM n.dimension_key
                 WHERE n.time >= q.latest_available_time -
                       make_interval(years => CAST(:history_years AS INTEGER))
                   AND n.time <= q.analysis_cutoff
                 ORDER BY n.series_code, n.canonical_geography_key,
                          n.dimension_key, n.time, n.updated_at, n.id
                """
            )
            rows = [
                dict(row)
                for row in (await db.execute(data_query, data_params)).mappings()
            ]
    ledger: list[dict[str, Any]] = []
    for row in stats:
        minimum = int(row.pop("required_observations"))
        freshness = int(row.pop("freshness_days"))
        latest = pd.Timestamp(row["latest_time"]).date()
        age_days = int(row.pop("period_age_days"))
        reason = None
        if int(row["observation_count"]) < minimum:
            reason = "insufficient_observations"
        elif age_days > freshness:
            reason = "stale"
        if reason:
            ledger.append(
                {
                    "series_code": row["series_code"],
                    "canonical_geography_key": row["canonical_geography_key"],
                    "source_geography_keys": list(row.get("source_geography_keys") or []),
                    "dimension_key": row["dimension_key"],
                    "disease_id": row["disease_id"],
                    "disease_name": row["disease_name"] or row["disease_id"],
                    "country_code": row["country_code"],
                    "country_name": row["country_name"],
                    "source_system": row["source_system"],
                    "metric_type": row["metric_type"],
                    "unit": row["unit"],
                    "cadence": row["temporal_granularity"],
                    "observation_count": int(row["observation_count"]),
                    "data_through": latest.isoformat(),
                    "latest_available_period": latest.isoformat(),
                    "reporting_lag_days": max(0, age_days),
                    "status": "rejected",
                    "rejection_reason": reason,
                }
            )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["numerator"] = pd.to_numeric(frame["numerator"], errors="coerce")
        frame["denominator"] = pd.to_numeric(frame["denominator"], errors="coerce")
        frame["disease_slug"] = frame["disease_name"].fillna(frame["disease_id"]).map(_slug)
        configured_urls = config.get("quality", {}).get("source_evidence_urls", {})
        frame["source_url"] = [
            _source_evidence_url(value, source, configured_urls)
            for value, source in zip(
                frame["source_url"],
                frame["source_system"],
                strict=True,
            )
        ]
    registered = len({str(row["series_code"]) for row in stats})
    return frame, ledger, registered


__all__ = ["fetch_series_inputs_v3"]
