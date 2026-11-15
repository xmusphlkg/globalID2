"""Versioned Research Radar autopilot with exception-only editorial review.

Automation is deliberately deterministic.  It can publish only records that
pass bibliographic, integrity, provenance, and confidence gates; all decisions
are written into existing audit metadata and status-event tables.  Explicit
editorial decisions always win and are never rewritten by this service.
"""

from __future__ import annotations

from src.literature.automation_queries import (
    _link_reconciliation_statement as _link_reconciliation_statement,
    _article_reconciliation_statement as _article_reconciliation_statement,
    _summary_reconciliation_statement as _summary_reconciliation_statement,
    _english_summary_fingerprint_statement as _english_summary_fingerprint_statement,
)


from src.literature.automation_policy import (
    POLICY_VERSION as POLICY_VERSION,
    AUTOPILOT_ACTOR as AUTOPILOT_ACTOR,
    AutomationDecision as AutomationDecision,
    _CORRECTION_TITLE as _CORRECTION_TITLE,
    _POLICY_OVERRIDE_FIELDS as _POLICY_OVERRIDE_FIELDS,
    _SCORED_REASON as _SCORED_REASON,
    _effective_config as _effective_config,
    _diagnostic_reason as _diagnostic_reason,
    _persisted_autopilot_decision as _persisted_autopilot_decision,
    _is_autopilot_managed as _is_autopilot_managed,
    _aware as _aware,
    _article_hold_reasons as _article_hold_reasons,
    _research_domain as _research_domain,
    _has_explicit_correction_parent as _has_explicit_correction_parent,
    decide_evidence_link as decide_evidence_link,
    decide_article as decide_article,
    _is_parent_archived_summary as _is_parent_archived_summary,
    decide_summary as decide_summary,
    _published_revalidation_status as _published_revalidation_status,
    _audit_payload as _audit_payload,
    _PARENT_EXCLUDED_GATE as _PARENT_EXCLUDED_GATE,
)


import asyncio
import re
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Load

from src.core.config import get_config
from src.core.database import get_db
from src.domain import (
    LiteratureArticle,
    LiteratureDiseaseLink,
    LiteratureEvidenceGap,
    LiteratureIngestRun,
    LiteratureSignalArticleLink,
    LiteratureStatusEvent,
    LiteratureSummary,
)
from src.literature.enrichment import (
    ALLOWED_EVIDENCE,
    SUMMARY_FIELDS,
    canonical_summary_fingerprint,
    source_fingerprint,
)

_RUN_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class _EffectiveLink:
    link_id: int
    article_id: str
    gap_id: str | None
    relation_level: str
    status: str


async def _configure_transaction_limits(
    db: Any,
    *,
    statement_timeout_seconds: int,
    lock_timeout_seconds: int,
) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT set_config('statement_timeout', :value, true)"),
        {"value": f"{statement_timeout_seconds}s"},
    )
    await db.execute(
        text("SELECT set_config('lock_timeout', :value, true)"),
        {"value": f"{lock_timeout_seconds}s"},
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# Archived summaries a deferred parent may bring back: archived because of the
# parent, not judged on their own content.


async def restore_parent_excluded_summaries(
    db: Any,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Restore archived summaries whose archive was caused by the parent exclusion.

    Finds LiteratureSummary rows that:
    - have status "archived"
    - were archived because the parent article was excluded
      (generation_metadata["publication_gate"] == "parent-article-excluded")
    - whose parent article now has publication_status "review" or "published"

    Moves matching summaries back to "review", writes an audit note, and
    returns counts.  With dry_run=True, changes nothing.

    Never restores summaries archived by AI review, an editor, or any other
    reason.
    """
    at = _now()
    candidates = 0
    restored = 0
    by_parent_status: dict[str, int] = {}

    archived_summaries = (
        await db.execute(
            select(LiteratureSummary, LiteratureArticle)
            .join(
                LiteratureArticle,
                LiteratureArticle.article_id == LiteratureSummary.article_id,
            )
            .where(
                LiteratureSummary.status == "archived",
                LiteratureArticle.publication_status.in_(("review", "published")),
            )
        )
    ).all()

    for summary, article in archived_summaries:
        gate = (summary.generation_metadata or {}).get("publication_gate")
        if gate != _PARENT_EXCLUDED_GATE or not _is_parent_archived_summary(summary):
            # Archived for a different reason (AI review, editor, quality gate);
            # never restore these.
            continue
        candidates += 1
        parent_status = article.publication_status
        by_parent_status[parent_status] = by_parent_status.get(parent_status, 0) + 1
        if dry_run:
            continue
        summary.status = "review"
        summary.generation_metadata = {
            **(summary.generation_metadata or {}),
            "publication_gate": "restored-from-parent-exclusion",
            "autopilot_restore": {
                "restored_at": at.isoformat(),
                "actor": AUTOPILOT_ACTOR,
                "policy_version": POLICY_VERSION,
                "reason": (
                    f"parent article moved to {parent_status}; "
                    "summary restored from parent-exclusion archive"
                ),
                "previous_gate": _PARENT_EXCLUDED_GATE,
            },
        }
        restored += 1

    if not dry_run and restored:
        await db.flush()

    return {
        "dry_run": dry_run,
        "candidates": candidates,
        "restored": restored,
        "by_parent_status": by_parent_status,
    }


class LiteratureAutomationService:
    async def reconcile(
        self,
        *,
        dry_run: bool = False,
        policy_overrides: Mapping[str, float] | None = None,
        diagnostics: bool = False,
        batch_size: int | None = None,
        statement_timeout_seconds: int | None = None,
        lock_timeout_seconds: int | None = None,
        expected_plan_sha256: str | None = None,
        max_projected_backlog: int = 500,
        max_changes: int = 5000,
        article_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        # None is an explicit full-corpus maintenance run. An empty collection
        # is a no-op, never an accidental request to reconcile the whole store.
        scope_article_ids = (
            tuple(sorted({str(value).strip() for value in article_ids if str(value).strip()}))
            if article_ids is not None else None
        )
        config = _effective_config(get_config().literature, policy_overrides)
        if scope_article_ids == ():
            return {
                "enabled": config.autopilot_enabled,
                "dry_run": dry_run,
                "policy_version": POLICY_VERSION,
                "scope": "articles",
                "scope_article_count": 0,
                "changed": 0,
                "status": "not_required",
            }
        if not config.autopilot_enabled and not dry_run:
            return {"enabled": False, "dry_run": False, "policy_version": POLICY_VERSION, "changed": 0}
        effective_batch_size = int(
            config.autopilot_reconcile_batch_size if batch_size is None else batch_size
        )
        effective_statement_timeout = int(
            config.autopilot_statement_timeout_seconds
            if statement_timeout_seconds is None
            else statement_timeout_seconds
        )
        effective_lock_timeout = int(
            config.autopilot_lock_timeout_seconds
            if lock_timeout_seconds is None
            else lock_timeout_seconds
        )
        if not 1 <= effective_batch_size <= 5000:
            raise ValueError("batch_size must be between 1 and 5000")
        if not 30 <= effective_statement_timeout <= 3600:
            raise ValueError("statement_timeout_seconds must be between 30 and 3600")
        if not 1 <= effective_lock_timeout <= 300:
            raise ValueError("lock_timeout_seconds must be between 1 and 300")
        from src.ai.runtime import current_parent
        parent = current_parent()
        if not dry_run and parent is None:
            from src.services.model_operations import enqueue_model_operation
            task = await enqueue_model_operation("literature_reconcile", {
                "dry_run": False,
                "policy_overrides": dict(policy_overrides) if policy_overrides else None,
                "diagnostics": diagnostics,
                "batch_size": batch_size,
                "statement_timeout_seconds": statement_timeout_seconds,
                "lock_timeout_seconds": lock_timeout_seconds,
                "expected_plan_sha256": expected_plan_sha256,
                "max_projected_backlog": max_projected_backlog,
                "max_changes": max_changes,
                **({"article_ids": list(scope_article_ids)} if scope_article_ids is not None else {}),
            })
            return {"enabled": True, "dry_run": False, "status": "queued", "task_uuid": task.task_uuid, "changed": 0}
        if expected_plan_sha256 and not dry_run:
            from src.literature.governance import governance_plan
            preview = await self.reconcile(dry_run=True, policy_overrides=policy_overrides, diagnostics=True, batch_size=batch_size, statement_timeout_seconds=statement_timeout_seconds, lock_timeout_seconds=lock_timeout_seconds, article_ids=scope_article_ids)
            plan = governance_plan(preview, max_projected_backlog=max_projected_backlog)
            if plan["plan_sha256"] != expected_plan_sha256:
                raise ValueError("Confirmed governance plan changed before worker execution")
            if not plan["within_backlog_guard"] or plan["counts"]["changed"] > max_changes:
                raise ValueError("Governance plan exceeds the confirmed bounds")
        async with _RUN_LOCK:
            from src.literature.pipeline import _retry_postgres_deadlock

            # Retry the whole rolled-back transaction with a fresh session and
            # counters. Publication artifacts are generated only by EXPORT_DATA.
            result = await _retry_postgres_deadlock(
                lambda: self._reconcile_database(
                    config,
                    dry_run=dry_run,
                    diagnostics=diagnostics,
                    batch_size=effective_batch_size,
                    statement_timeout_seconds=effective_statement_timeout,
                    lock_timeout_seconds=effective_lock_timeout,
                    source_task_uuid=parent.task_uuid if parent else None,
                    article_ids=scope_article_ids,
                ),
                max_retries=config.persistence_deadlock_max_retries,
                base_delay_seconds=config.persistence_deadlock_retry_base_seconds,
            )
            return result

    async def _reconcile_database(
        self,
        config: Any,
        *,
        dry_run: bool,
        diagnostics: bool = False,
        batch_size: int,
        statement_timeout_seconds: int,
        lock_timeout_seconds: int,
        source_task_uuid: str | None = None,
        article_ids: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if not dry_run and not source_task_uuid:
            raise RuntimeError("Literature governance writes require a worker task")
        at = _now()
        scope_article_ids = article_ids
        scope_set = set(scope_article_ids) if scope_article_ids is not None else None
        counts = {
            "articles_published": 0,
            "articles_excluded": 0,
            "articles_deferred": 0,
            "articles_reopened": 0,
            "article_exceptions": 0,
            "links_confirmed": 0,
            "links_rejected": 0,
            "link_exceptions": 0,
            "summaries_published": 0,
            "summaries_queued_for_peer_review": 0,
            "summaries_archived": 0,
            "summaries_deferred": 0,
            "summaries_restored": 0,
            "summaries_reopened": 0,
            "summary_exceptions": 0,
            "gaps_covered": 0,
            "gaps_reopened": 0,
        }
        published_article_ids: list[str] = []
        decision_reasons: dict[str, Counter[str]] = {
            "articles": Counter(),
            "links": Counter(),
            "summaries": Counter(),
        }

        def record_reason(entity: str, decision: AutomationDecision) -> None:
            if diagnostics:
                decision_reasons[entity][_diagnostic_reason(decision)] += 1

        async with get_db() as db:
            await _configure_transaction_limits(
                db,
                statement_timeout_seconds=statement_timeout_seconds,
                lock_timeout_seconds=lock_timeout_seconds,
            )
            confirmed_levels_by_article: dict[str, set[str]] = {}
            links_by_gap: dict[str, list[_EffectiveLink]] = {}
            linked_article_status: dict[str, str] = {}
            link_cursor = 0
            while True:
                links_with_articles = (
                    await db.execute(
                        _link_reconciliation_statement(
                            after_id=link_cursor,
                            batch_size=batch_size,
                            article_ids=scope_article_ids,
                        )
                    )
                ).all()
                if not links_with_articles:
                    break
                link_cursor = int(links_with_articles[-1][0].id)
                for link, article in links_with_articles:
                    linked_article_status[article.article_id] = article.publication_status
                    effective = link.status
                    if link.status in {"review", "deprioritized"} and (
                        scope_set is None or article.article_id in scope_set
                    ):
                        decision = decide_evidence_link(link, article, config, now=at)
                        record_reason("links", decision)
                        if decision.action == "confirm":
                            effective = "confirmed"
                            counts["links_confirmed"] += 1
                        elif decision.action == "reject":
                            effective = "rejected"
                            counts["links_rejected"] += 1
                        elif link.status == "review":
                            counts["link_exceptions"] += 1
                        if not dry_run and effective != link.status:
                            link.status = effective
                            link.reviewed_at = at
                            link.reviewed_by = AUTOPILOT_ACTOR
                            link.review_note = "; ".join(decision.reasons)
                            link.metadata_ = {
                                **(link.metadata_ or {}),
                                "autopilot": _audit_payload(decision, at=at, config=config),
                            }
                    projected = _EffectiveLink(
                        link_id=int(link.id),
                        article_id=article.article_id,
                        gap_id=link.gap_id,
                        relation_level=link.relation_level,
                        status=effective,
                    )
                    if effective == "confirmed":
                        confirmed_levels_by_article.setdefault(
                            article.article_id, set()
                        ).add(link.relation_level)
                    if link.gap_id:
                        links_by_gap.setdefault(link.gap_id, []).append(projected)
                if not dry_run:
                    await db.flush()
                for link, _article in links_with_articles:
                    db.expunge(link)
                unique_articles = {
                    id(article): article for _link, article in links_with_articles
                }
                for article in unique_articles.values():
                    db.expunge(article)
            # Review rows are candidates for first publication. Autopilot-owned
            # published rows are revalidated so stale policy or classification
            # evidence cannot remain on the public boundary indefinitely.
            effective_article_status: dict[str, str] = {}
            article_cursor = 0
            while True:
                articles = (
                    await db.execute(
                        _article_reconciliation_statement(
                            after_id=article_cursor,
                            batch_size=batch_size,
                            article_ids=scope_article_ids,
                        )
                    )
                ).scalars().all()
                if not articles:
                    break
                article_cursor = int(articles[-1].id)
                article_ids = [article.article_id for article in articles]
                correction_database_ids = [
                    int(article.id)
                    for article in articles
                    if _CORRECTION_TITLE.search(str(article.title or ""))
                ]
                correction_payloads = {}
                if correction_database_ids:
                    correction_payloads = {
                        int(database_id): payload
                        for database_id, payload in (
                            await db.execute(
                                select(
                                    LiteratureArticle.id,
                                    LiteratureArticle.source_payload,
                                ).where(
                                    LiteratureArticle.id.in_(correction_database_ids)
                                )
                            )
                        ).all()
                    }
                disease_confidence = {
                    article_id: float(value or 0.0)
                    for article_id, value in (
                        await db.execute(
                            select(
                                LiteratureDiseaseLink.article_id,
                                func.max(LiteratureDiseaseLink.confidence),
                            )
                            .where(LiteratureDiseaseLink.article_id.in_(article_ids))
                            .group_by(LiteratureDiseaseLink.article_id)
                        )
                    ).all()
                }
                for article in articles:
                    article._autopilot_source_payload = correction_payloads.get(
                        int(article.id), {}
                    )
                    original_status = article.publication_status
                    was_published = original_status == "published"
                    autopilot_managed = _is_autopilot_managed(article.metadata_)
                    if was_published and not autopilot_managed:
                        continue
                    confirmed_levels = confirmed_levels_by_article.get(
                        article.article_id, set()
                    )
                    decision = decide_article(
                        article,
                        config,
                        max_disease_confidence=disease_confidence.get(article.article_id, 0.0),
                        confirmed_relation_levels=confirmed_levels,
                        now=at,
                    )
                    record_reason("articles", decision)
                    if was_published:
                        desired = _published_revalidation_status(article, decision, now=at)
                        if desired == "published":
                            continue
                        if desired == "excluded":
                            counts["articles_excluded"] += 1
                        else:
                            counts["articles_reopened"] += 1
                    elif decision.action == "publish":
                        desired = "published"
                        counts["articles_published"] += 1
                        if len(published_article_ids) < 100:
                            published_article_ids.append(article.article_id)
                    elif decision.action == "exclude":
                        desired = "excluded"
                        counts["articles_excluded"] += 1
                    elif decision.action == "defer":
                        desired = "review"
                        counts["articles_deferred"] += 1
                    else:
                        desired = "review"
                        counts["article_exceptions"] += 1
                    if desired != original_status:
                        effective_article_status[article.article_id] = desired
                    if dry_run:
                        continue
                    # Refresh the final evaluated decision even when status remains
                    # review. This replaces stale exclude metadata after a newer
                    # classification legitimately reopens the record.
                    if not (article.metadata_ or {}).get("editorial_locked"):
                        article.metadata_ = {
                            **(article.metadata_ or {}),
                            "autopilot": _audit_payload(decision, at=at, config=config),
                        }
                    if desired == original_status:
                        continue
                    article.publication_status = desired
                    db.add(LiteratureStatusEvent(
                        article_id=article.article_id,
                        event_type="publication_status_changed",
                        previous_status=original_status,
                        current_status=desired,
                        source=AUTOPILOT_ACTOR,
                        effective_at=at,
                        metadata_={"policy_version": POLICY_VERSION, "reasons": list(decision.reasons)},
                    ))
                if not dry_run:
                    await db.flush()
                for article in articles:
                    db.expunge(article)

            english_canonical_fingerprints: dict[str, str] = {}
            english_summary_cursor = 0
            while True:
                english_summary_rows = (
                    await db.execute(
                        _english_summary_fingerprint_statement(
                            after_id=english_summary_cursor,
                            batch_size=batch_size,
                            article_ids=scope_article_ids,
                        )
                    )
                ).all()
                if not english_summary_rows:
                    break
                english_summary_cursor = int(english_summary_rows[-1][0])
                for row in english_summary_rows:
                    english_canonical_fingerprints[row[1]] = canonical_summary_fingerprint({
                        field: row[index + 2]
                        for index, field in enumerate(SUMMARY_FIELDS)
                    })
            summary_cursor = 0
            while True:
                summaries_with_articles = (
                    await db.execute(
                        _summary_reconciliation_statement(
                            after_id=summary_cursor,
                            batch_size=batch_size,
                            article_ids=scope_article_ids,
                        )
                    )
                ).all()
                if not summaries_with_articles:
                    break
                summary_cursor = int(summaries_with_articles[-1][0].id)
                for summary, article in summaries_with_articles:
                    projected_article_status = effective_article_status.get(
                        article.article_id, article.publication_status
                    )
                    original_status = article.publication_status
                    if projected_article_status != original_status:
                        article.publication_status = projected_article_status
                    decision = decide_summary(
                        summary,
                        article,
                        config,
                        expected_canonical_summary_fingerprint=english_canonical_fingerprints.get(
                            summary.article_id
                        ),
                    )
                    article.publication_status = original_status
                    record_reason("summaries", decision)
                    if decision.action == "publish":
                        # The deterministic gate no longer publishes: a passing
                        # summary waits in ``review`` for the shared two-model
                        # peer review run by AI content governance.
                        if summary.status != "published":
                            counts["summaries_queued_for_peer_review"] += 1
                        if not dry_run and summary.status != "published":
                            summary.status = "review"
                            summary.generation_metadata = {
                                **(summary.generation_metadata or {}),
                                "publication_gate": "awaiting-peer-review",
                                "autopilot": _audit_payload(decision, at=at, config=config),
                            }
                    elif decision.action == "archive":
                        if summary.status != "archived":
                            counts["summaries_archived"] += 1
                        if not dry_run and summary.status != "archived":
                            summary.status = "archived"
                            summary.generation_metadata = {
                                **(summary.generation_metadata or {}),
                                "publication_gate": "parent-article-excluded",
                                "autopilot": _audit_payload(decision, at=at, config=config),
                            }
                    elif decision.action == "defer":
                        counts["summaries_deferred"] += 1
                        gate = (summary.generation_metadata or {}).get("publication_gate")
                        # Only summaries archived because of the parent come back; an
                        # AI rejection or quality archive stays final.
                        restore = _is_parent_archived_summary(summary)
                        reopen = summary.status == "published" and bool(
                            (summary.generation_metadata or {}).get("autopilot")
                        )
                        if restore:
                            counts["summaries_restored"] += 1
                        elif reopen:
                            counts["summaries_reopened"] += 1
                        if summary.status == "archived" and not restore:
                            continue
                        if (
                            not restore
                            and not reopen
                            and summary.status == "review"
                            and gate == "awaiting-article-decision"
                        ):
                            # Already waiting: rewriting the audit each cycle only churns updated_at.
                            continue
                        if not dry_run:
                            if restore or reopen:
                                summary.status = "review"
                            summary.generation_metadata = {
                                **(summary.generation_metadata or {}),
                                "publication_gate": "awaiting-article-decision",
                                "autopilot": _audit_payload(decision, at=at, config=config),
                            }
                    elif summary.status == "review":
                        counts["summary_exceptions"] += 1
                    elif (summary.generation_metadata or {}).get("autopilot"):
                        # A published summary that no longer passes a quality
                        # gate stays public. It is flagged for review instead of
                        # being withdrawn; the parent-article check above is the
                        # only path that takes a published summary down.
                        counts["summary_exceptions"] += 1
                        previous = (summary.generation_metadata or {}).get("autopilot_revalidation")
                        unchanged = isinstance(previous, dict) and previous.get("reasons") == list(
                            decision.reasons
                        )
                        if not dry_run and not unchanged:
                            summary.generation_metadata = {
                                **(summary.generation_metadata or {}),
                                "autopilot_revalidation": _audit_payload(
                                    decision, at=at, config=config
                                ),
                            }
                if not dry_run:
                    await db.flush()
                for summary, _article in summaries_with_articles:
                    db.expunge(summary)
                unique_articles = {
                    id(article): article for _summary, article in summaries_with_articles
                }
                for article in unique_articles.values():
                    db.expunge(article)

            gap_statement = select(LiteratureEvidenceGap).where(
                LiteratureEvidenceGap.status.notin_(("dismissed", "inactive"))
            )
            if scope_article_ids is not None:
                gap_statement = gap_statement.where(LiteratureEvidenceGap.gap_id.in_(links_by_gap))
            gaps = (
                await db.execute(
                    gap_statement
                )
            ).scalars().all()
            for gap in gaps:
                related = links_by_gap.get(gap.gap_id, [])
                is_covered = any(
                    link.status == "confirmed"
                    and link.relation_level == "exact_disease_geography"
                    and effective_article_status.get(
                        link.article_id,
                        linked_article_status.get(link.article_id, "review"),
                    ) == "published"
                    for link in related
                )
                has_exception = any(
                    link.status == "review"
                    for link in related
                )
                if is_covered:
                    desired = "covered"
                elif has_exception:
                    desired = "review"
                elif related and gap.status not in {"no_results", "error"}:
                    desired = "open"
                else:
                    desired = gap.status
                if desired == "covered" and gap.status != "covered":
                    counts["gaps_covered"] += 1
                elif desired == "open" and gap.status != "open":
                    counts["gaps_reopened"] += 1
                if not dry_run and desired != gap.status:
                    gap.status = desired
                    gap.resolved_at = at if desired == "covered" else None
                    gap.resolution_note = (
                        f"Automatically covered by an exact public evidence link under {POLICY_VERSION}."
                        if desired == "covered"
                        else None
                    )
                if not dry_run:
                    gap.latest_metrics = {
                        **(gap.latest_metrics or {}),
                        "autopilot_policy_version": POLICY_VERSION,
                        "autopilot_last_evaluated_at": at.isoformat(),
                    }

            changed = sum(
                counts[key]
                for key in (
                    "articles_published", "articles_excluded", "links_confirmed", "links_rejected",
                    "articles_reopened",
                    "summaries_published", "summaries_archived", "summaries_restored",
                    "summaries_reopened",
                    "gaps_covered", "gaps_reopened",
                )
            )
            if not dry_run and changed:
                run_uuid = str(uuid.uuid4())
                db.add(LiteratureIngestRun(
                    run_uuid=run_uuid,
                    source="research-radar-autopilot-scoped" if scope_article_ids is not None else "research-radar-autopilot",
                    status="completed",
                    started_at=at,
                    completed_at=_now(),
                    through_indexed_at=at,
                    checkpoint={
                        "policy_version": POLICY_VERSION,
                        "mode": "apply",
                        "scope": "articles" if scope_article_ids is not None else "all",
                        "scope_article_count": len(scope_article_ids) if scope_article_ids is not None else None,
                    },
                    counts={**counts, "changed": changed},
                ))
            else:
                run_uuid = None
            if not dry_run:
                from src.services.ingestion.stages import mark_dirty
                await mark_dirty(db, line="literature", scope_key="*", task_uuid=source_task_uuid)
                await db.commit()
        result = {
            "enabled": config.autopilot_enabled,
            "dry_run": dry_run,
            "policy_version": POLICY_VERSION,
            "scope": "articles" if scope_article_ids is not None else "all",
            "scope_article_count": len(scope_article_ids) if scope_article_ids is not None else None,
            "effective_article_min_score": float(config.autopilot_article_min_score),
            "reconcile_batch_size": batch_size,
            "statement_timeout_seconds": statement_timeout_seconds,
            "lock_timeout_seconds": lock_timeout_seconds,
            "run_uuid": run_uuid,
            **counts,
            "changed": changed,
            "published_article_ids": published_article_ids[:100],
        }
        if diagnostics:
            result["decision_reasons"] = {
                entity: dict(counter.most_common())
                for entity, counter in decision_reasons.items()
            }
        return result

    async def snapshot(self) -> dict[str, Any]:
        config = get_config().literature
        async with get_db() as db:
            article_metadata = (
                await db.execute(
                    select(LiteratureArticle.metadata_).where(
                        LiteratureArticle.publication_status == "published"
                    )
                )
            ).scalars().all()
            auto_articles = sum(
                1 for metadata in article_metadata
                if (metadata or {}).get("autopilot", {}).get("policy_version") == POLICY_VERSION
            )
            auto_links = int((await db.execute(
                select(func.count()).select_from(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.reviewed_by == AUTOPILOT_ACTOR,
                    LiteratureSignalArticleLink.status == "confirmed",
                )
            )).scalar_one() or 0)
            summary_metadata = (
                await db.execute(
                    select(LiteratureSummary.generation_metadata).where(
                        LiteratureSummary.status == "published"
                    )
                )
            ).scalars().all()
            auto_summaries = sum(
                1 for metadata in summary_metadata
                if (metadata or {}).get("autopilot", {}).get("policy_version") == POLICY_VERSION
            )
            review_article_metadata = (
                await db.execute(
                    select(LiteratureArticle.metadata_).where(
                        LiteratureArticle.publication_status == "review"
                    )
                )
            ).scalars().all()
            deferred_articles = sum(
                1 for metadata in review_article_metadata
                if _persisted_autopilot_decision(metadata) == "defer"
            )
            archived_decision_articles = sum(
                1 for metadata in review_article_metadata
                if _persisted_autopilot_decision(metadata) == "archive"
            )
            exception_articles = (
                len(review_article_metadata) - deferred_articles - archived_decision_articles
            )
            exception_links = int((await db.execute(
                select(func.count()).select_from(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.status == "review"
                )
            )).scalar_one() or 0)
            review_summary_metadata = (
                await db.execute(
                    select(LiteratureSummary.generation_metadata).where(
                        LiteratureSummary.status == "review"
                    )
                )
            ).scalars().all()
            deferred_summaries = sum(
                1 for metadata in review_summary_metadata
                if _persisted_autopilot_decision(metadata) == "defer"
            )
            archived_decision_summaries = sum(
                1 for metadata in review_summary_metadata
                if _persisted_autopilot_decision(metadata) == "archive"
            )
            exception_summaries = (
                len(review_summary_metadata) - deferred_summaries - archived_decision_summaries
            )
            archived_summaries = int((await db.execute(
                select(func.count()).select_from(LiteratureSummary).where(LiteratureSummary.status == "archived")
            )).scalar_one() or 0)
            latest_run = (
                await db.execute(
                    select(LiteratureIngestRun)
                    .where(LiteratureIngestRun.source == "research-radar-autopilot")
                    .order_by(LiteratureIngestRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return {
            "enabled": config.autopilot_enabled,
            "policy_version": POLICY_VERSION,
            "mode": "exception-only-review" if config.autopilot_enabled else "manual-review",
            "thresholds": {
                "article_min_score": config.autopilot_article_min_score,
                "article_exclude_below_score": config.autopilot_article_exclude_below_score,
                "disease_min_confidence": config.autopilot_disease_min_confidence,
                "exact_relation_min_confidence": config.autopilot_exact_relation_min_confidence,
                "context_relation_min_confidence": config.autopilot_context_relation_min_confidence,
                "summary_min_quality": config.autopilot_summary_min_quality,
            },
            "automatic": {
                "published_articles": auto_articles,
                "confirmed_links": auto_links,
                "published_summaries": auto_summaries,
            },
            "exceptions": {
                "articles": exception_articles,
                "links": exception_links,
                "summaries": exception_summaries,
                "total": exception_articles + exception_links + exception_summaries,
            },
            "deferred": {
                "articles": deferred_articles,
                "summaries": deferred_summaries,
                "total": deferred_articles + deferred_summaries,
            },
            "archived": {
                "summaries": archived_summaries,
                "review_article_decisions": archived_decision_articles,
                "review_summary_decisions": archived_decision_summaries,
            },
            "last_run": (
                {
                    "run_uuid": latest_run.run_uuid,
                    "completed_at": latest_run.completed_at,
                    "counts": latest_run.counts or {},
                }
                if latest_run else None
            ),
        }


literature_automation_service = LiteratureAutomationService()


__all__ = [
    "AUTOPILOT_ACTOR",
    "POLICY_VERSION",
    "AutomationDecision",
    "LiteratureAutomationService",
    "decide_article",
    "decide_evidence_link",
    "decide_summary",
    "literature_automation_service",
    "restore_parent_excluded_summaries",
]
