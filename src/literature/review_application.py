"""One transactional boundary for applying literature peer-review decisions.

Model calls happen before this module is entered. All entry points reload and
lock the same records, enforce the same rules, and leave the final commit to
their caller so the decision, audit event and publication marker stay atomic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.peer_review import ReviewedOutput
from src.domain import LiteratureArticle, LiteratureSummary, Task, TaskStatus
from src.literature.screening import decision_is_grounded

ReviewKind = Literal["articles", "summaries"]
ReviewKey = str | int
CompletedReview = tuple[ReviewKey, str, ReviewedOutput]


def review_outcome(reviewed: ReviewedOutput) -> str:
    """A disagreement or an unrecognized decision remains a hold."""
    if not reviewed.published:
        return "hold"
    decision = str((reviewed.payload or {}).get("decision") or "hold")
    return decision if decision in {"publish", "exclude", "reject"} else "hold"


class ReviewPolicy(Protocol):
    """Business rules shared by queued, batch and resumed review callers."""

    def _article_fingerprint(self, article: Any) -> str: ...
    def _summary_fingerprint(self, summary: Any) -> str: ...
    async def _canonical_fingerprints(
        self, db: AsyncSession, pairs: list
    ) -> dict[str, str]: ...
    def _summary_precheck(
        self, summary: Any, article: Any, *, canonical_fingerprint: str | None = None
    ) -> tuple[str, str | None]: ...
    def _hold(
        self, row: Any, reviewed: ReviewedOutput, fingerprint: str, kind: str
    ) -> None: ...
    def _apply_article(
        self, db: AsyncSession, article: Any, reviewed: ReviewedOutput, desired: str
    ) -> None: ...
    def _apply_summary(
        self, summary: Any, reviewed: ReviewedOutput, publish: bool
    ) -> None: ...
    def _archive_summary(self, summary: Any, reason: str | None) -> None: ...


@dataclass
class ReviewApplicationResult:
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "published": 0,
            "rejected": 0,
            "held": 0,
            "stale": 0,
        }
    )
    applied: list[dict[str, Any]] = field(default_factory=list)


def _editorial_locked(row: Any) -> bool:
    metadata = (
        getattr(row, "metadata_", None)
        or getattr(row, "generation_metadata", None)
        or {}
    )
    return bool(metadata.get("editorial_locked"))


async def _lock_active_parent(
    db: AsyncSession, parent_task_uuid: str | None
) -> str | None:
    """Linearize cancellation against application before locking content rows."""
    from src.ai.runtime import current_parent
    from src.ai.runtime.context import parent_execution_generation
    from src.services.exceptions import TaskExecutionSupersededError

    parent = current_parent()
    if parent_task_uuid is None:
        parent_task_uuid = parent.task_uuid if parent is not None else None
    if parent_task_uuid is None:
        return None
    row = (
        await db.execute(
            select(Task)
            .where(Task.task_uuid == parent_task_uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        row is not None
        and parent is not None
        and parent.task_uuid == parent_task_uuid
        and parent.generation != parent_execution_generation(row)
    ):
        raise TaskExecutionSupersededError(
            "Literature review belongs to an obsolete execution generation"
        )
    if row and (
        row.status == TaskStatus.CANCELLED
        or (row.metadata_ or {}).get("cancel_requested")
    ):
        from src.services.exceptions import TaskCancelledError

        raise TaskCancelledError(
            "Parent task cancelled before applying literature review"
        )
    return parent_task_uuid


class LiteratureReviewApplication:
    def __init__(
        self,
        policy: ReviewPolicy,
        *,
        prompt_version: str,
        mark_dirty: Callable[..., Awaitable[None]],
    ) -> None:
        self.policy = policy
        self.prompt_version = prompt_version
        self.mark_dirty = mark_dirty

    async def archive_incomplete(
        self,
        db: AsyncSession,
        candidates: Sequence[tuple[int, str]],
        *,
        dry_run: bool = False,
        parent_task_uuid: str | None = None,
    ) -> ReviewApplicationResult:
        """Recheck deterministic archives under the same locks as model results."""
        result = ReviewApplicationResult()
        if not candidates:
            return result
        parent_task_uuid = await _lock_active_parent(db, parent_task_uuid)
        rows, parents = await lock_review_subjects(
            db, "summaries", sorted({key for key, _ in candidates})
        )
        canonical = await self.policy._canonical_fingerprints(
            db,
            [
                (row, parents[row.article_id])
                for row in rows.values()
                if row.article_id in parents
            ],
        )
        for key, fingerprint in candidates:
            row = rows.get(key)
            parent = parents.get(row.article_id) if row is not None else None
            if (
                row is None
                or parent is None
                or row.status != "review"
                or _editorial_locked(row)
                or _editorial_locked(parent)
            ):
                result.counts["stale"] += 1
                continue
            if self.policy._summary_fingerprint(row) != fingerprint:
                result.counts["stale"] += 1
                continue
            action, reason = self.policy._summary_precheck(
                row,
                parent,
                canonical_fingerprint=canonical.get(row.article_id),
            )
            if action != "archive":
                result.counts["stale"] += 1
                continue
            if not dry_run:
                self.policy._archive_summary(row, reason)
            result.counts["rejected"] += 1
            result.applied.append(
                {"summary_id": key, "decision": "archive", "reason": reason}
            )
        if not dry_run and result.counts["rejected"]:
            await self.mark_dirty(
                db,
                line="literature",
                scope_key="*",
                parent_task_uuid=parent_task_uuid,
            )
        return result

    async def apply(
        self,
        db: AsyncSession,
        kind: ReviewKind,
        reviews: Sequence[CompletedReview],
        *,
        dry_run: bool = False,
        parent_task_uuid: str | None = None,
    ) -> ReviewApplicationResult:
        if kind not in {"articles", "summaries"}:
            raise ValueError(f"unsupported review kind: {kind}")
        result = ReviewApplicationResult()
        if not reviews:
            return result
        parent_task_uuid = await _lock_active_parent(db, parent_task_uuid)
        keys = sorted({key for key, _fingerprint, _reviewed in reviews})
        rows, parents = await lock_review_subjects(db, kind, keys)
        canonical = (
            await self.policy._canonical_fingerprints(
                db,
                [
                    (row, parents[row.article_id])
                    for row in rows.values()
                    if row.article_id in parents
                ],
            )
            if kind == "summaries"
            else {}
        )
        applied_keys: set[ReviewKey] = set()
        for key, fingerprint, reviewed in reviews:
            row = rows.get(key)
            current = (
                None
                if row is None
                else (
                    self.policy._article_fingerprint(row)
                    if kind == "articles"
                    else self.policy._summary_fingerprint(row)
                )
            )
            status = (
                None
                if row is None
                else (row.publication_status if kind == "articles" else row.status)
            )
            if (
                row is None
                or key in applied_keys
                or status != "review"
                or current != fingerprint
                or _editorial_locked(row)
            ):
                result.counts["stale"] += 1
                continue
            if kind == "articles":
                if (
                    row.integrity_status != "current"
                    or row.peer_review_status != "peer_reviewed"
                ):
                    result.counts["stale"] += 1
                    continue
            else:
                parent = parents.get(row.article_id)
                if (
                    parent is None
                    or _editorial_locked(parent)
                    or self.policy._summary_precheck(
                        row, parent, canonical_fingerprint=canonical.get(row.article_id)
                    )[0]
                    != "review"
                ):
                    result.counts["stale"] += 1
                    continue

            outcome = review_outcome(reviewed)
            allowed = (
                {"publish", "exclude", "hold"}
                if kind == "articles"
                else {"publish", "reject", "hold"}
            )
            if outcome not in allowed:
                outcome = "hold"
            ungrounded = (
                kind == "articles"
                and outcome in {"publish", "exclude"}
                and not decision_is_grounded(row, reviewed)
            )
            if ungrounded:
                outcome = "hold"
            applied_keys.add(key)
            if outcome == "hold":
                result.counts["held"] += 1
                if not dry_run:
                    self.policy._hold(row, reviewed, current, kind)
                    if ungrounded:
                        row.metadata_ = {
                            **(row.metadata_ or {}),
                            "screening_hold_reason": "abstract_evidence_not_verified",
                        }
                displayed = "hold"
            else:
                publish = outcome == "publish"
                result.counts["published" if publish else "rejected"] += 1
                if kind == "articles":
                    displayed = "published" if publish else "excluded"
                    if not dry_run:
                        self.policy._apply_article(db, row, reviewed, displayed)
                else:
                    displayed = "publish" if publish else "archive"
                    if not dry_run:
                        self.policy._apply_summary(row, reviewed, publish)
            result.applied.append(
                {
                    "article_id" if kind == "articles" else "summary_id": key,
                    "decision": displayed,
                    "review": reviewed.provenance(self.prompt_version),
                }
            )
        if not dry_run and (result.counts["published"] or result.counts["rejected"]):
            await self.mark_dirty(
                db,
                line="literature",
                scope_key="*",
                parent_task_uuid=parent_task_uuid,
            )
        return result


async def lock_review_subjects(
    db: AsyncSession, kind: ReviewKind, keys: list[ReviewKey]
) -> tuple[dict[ReviewKey, Any], dict[str, Any]]:
    """Lock task → sorted articles → sorted summaries in every caller."""
    if kind == "articles":
        found = (
            (
                await db.execute(
                    select(LiteratureArticle)
                    .where(LiteratureArticle.article_id.in_(keys))
                    .order_by(LiteratureArticle.article_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        return {row.article_id: row for row in found}, {}

    article_ids = (
        (
            await db.execute(
                select(LiteratureSummary.article_id)
                .where(LiteratureSummary.id.in_(keys))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    parents = (
        (
            await db.execute(
                select(LiteratureArticle)
                .where(LiteratureArticle.article_id.in_(article_ids))
                .order_by(LiteratureArticle.article_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    # Lock canonical English inputs too: a translation must not be applied
    # while its canonical summary changes between validation and commit.
    summaries = (
        (
            await db.execute(
                select(LiteratureSummary)
                .where(
                    or_(
                        LiteratureSummary.id.in_(keys),
                        and_(
                            LiteratureSummary.article_id.in_(article_ids),
                            LiteratureSummary.language == "en",
                        ),
                    )
                )
                .order_by(LiteratureSummary.article_id, LiteratureSummary.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    wanted = set(keys)
    return {row.id: row for row in summaries if row.id in wanted}, {
        row.article_id: row for row in parents
    }
