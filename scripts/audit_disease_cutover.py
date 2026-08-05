#!/usr/bin/env python3
"""Audit per-concept readiness for strict disease source-series reads."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any, Iterable

from sqlalchemy import case, func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.api.services.disease_series_projection import (  # noqa: E402
    load_series_first_records,
)
from src.core import get_database  # noqa: E402
from src.core.disease_cutover import get_disease_cutover_config  # noqa: E402
from src.domain.country import Country  # noqa: E402
from src.domain.disease_ontology import (  # noqa: E402
    DiseaseSeriesObservation,
    DiseaseSourceAvailability,
    DiseaseSurveillanceSeries,
)
from src.services.disease_series_policy import (  # noqa: E402
    SERIES_CASE_COUNT_METRICS,
)

AUTO_APPROVED_PROJECTION_POLICIES = frozenset({"single_series", "sum_disjoint"})


async def audit_cutover(
    *,
    country_code: str | None = None,
    concept_id: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-ready readiness report without mutating either data layer."""

    country_filter = country_code.strip().upper() if country_code else None
    concept_filter = concept_id.strip().upper() if concept_id else None
    config = get_disease_cutover_config()

    async with get_database() as db:
        pairs_query = (
            select(
                DiseaseSurveillanceSeries.country_code,
                DiseaseSurveillanceSeries.disease_id,
            )
            .where(
                DiseaseSurveillanceSeries.disease_id.is_not(None),
                DiseaseSurveillanceSeries.metric_type.in_(SERIES_CASE_COUNT_METRICS),
                DiseaseSurveillanceSeries.unit == "count",
                DiseaseSurveillanceSeries.is_active.is_(True),
            )
            .distinct()
        )
        if country_filter:
            pairs_query = pairs_query.where(
                DiseaseSurveillanceSeries.country_code == country_filter
            )
        if concept_filter:
            pairs_query = pairs_query.where(
                DiseaseSurveillanceSeries.disease_id == concept_filter
            )
        pairs = {
            (str(row.country_code), str(row.disease_id))
            for row in (await db.execute(pairs_query)).all()
        }
        for policy in config.configured_read_policies():
            if country_filter and policy.country_code != country_filter:
                continue
            if concept_filter and policy.concept_id != concept_filter:
                continue
            pairs.add((policy.country_code, policy.concept_id))

        countries = {
            str(row.code): int(row.id)
            for row in (
                await db.execute(
                    select(Country.code, Country.id).where(
                        Country.code.in_({country for country, _ in pairs})
                    )
                )
            ).all()
        }

        targets: list[dict[str, Any]] = []
        for country, concept in sorted(pairs):
            country_id = countries.get(country)
            policy = config.resolve_read_policy(country, concept)
            if country_id is None:
                targets.append(
                    {
                        "country_code": country,
                        "concept_id": concept,
                        "configured_read_mode": policy.read_mode,
                        "ready_for_series_only": False,
                        "blocking_reasons": ["country_not_found"],
                    }
                )
                continue

            result = await load_series_first_records(
                db,
                disease_code=concept,
                country_id=country_id,
                read_mode="series_with_fallback",
                shadow_compare=True,
            )
            metadata = result.metadata
            source_series = metadata.get("source_series") or []
            selected_codes = tuple(metadata.get("selected_series_codes") or [])
            required_codes = tuple(policy.required_series or selected_codes)
            series_stats = await _load_series_stats(
                db,
                country_code=country,
                series_codes=required_codes,
            )
            availability = await _load_availability(db, required_codes)
            shadow = (metadata.get("cutover") or {}).get("shadow") or {}
            coverage = metadata.get("coverage") or {}
            projection_policy = metadata.get(
                "registry_projection_policy"
            ) or metadata.get("projection_policy")
            reasons = _blocking_reasons(
                policy=policy,
                projection_policy=projection_policy,
                source_series=source_series,
                required_codes=required_codes,
                series_stats=series_stats,
                availability=availability,
                coverage=coverage,
                shadow=shadow,
            )
            targets.append(
                {
                    "country_code": country,
                    "concept_id": concept,
                    "configured_read_mode": policy.read_mode,
                    "target_override": policy.target_override,
                    "ready_for_series_only": not reasons,
                    "blocking_reasons": reasons,
                    "projection_policy": projection_policy,
                    "selected_series": list(selected_codes),
                    "required_series": list(required_codes),
                    "coverage": coverage,
                    "shadow": shadow,
                    "series_stats": series_stats,
                    "availability": availability,
                }
            )

    configured_strict = [
        target
        for target in targets
        if target.get("configured_read_mode") == "series_only"
    ]
    configured_strict_failures = [
        target for target in configured_strict if not target["ready_for_series_only"]
    ]
    return {
        "schema_version": 1,
        "cutover_release_version": config.release_version,
        "filters": {
            "country_code": country_filter,
            "concept_id": concept_filter,
        },
        "summary": {
            "target_count": len(targets),
            "ready_count": sum(target["ready_for_series_only"] for target in targets),
            "blocked_count": sum(
                not target["ready_for_series_only"] for target in targets
            ),
            "configured_series_only_count": len(configured_strict),
            "configured_series_only_failure_count": len(configured_strict_failures),
        },
        "targets": targets,
    }


async def _load_series_stats(
    db,
    *,
    country_code: str,
    series_codes: Iterable[str],
) -> dict[str, dict[str, Any]]:
    codes = tuple(sorted({str(code) for code in series_codes if code}))
    if not codes:
        return {}
    rows = (
        await db.execute(
            select(
                DiseaseSurveillanceSeries.series_code,
                func.count(DiseaseSeriesObservation.id).label("fact_count"),
                func.min(DiseaseSeriesObservation.time).label("coverage_start"),
                func.max(DiseaseSeriesObservation.time).label("coverage_end"),
                func.sum(
                    case((DiseaseSeriesObservation.suppressed.is_(True), 1), else_=0)
                ).label("suppressed_count"),
                func.sum(
                    case(
                        (DiseaseSeriesObservation.quality_status == "rejected", 1),
                        else_=0,
                    )
                ).label("rejected_count"),
                func.sum(
                    case(
                        (
                            DiseaseSeriesObservation.unit
                            != DiseaseSurveillanceSeries.unit,
                            1,
                        ),
                        else_=0,
                    )
                ).label("unit_mismatch_count"),
            )
            .select_from(DiseaseSurveillanceSeries)
            .outerjoin(
                DiseaseSeriesObservation,
                (
                    DiseaseSeriesObservation.series_code
                    == DiseaseSurveillanceSeries.series_code
                )
                & (
                    DiseaseSeriesObservation.geography_key
                    == f"country:{country_code}:national"
                )
                & (DiseaseSeriesObservation.dimension_key == "all"),
            )
            .where(DiseaseSurveillanceSeries.series_code.in_(codes))
            .group_by(DiseaseSurveillanceSeries.series_code)
        )
    ).all()
    return {
        str(row.series_code): {
            "fact_count": int(row.fact_count or 0),
            "coverage_start": (
                row.coverage_start.date().isoformat() if row.coverage_start else None
            ),
            "coverage_end": (
                row.coverage_end.date().isoformat() if row.coverage_end else None
            ),
            "suppressed_count": int(row.suppressed_count or 0),
            "rejected_count": int(row.rejected_count or 0),
            "unit_mismatch_count": int(row.unit_mismatch_count or 0),
        }
        for row in rows
    }


async def _load_availability(db, series_codes: Iterable[str]) -> dict[str, list[str]]:
    codes = tuple(sorted({str(code) for code in series_codes if code}))
    if not codes:
        return {}
    rows = (
        await db.execute(
            select(
                DiseaseSourceAvailability.series_code,
                DiseaseSourceAvailability.status,
            ).where(
                DiseaseSourceAvailability.series_code.in_(codes),
                DiseaseSourceAvailability.is_active.is_(True),
            )
        )
    ).all()
    result: dict[str, list[str]] = {code: [] for code in codes}
    for row in rows:
        if row.series_code:
            result[str(row.series_code)].append(str(row.status))
    return {code: sorted(set(statuses)) for code, statuses in result.items()}


def _blocking_reasons(
    *,
    policy,
    projection_policy: str | None,
    source_series: list[dict[str, Any]],
    required_codes: tuple[str, ...],
    series_stats: dict[str, dict[str, Any]],
    availability: dict[str, list[str]],
    coverage: dict[str, Any],
    shadow: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if not source_series or not required_codes:
        reasons.append("no_required_case_count_series")
    allowed_policy = policy.allowed_projection_policy
    if allowed_policy:
        if projection_policy != allowed_policy:
            reasons.append(
                "projection_policy_mismatch:"
                f"expected={allowed_policy},actual={projection_policy}"
            )
    elif projection_policy not in AUTO_APPROVED_PROJECTION_POLICIES:
        reasons.append(f"projection_policy_requires_approval:{projection_policy}")

    for code in required_codes:
        stats = series_stats.get(code)
        if not stats or stats["fact_count"] < 2:
            reasons.append(f"required_series_has_fewer_than_two_periods:{code}")
            continue
        if stats["suppressed_count"]:
            reasons.append(f"suppressed_observations:{code}")
        if stats["rejected_count"]:
            reasons.append(f"rejected_observations:{code}")
        if stats["unit_mismatch_count"]:
            reasons.append(f"unit_mismatch:{code}")
        statuses = availability.get(code) or []
        if "available" not in statuses:
            reasons.append(f"availability_not_explicitly_available:{code}")

    if coverage.get("status") != "parity":
        reasons.append(f"coverage_not_parity:{coverage.get('status')}")
    if float(coverage.get("coverage_ratio_against_legacy") or 0) < 1.0:
        reasons.append("legacy_period_coverage_below_100_percent")
    if int(shadow.get("legacy_only_period_count") or 0):
        reasons.append("registry_tail_or_history_gap")
    if int(shadow.get("value_difference_count") or 0):
        reasons.append("unapproved_shadow_value_differences")
    return sorted(set(reasons))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", help="Limit to one country code")
    parser.add_argument("--concept", help="Limit to one canonical D-code")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if a configured series_only target does not pass every gate",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete JSON report instead of the compact summary",
    )
    return parser


def _print_text(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(
        "Disease cutover audit | "
        f"release={report['cutover_release_version']} "
        f"targets={summary['target_count']} ready={summary['ready_count']} "
        f"blocked={summary['blocked_count']} "
        "configured_series_only_failures="
        f"{summary['configured_series_only_failure_count']}"
    )
    for target in report["targets"]:
        marker = "READY" if target["ready_for_series_only"] else "BLOCKED"
        reasons = "; ".join(target.get("blocking_reasons") or []) or "none"
        print(
            f"  {marker} {target['country_code']}/{target['concept_id']} "
            f"mode={target['configured_read_mode']} reasons={reasons}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(
        audit_cutover(country_code=args.country, concept_id=args.concept)
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        _print_text(report)
    if args.strict and report["summary"]["configured_series_only_failure_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
