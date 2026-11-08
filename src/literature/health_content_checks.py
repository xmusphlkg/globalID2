"""Article coverage and published-release health checks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from .classification import CLASSIFICATION_VERSION
from .health_check_utils import _blocker_categories, _check
from .health_snapshot import (
    HealthThresholds,
    ResearchRadarSnapshot,
    _age_hours,
    _safe_int,
)
from .release_validation import validate_public_research_payload
from .weekly_ai_review import project_weekly_ai_review
from .weekly_briefs import project_weekly_editorial_review


def _article_checks(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds,
) -> list[dict[str, Any]]:
    metrics = snapshot.article_metrics
    total = (
        _safe_int(metrics.get("article_count"))
        if isinstance(metrics, Mapping)
        else len(snapshot.articles)
    )
    current = (
        _safe_int(metrics.get("classification_current_count"))
        if isinstance(metrics, Mapping)
        else sum(
            _safe_int((row.get("metadata_") or {}).get("classification_version"))
            >= CLASSIFICATION_VERSION
            for row in snapshot.articles
        )
    )
    current_ratio = current / total if total else 0.0
    if isinstance(metrics, Mapping):
        denominator = _safe_int(metrics.get("doi_article_count"))
        openalex = _safe_int(metrics.get("openalex_count"))
        unpaywall = _safe_int(metrics.get("unpaywall_count"))
    else:
        doi_rows = [row for row in snapshot.articles if str(row.get("doi") or "").strip()]
        openalex = sum(bool(row.get("openalex_id")) for row in doi_rows)
        unpaywall = sum(
            isinstance(row.get("source_payload"), Mapping)
            and isinstance(row.get("source_payload", {}).get("unpaywall"), Mapping)
            for row in doi_rows
        )
        denominator = len(doi_rows)
    openalex_ratio = openalex / denominator if denominator else 0.0
    unpaywall_ratio = unpaywall / denominator if denominator else 0.0
    return [
        _check(
            "classification_version",
            "pass" if total and current_ratio >= thresholds.min_classification_current_ratio else "critical",
            {
                "article_count": total,
                "current_count": current,
                "stale_count": total - current,
                "current_ratio": round(current_ratio, 6),
                "required_version": CLASSIFICATION_VERSION,
            },
            {"min_current_ratio": thresholds.min_classification_current_ratio},
        ),
        _check(
            "metadata_provider_coverage",
            "pass" if (
                denominator
                and openalex_ratio >= thresholds.min_openalex_coverage
                and unpaywall_ratio >= thresholds.min_unpaywall_coverage
            ) else "critical",
            {
                "doi_article_count": denominator,
                "openalex_count": openalex,
                "openalex_ratio": round(openalex_ratio, 6),
                "unpaywall_count": unpaywall,
                "unpaywall_ratio": round(unpaywall_ratio, 6),
            },
            {
                "min_openalex_coverage": thresholds.min_openalex_coverage,
                "min_unpaywall_coverage": thresholds.min_unpaywall_coverage,
            },
            next_action_code=(
                "none"
                if denominator
                and openalex_ratio >= thresholds.min_openalex_coverage
                and unpaywall_ratio >= thresholds.min_unpaywall_coverage
                else "run_metadata_backfill_dry_run"
            ),
        ),
    ]


def _release_checks(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds,
) -> list[dict[str, Any]]:
    payload = snapshot.release_payload
    metrics = snapshot.release_metrics
    if not isinstance(payload, Mapping) and not isinstance(metrics, Mapping):
        missing = {"read_status": snapshot.release_read_status, "article_count": 0}
        return [
            _check("public_bilingual_gate", "critical", missing),
            _check("release_validator", "critical", {**missing, "blocker_count": None}),
            _check("release_freshness", "critical", {**missing, "age_hours": None}),
            _check("weekly_digest", "critical", {"read_status": snapshot.release_read_status}),
        ]
    if isinstance(metrics, Mapping):
        public_count = _safe_int(metrics.get("article_count"))
        bilingual = _safe_int(metrics.get("bilingual_count"))
        preprint_count = _safe_int(metrics.get("preprint_count"))
        integrity_alert_count = _safe_int(metrics.get("integrity_alert_count"))
        blocker_count = _safe_int(metrics.get("blocker_count"))
        blocker_categories = dict(metrics.get("blocker_categories") or {})
        release_updated = metrics.get("last_updated")
        latest_brief = metrics.get("latest_brief")
        finding_count = _safe_int((latest_brief or {}).get("cited_finding_count"))
    else:
        articles = [item for item in payload.get("articles") or [] if isinstance(item, Mapping)]
        preprints = [item for item in payload.get("preprints") or [] if isinstance(item, Mapping)]
        public = [*articles, *preprints]
        public_count = len(public)
        bilingual = sum(
            bool((item.get("summary") or {}).get("en"))
            and bool((item.get("summary") or {}).get("zh"))
            for item in public
        )
        blockers = validate_public_research_payload(dict(payload))
        blocker_count = len(blockers)
        blocker_categories = _blocker_categories(blockers)
        preprint_count = len(preprints)
        integrity_alert_count = len(payload.get("integrity_alerts") or [])
        release_updated = payload.get("last_updated")
        briefs = [item for item in payload.get("weekly_briefs") or [] if isinstance(item, Mapping)]
        latest_brief = briefs[0] if briefs else None
        finding_count = len((latest_brief or {}).get("cited_findings") or [])
    bilingual_ratio = bilingual / public_count if public_count else 0.0
    bilingual_ok = (
        public_count >= thresholds.min_public_articles
        and bilingual_ratio >= thresholds.min_bilingual_public_ratio
    )
    release_age = _age_hours(snapshot.collected_at, release_updated)
    release_fresh = release_age is not None and release_age <= thresholds.max_release_age_hours

    brief_date: date | None = None
    if latest_brief and latest_brief.get("end_date"):
        try:
            brief_date = date.fromisoformat(str(latest_brief["end_date"]))
        except ValueError:
            pass
    digest_age = (
        max(0.0, (snapshot.collected_at.date() - brief_date).days)
        if brief_date is not None else None
    )
    brief_status = str((latest_brief or {}).get("brief_status") or "")
    reviewer = (
        ((latest_brief or {}).get("byline") or {}).get("reviewer")
        if isinstance((latest_brief or {}).get("byline"), Mapping)
        else None
    )
    ai_review = (
        ((latest_brief or {}).get("byline") or {}).get("ai_review")
        if isinstance((latest_brief or {}).get("byline"), Mapping)
        else None
    )
    review_evidence_valid = (
        brief_status == "automatically_compiled_not_editorially_reviewed"
        and reviewer is None
    ) or (
        brief_status == "editorially_reviewed"
        and project_weekly_editorial_review(reviewer, now=snapshot.collected_at) is not None
    ) or (
        brief_status == "ai_reviewed"
        and reviewer is None
        and project_weekly_ai_review(ai_review, now=snapshot.collected_at) is not None
    )
    digest_valid = bool(
        latest_brief
        and review_evidence_valid
        and digest_age is not None
        and digest_age <= thresholds.max_digest_age_days
    )
    return [
        _check(
            "public_bilingual_gate",
            "pass" if bilingual_ok else "critical",
            {
                "article_count": public_count,
                "bilingual_count": bilingual,
                "missing_bilingual_count": public_count - bilingual,
                "bilingual_ratio": round(bilingual_ratio, 6),
            },
            {
                "min_public_articles": thresholds.min_public_articles,
                "min_bilingual_ratio": thresholds.min_bilingual_public_ratio,
            },
        ),
        _check(
            "release_validator",
            "pass" if blocker_count <= thresholds.max_release_blockers else "critical",
            {
                "blocker_count": blocker_count,
                "blocker_categories": blocker_categories,
                "integrity_alert_count": integrity_alert_count,
                "preprint_count": preprint_count,
            },
            {"max_release_blockers": thresholds.max_release_blockers},
        ),
        _check(
            "release_freshness",
            "pass" if release_fresh else "critical",
            {"age_hours": release_age, "read_status": snapshot.release_read_status},
            {"max_release_age_hours": thresholds.max_release_age_hours},
        ),
        _check(
            "weekly_digest",
            "pass" if digest_valid else "critical",
            {
                "brief_present": latest_brief is not None,
                "brief_age_days": digest_age,
                "cited_finding_count": finding_count,
                "brief_status": brief_status or "missing",
                "human_reviewed": brief_status == "editorially_reviewed",
                "ai_reviewed": brief_status == "ai_reviewed",
                "review_evidence_valid": review_evidence_valid,
            },
            {"max_digest_age_days": thresholds.max_digest_age_days},
        ),
    ]
