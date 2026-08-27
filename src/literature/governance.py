"""Privacy-safe planning helpers for Research Radar backlog governance."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from sqlalchemy import func, select

from src.core.database import get_db
from src.domain import LiteratureArticle, LiteratureSignalArticleLink, LiteratureSummary
from src.services.literature_automation_service import POLICY_VERSION


GOVERNANCE_SCHEMA_VERSION = 1
_ACTIONABLE_DECISIONS = {None, "hold"}
_SPACE = re.compile(r"\s+")


def _decision(metadata: Any) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("autopilot")
    if not isinstance(value, Mapping):
        return None
    decision = value.get("decision")
    return str(decision) if decision in {"hold", "defer", "archive", "publish", "exclude"} else None


def _primary_reason(metadata: Any, fallback: str) -> str:
    if not isinstance(metadata, Mapping):
        return fallback
    value = metadata.get("autopilot")
    if not isinstance(value, Mapping):
        return fallback
    reasons = value.get("reasons")
    if isinstance(reasons, list) and reasons and isinstance(reasons[0], str):
        return reasons[0]
    return fallback


def _age_bucket(value: datetime | None, *, now: datetime) -> str:
    if value is None:
        return "unknown"
    observed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    age = now - observed.astimezone(timezone.utc)
    if age <= timedelta(days=7):
        return "0-7d"
    if age <= timedelta(days=30):
        return "8-30d"
    if age <= timedelta(days=90):
        return "31-90d"
    return "90d+"


async def audit_current_backlog() -> dict[str, Any]:
    """Return aggregate exception reasons, ages, and duplicate indicators.

    No article identifiers, titles, URLs, or free-form review notes leave this
    function. Only compact columns are read, keeping the audit bounded.
    """

    now = datetime.now(timezone.utc)
    async with get_db() as db:
        articles = (
            await db.execute(
                select(
                    LiteratureArticle.title,
                    LiteratureArticle.doi,
                    LiteratureArticle.pmid,
                    LiteratureArticle.metadata_,
                    LiteratureArticle.indexed_at,
                    LiteratureArticle.created_at,
                ).where(LiteratureArticle.publication_status == "review")
            )
        ).all()
        summaries = (
            await db.execute(
                select(
                    LiteratureSummary.generation_metadata,
                    LiteratureSummary.quality_score,
                    LiteratureSummary.generated_at,
                    LiteratureSummary.created_at,
                ).where(LiteratureSummary.status == "review")
            )
        ).all()
        # ``COUNT`` avoids materializing relationship payloads.
        review_links = int(
            (
                await db.execute(
                    select(func.count()).select_from(LiteratureSignalArticleLink).where(
                        LiteratureSignalArticleLink.status == "review"
                    )
                )
            ).scalar_one()
            or 0
        )

    article_reasons: Counter[str] = Counter()
    article_ages: Counter[str] = Counter()
    article_actionable = 0
    normalized_titles: Counter[str] = Counter()
    dois: Counter[str] = Counter()
    pmids: Counter[str] = Counter()
    for title, doi, pmid, metadata, indexed_at, created_at in articles:
        normalized = _SPACE.sub(" ", str(title or "").strip().casefold())
        if normalized:
            normalized_titles[normalized] += 1
        if doi:
            dois[str(doi).strip().casefold()] += 1
        if pmid:
            pmids[str(pmid).strip()] += 1
        if _decision(metadata) not in _ACTIONABLE_DECISIONS:
            continue
        article_actionable += 1
        article_reasons[_primary_reason(metadata, "not evaluated by current policy")] += 1
        article_ages[_age_bucket(indexed_at or created_at, now=now)] += 1

    summary_reasons: Counter[str] = Counter()
    summary_ages: Counter[str] = Counter()
    summary_actionable = 0
    for metadata, quality, generated_at, created_at in summaries:
        persisted_decision = _decision(metadata)
        if persisted_decision in {"defer", "archive"}:
            continue
        summary_actionable += 1
        if persisted_decision == "publish":
            reason = "published gate marker has review status; revalidation required"
        elif quality is not None and float(quality) < 0.90:
            reason = "summary quality is below the automatic gate"
        else:
            reason = _primary_reason(metadata, "summary needs evidence-trace or fingerprint review")
        summary_reasons[reason] += 1
        summary_ages[_age_bucket(generated_at or created_at, now=now)] += 1

    def duplicate_extras(counter: Counter[str]) -> int:
        return sum(count - 1 for count in counter.values() if count > 1)

    return {
        "collected_at": now.replace(microsecond=0).isoformat(),
        "actionable": {
            "articles": article_actionable,
            "links": review_links,
            "summaries": summary_actionable,
            "total": article_actionable + review_links + summary_actionable,
        },
        "reason_counts": {
            "articles": dict(article_reasons.most_common()),
            "summaries": dict(summary_reasons.most_common()),
        },
        "age_buckets": {
            "articles": dict(sorted(article_ages.items())),
            "summaries": dict(sorted(summary_ages.items())),
        },
        "duplicate_extras": {
            "doi": duplicate_extras(dois),
            "pmid": duplicate_extras(pmids),
            "normalized_title": duplicate_extras(normalized_titles),
        },
    }


_PLAN_COUNT_KEYS = (
    "articles_published",
    "articles_excluded",
    "articles_deferred",
    "article_exceptions",
    "links_confirmed",
    "links_rejected",
    "link_exceptions",
    "summaries_published",
    "summaries_archived",
    "summaries_deferred",
    "summary_exceptions",
    "changed",
)


def governance_plan(preview: Mapping[str, Any], *, max_projected_backlog: int) -> dict[str, Any]:
    counts = {key: int(preview.get(key) or 0) for key in _PLAN_COUNT_KEYS}
    projected = (
        counts["article_exceptions"]
        + counts["link_exceptions"]
        + counts["summary_exceptions"]
    )
    body = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "policy_version": str(preview.get("policy_version") or POLICY_VERSION),
        "autopilot_enabled": bool(preview.get("enabled")),
        "article_min_score": float(preview.get("effective_article_min_score") or 0.0),
        "counts": counts,
        "projected_actionable_backlog": projected,
        "max_projected_backlog": int(max_projected_backlog),
        "within_backlog_guard": projected <= max_projected_backlog,
        "projected_reason_counts": dict(preview.get("decision_reasons") or {}),
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**body, "plan_sha256": digest}


__all__ = ["GOVERNANCE_SCHEMA_VERSION", "audit_current_backlog", "governance_plan"]
