"""Short transactions for durable review cursors and workflow transitions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.ai.peer_review import (
    build_author_prompt,
    build_reviewer_prompt,
    reviewer_system,
)
from src.ai.runtime import payloads, store
from src.ai.runtime.context import parent_execution_generation
from src.core import get_database
from src.domain import LiteratureArticle, LiteratureSummary, Task, TaskStatus
from src.domain.ai_review_workflow import AIReviewDrain, AIReviewWorkflow
from src.domain.ai_work_item import AIWorkItem
from src.literature.review_application import lock_review_subjects
from src.services.exceptions import TaskCancelledError, TaskExecutionSupersededError

from .reducer import POLICY_VERSION, initial_state, model_exclusions, reduce_completion

PAGE_SIZE = 64
ADVANCE_BATCH_SIZE = 8
TERMINAL_ACTIVITIES = ("succeeded", "failed", "cancelled")


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def lock_parent(db, task_uuid: str, *, check_cancel: bool = True) -> Task | None:
    if db.get_bind().dialect.name == "sqlite" and not db.info.get("ai_write_reserved"):
        await db.execute(text("BEGIN IMMEDIATE"))
        db.info["ai_write_reserved"] = True
    task = (
        await db.execute(
            select(Task).where(Task.task_uuid == task_uuid).with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
        if check_cancel:
            raise TaskCancelledError("Literature review parent no longer exists")
        return None
    if check_cancel and (
        task.status == TaskStatus.CANCELLED
        or (task.metadata_ or {}).get("cancel_requested")
    ):
        raise TaskCancelledError("Literature review parent cancelled")
    return task


def finish(workflow: AIReviewWorkflow, outcome: str, reason: str | None = None) -> None:
    workflow.status, workflow.step, workflow.outcome = "terminal", "terminal", outcome
    workflow.finished_at = workflow.updated_at = now()
    workflow.version += 1
    if reason:
        workflow.state = {**workflow.state, "terminal_reason": reason[:1000]}


async def cancel_activity(db, activity_id: int | None) -> None:
    if activity_id is None:
        return
    item = (
        await db.execute(select(AIWorkItem).where(AIWorkItem.id == activity_id))
    ).scalar_one_or_none()
    if item is not None and item.status not in TERMINAL_ACTIVITIES:
        # A leased provider call keeps its permit until the dispatcher has
        # cancelled and joined the transport coroutine.
        await store.request_cancellation(db, item)


class ReviewWorkflowRepository:
    def __init__(self, policy, *, generation: int = 0):
        self.policy = policy
        self.generation = generation

    async def _lock_current_parent(self, db, parent: str) -> Task:
        task = await lock_parent(db, parent)
        if parent_execution_generation(task) != self.generation:
            raise TaskExecutionSupersededError(
                "Literature review execution generation changed"
            )
        return task

    async def ensure_drain(
        self, parent: str, kind: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        async with get_database() as db:
            await self._lock_current_parent(db, parent)
            previous = (
                (
                    await db.execute(
                        select(AIReviewWorkflow)
                        .where(
                            AIReviewWorkflow.parent_task_uuid == parent,
                            AIReviewWorkflow.generation != self.generation,
                            AIReviewWorkflow.status == "open",
                        )
                        .order_by(AIReviewWorkflow.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for workflow in previous:
                await cancel_activity(db, workflow.activity_id)
                finish(workflow, "cancelled", "Superseded by an explicit task retry")
            drain = await db.get(AIReviewDrain, (parent, kind, self.generation))
            if drain is None:
                model = LiteratureArticle if kind == "articles" else LiteratureSummary
                watermark = await db.scalar(select(func.max(model.id)))
                drain = AIReviewDrain(
                    parent_task_uuid=parent,
                    kind=kind,
                    generation=self.generation,
                    high_watermark=int(watermark or 0),
                    settings=settings,
                )
                db.add(drain)
                await db.flush()
            # A resumed task uses its original business bounds and finite snapshot.
            return dict(drain.settings)

    async def feed(self, parent: str, kind: str, *, target_size: int) -> int:
        """Advance by immutable primary key; all outcomes advance the durable cursor."""
        async with get_database() as db:
            await self._lock_current_parent(db, parent)
            drain = await db.get(
                AIReviewDrain, (parent, kind, self.generation), with_for_update=True
            )
            if drain.exhausted:
                return 0
            active = await db.scalar(
                select(func.count())
                .select_from(AIReviewWorkflow)
                .where(
                    AIReviewWorkflow.parent_task_uuid == parent,
                    AIReviewWorkflow.generation == self.generation,
                    AIReviewWorkflow.kind == kind,
                    AIReviewWorkflow.status == "open",
                )
            )
            capacity = min(
                PAGE_SIZE, min(target_size, drain.settings["buffer_size"]) - active
            )
            remaining = drain.settings.get("max_items")
            if remaining is not None:
                capacity = min(capacity, remaining - drain.admitted)
                if capacity <= 0 and remaining <= drain.admitted:
                    drain.exhausted = True
            if capacity <= 0:
                return 0
            if kind == "articles":
                rows = (
                    (
                        await db.execute(
                            self.policy._article_review_query()
                            .order_by(None)
                            .where(
                                LiteratureArticle.id > drain.cursor,
                                LiteratureArticle.id <= drain.high_watermark,
                            )
                            .order_by(LiteratureArticle.id)
                            .limit(capacity)
                        )
                    )
                    .scalars()
                    .all()
                )
                candidates = [
                    (
                        row.id,
                        row.article_id,
                        self.policy._article_fingerprint(row),
                        row.metadata_,
                    )
                    for row in rows
                ]
            else:
                rows = (
                    (
                        await db.execute(
                            select(LiteratureSummary)
                            .where(
                                LiteratureSummary.status == "review",
                                LiteratureSummary.id > drain.cursor,
                                LiteratureSummary.id <= drain.high_watermark,
                            )
                            .order_by(LiteratureSummary.id)
                            .limit(capacity)
                        )
                    )
                    .scalars()
                    .all()
                )
                candidates = [
                    (
                        row.id,
                        str(row.id),
                        self.policy._summary_fingerprint(row),
                        row.generation_metadata,
                    )
                    for row in rows
                ]
            values = []
            deadline = now() + timedelta(seconds=drain.settings["deadline_seconds"])
            for row_id, subject_id, fingerprint, metadata in candidates:
                if self.policy._held(metadata, fingerprint):
                    continue
                values.append(
                    dict(
                        parent_task_uuid=parent,
                        kind=kind,
                        generation=self.generation,
                        subject_id=subject_id,
                        input_fingerprint=fingerprint,
                        policy_version=POLICY_VERSION,
                        status="open",
                        step="ready",
                        state=initial_state(
                            allow_degraded_review=drain.settings[
                                "allow_degraded_review"
                            ]
                        ),
                        deadline_at=deadline,
                    )
                )
            inserted = 0
            if values:
                insert = (
                    sqlite_insert
                    if db.get_bind().dialect.name == "sqlite"
                    else pg_insert
                )
                result = await db.execute(
                    insert(AIReviewWorkflow)
                    .values(values)
                    .on_conflict_do_nothing()
                    .returning(AIReviewWorkflow.id)
                )
                inserted = len(result.scalars().all())
            # Mutate the cursor once after the bulk insert. Mutating it inside
            # the loop made ORM autoflush issue an UPDATE for every candidate.
            if candidates:
                drain.cursor = candidates[-1][0]
                drain.scanned += len(candidates)
            drain.admitted += inserted
            if len(candidates) < capacity or drain.cursor >= drain.high_watermark:
                drain.exhausted = True
            return inserted

    async def open_work(self, parent: str, kind: str) -> tuple[list[int], list[int]]:
        """One bounded join, never one polling query per pending model call."""
        async with get_database() as db:
            rows = (
                await db.execute(
                    select(
                        AIReviewWorkflow.id,
                        AIReviewWorkflow.activity_id,
                        AIWorkItem.status,
                        AIReviewWorkflow.deadline_at,
                    )
                    .outerjoin(
                        AIWorkItem, AIWorkItem.id == AIReviewWorkflow.activity_id
                    )
                    .where(
                        AIReviewWorkflow.parent_task_uuid == parent,
                        AIReviewWorkflow.generation == self.generation,
                        AIReviewWorkflow.kind == kind,
                        AIReviewWorkflow.status == "open",
                    )
                    .order_by(AIReviewWorkflow.id)
                )
            ).all()
        actionable, waiting = [], []
        for work_id, activity_id, status, deadline in rows:
            if (
                activity_id is None
                or status in TERMINAL_ACTIVITIES
                or utc(deadline) <= now()
            ):
                actionable.append(work_id)
            else:
                waiting.append(activity_id)
        return actionable[:PAGE_SIZE], waiting

    def _subject(self, workflow, rows, parents, canonical):
        # Every subject in this batch has already been locked in one global order.
        key = (
            workflow.subject_id
            if workflow.kind == "articles"
            else int(workflow.subject_id)
        )
        row = rows.get(key)
        if row is None:
            return key, None, None, "stale"
        fingerprint = (
            self.policy._article_fingerprint(row)
            if workflow.kind == "articles"
            else self.policy._summary_fingerprint(row)
        )
        if fingerprint != workflow.input_fingerprint:
            return key, row, None, "stale"
        if workflow.kind == "articles":
            if (
                row.publication_status != "review"
                or row.integrity_status != "current"
                or row.peer_review_status != "peer_reviewed"
            ):
                return key, row, None, "stale"
            if (row.metadata_ or {}).get("editorial_locked"):
                return key, row, None, "skipped"
            return key, row, None, "review"
        article = parents.get(row.article_id)
        if article is None or row.status != "review":
            return key, row, article, "stale"
        action, _reason = self.policy._summary_precheck(
            row, article, canonical_fingerprint=canonical.get(row.article_id)
        )
        return key, row, article, action

    def _request(
        self, workflow, row, article, *, step, state
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from src.services.ai_content_governance_service import (
            LITERATURE_ARTICLE_REVIEW_RULES,
            LITERATURE_ARTICLE_REVIEW_SYSTEM,
            LITERATURE_SUMMARY_REVIEW_RULES,
            LITERATURE_SUMMARY_REVIEW_SYSTEM,
            PROMPT_VERSION,
            REVIEW_MAX_TOKENS,
        )

        role = step
        if workflow.kind == "articles":
            rules, system = (
                LITERATURE_ARTICLE_REVIEW_RULES,
                LITERATURE_ARTICLE_REVIEW_SYSTEM,
            )
            task = {
                "task": "Decide whether this article is published in the Research Radar.",
                "item": self.policy._literature_article_item(row),
            }
            decisions, domain = (
                "publish|exclude|hold",
                "infectious-disease Research Radar curation",
            )
        else:
            rules, system = (
                LITERATURE_SUMMARY_REVIEW_RULES,
                LITERATURE_SUMMARY_REVIEW_SYSTEM,
            )
            task = {
                "task": "Decide whether this evidence-agent summary is published.",
                "item": self.policy._literature_review_item(str(row.id), row, article),
            }
            decisions, domain = (
                "publish|reject|hold",
                "public-health literature summaries",
            )
        if role == "author":
            prompt = build_author_prompt(
                prompt_version=PROMPT_VERSION,
                task=task,
                required_output={
                    "decision": decisions,
                    "reasons": ["concise evidence-bound reasons"],
                },
                rules=rules,
                reviewer_notes=state.get("reviewer_notes"),
                previous_draft=state.get("previous"),
            )
            avoid = state.get("previous_models", [])
        else:
            system = reviewer_system(domain)
            prompt = build_reviewer_prompt(
                prompt_version=PROMPT_VERSION,
                task=task,
                draft=state["draft"],
                rules=[
                    "Audit the decision itself: is it the right outcome for this item given the rules?",
                    *rules,
                ],
            )
            avoid = model_exclusions([state.get("author_model")])
        content = {
            "prompt": prompt,
            "system": system,
            "kwargs": {},
            "workflow": {
                "id": workflow.id,
                "step": role,
                "round": state["round"],
                "opinion": state["opinion"],
            },
        }
        payload = {
            "agent_name": f"LiteratureReview:{role}",
            "temperature": 0.1,
            "max_tokens": REVIEW_MAX_TOKENS,
            "use_cache": False,
            "avoid_models": avoid,
            "workflow_id": workflow.id,
            "execution_generation": workflow.generation,
            "workflow_step": role,
            "review_round": state["round"],
            "review_opinion": state["opinion"],
            "require_distinct_model": role == "reviewer"
            and not state["allow_degraded_review"],
        }
        return content, payload

    async def advance(self, parent: str, workflow_id: int) -> bool:
        """Advance one workflow through the same transaction path as a batch."""
        return bool(await self.advance_many(parent, [workflow_id]))

    async def advance_many(self, parent: str, workflow_ids: list[int]) -> int:
        """Advance at most eight workflows with one globally ordered lock set.

        Parent → all workflows by id → all articles by article_id → summaries.
        Mixed kinds are rejected before content locks; a coordinator owns one
        kind. No provider wait holds this transaction or its parent row lock.
        """
        if not workflow_ids:
            return 0
        if len(workflow_ids) > ADVANCE_BATCH_SIZE:
            raise ValueError(f"Review transition batch exceeds {ADVANCE_BATCH_SIZE}")
        async with get_database() as db:
            task = await self._lock_current_parent(db, parent)
            workflows = (
                (
                    await db.execute(
                        select(AIReviewWorkflow)
                        .where(
                            AIReviewWorkflow.id.in_(workflow_ids),
                            AIReviewWorkflow.parent_task_uuid == parent,
                            AIReviewWorkflow.generation == self.generation,
                            AIReviewWorkflow.status == "open",
                        )
                        .order_by(AIReviewWorkflow.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            kinds = {workflow.kind for workflow in workflows}
            if len(kinds) > 1:
                raise ValueError("A review transition batch must contain one kind")
            if not workflows:
                return 0
            kind = next(iter(kinds))
            activity_ids = {
                workflow.activity_id for workflow in workflows if workflow.activity_id
            }
            activities = (
                {
                    item.id: item
                    for item in (
                        await db.execute(
                            select(AIWorkItem).where(AIWorkItem.id.in_(activity_ids))
                        )
                    )
                    .scalars()
                    .all()
                }
                if activity_ids
                else {}
            )
            clock = now()
            actionable = [
                workflow
                for workflow in workflows
                if (
                    workflow.policy_version != POLICY_VERSION
                    or utc(workflow.deadline_at) <= clock
                    or workflow.activity_id is None
                    or workflow.activity_id not in activities
                    or activities[workflow.activity_id].status in TERMINAL_ACTIVITIES
                )
            ]
            if not actionable:
                return 0
            keys = sorted(
                {
                    workflow.subject_id
                    if kind == "articles"
                    else int(workflow.subject_id)
                    for workflow in actionable
                }
            )
            # Acquire the entire batch's subject locks before inspecting or
            # mutating any one row. Workflow id order is not article lock order.
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
            reviews = []
            archives = []
            submissions = []
            changed = 0
            for workflow in actionable:
                changed += 1
                if workflow.policy_version != POLICY_VERSION:
                    await cancel_activity(db, workflow.activity_id)
                    finish(
                        workflow,
                        "deferred",
                        "Review policy changed; a new pass must take a fresh snapshot",
                    )
                    continue
                if utc(workflow.deadline_at) <= clock:
                    await cancel_activity(db, workflow.activity_id)
                    finish(
                        workflow,
                        "deferred",
                        "Review deadline elapsed while awaiting eligible models",
                    )
                    continue
                activity = activities.get(workflow.activity_id)
                key, row, article, action = self._subject(
                    workflow, rows, parents, canonical
                )
                if action == "archive":
                    archives.append((key, workflow))
                    continue
                if action != "review":
                    finish(workflow, action)
                    continue
                state, step = workflow.state, "author"
                if activity is not None:
                    if activity.status != "succeeded":
                        finish(
                            workflow,
                            "deferred",
                            activity.error or f"Model activity {activity.status}",
                        )
                        continue
                    result = activity.result or {}
                    route = (
                        (result.get("conversation") or {}).get("metadata") or {}
                    ).get("runtime_route") or {}
                    model = (
                        route.get("model_key")
                        or route.get("model_name")
                        or activity.route_key
                    )
                    transition = reduce_completion(
                        workflow.kind,
                        workflow.step,
                        state,
                        text=result.get("text", ""),
                        model=model,
                        input_mode=result.get("input_mode"),
                    )
                    state, step = transition.state, transition.step
                    if transition.reviewed is not None:
                        reviews.append((key, workflow, state, transition.reviewed))
                        continue
                    if transition.outcome:
                        workflow.state = state
                        finish(workflow, transition.outcome)
                        continue
                content, payload = self._request(
                    workflow, row, article, step=step, state=state
                )
                path, digest = payloads.write_request(parent, content)
                payload.update(request_path=path, request_key=digest)
                submissions.append((workflow, state, step, payload))
            if submissions:
                items = await store.submit_activities(
                    db,
                    parent_task_uuid=parent,
                    line="content_governance",
                    priority=str(getattr(task.priority, "value", task.priority)),
                    payloads=[
                        payload for _workflow, _state, _step, payload in submissions
                    ],
                )
                # Flush the activity rows once, then attach them and their new
                # states together without intermediate per-workflow UPDATEs.
                for (workflow, state, step, _payload), item in zip(submissions, items):
                    workflow.state, workflow.step = state, step
                    workflow.activity_id, workflow.updated_at = item.id, now()
                    workflow.version += 1
            if archives:
                applied = await self.policy._archive_incomplete_summaries(
                    db,
                    [(key, workflow.input_fingerprint) for key, workflow in archives],
                    parent_task_uuid=parent,
                )
                archived = {entry["summary_id"] for entry in applied.applied}
                for key, workflow in archives:
                    finish(workflow, "archived" if key in archived else "stale")
            if reviews:
                applied = await self.policy._apply_literature_results(
                    db,
                    kind,
                    [
                        (key, workflow.input_fingerprint, reviewed)
                        for key, workflow, _state, reviewed in reviews
                    ],
                    parent_task_uuid=parent,
                )
                key_name = "article_id" if kind == "articles" else "summary_id"
                decisions = {
                    entry[key_name]: entry["decision"] for entry in applied.applied
                }
                outcomes = {
                    "published": "published",
                    "publish": "published",
                    "excluded": "rejected",
                    "archive": "archived",
                    "hold": "held",
                }
                for key, workflow, state, _reviewed in reviews:
                    workflow.state = state
                    finish(workflow, outcomes.get(decisions.get(key), "stale"))
            return changed

    async def totals(self, parent: str, kind: str) -> dict[str, Any]:
        async with get_database() as db:
            drain = await db.get(AIReviewDrain, (parent, kind, self.generation))
            rows = (
                await db.execute(
                    select(AIReviewWorkflow.outcome, func.count())
                    .where(
                        AIReviewWorkflow.parent_task_uuid == parent,
                        AIReviewWorkflow.generation == self.generation,
                        AIReviewWorkflow.kind == kind,
                    )
                    .group_by(AIReviewWorkflow.outcome)
                )
            ).all()
        counts = {
            "published": 0,
            "archived": 0,
            "rejected": 0,
            "held": 0,
            "deferred": 0,
            "stale": 0,
            "waiting": 0,
            "skipped": 0,
            "cancelled": 0,
        }
        counts.update({outcome: count for outcome, count in rows if outcome})
        return {
            **counts,
            "generation": self.generation,
            "claimed": drain.admitted,
            "scanned": drain.scanned,
            "cursor": drain.cursor,
            "exhausted": drain.exhausted,
            "pending": sum(count for outcome, count in rows if outcome is None),
        }

    async def cancel_open(
        self,
        parent: str,
        *,
        only_ids: list[int] | None = None,
        terminal_owner_only: bool = False,
    ) -> None:
        async with get_database() as db:
            task = await lock_parent(db, parent, check_cancel=False)
            if task is None:
                return  # Cascading parent deletion already released the workflows.
            if terminal_owner_only and task.status not in (
                TaskStatus.CANCELLED,
                TaskStatus.FAILED,
                TaskStatus.COMPLETED,
            ):
                return
            query = select(AIReviewWorkflow).where(
                AIReviewWorkflow.parent_task_uuid == parent,
                AIReviewWorkflow.generation == self.generation,
                AIReviewWorkflow.status == "open",
            )
            if only_ids is not None:
                query = query.where(AIReviewWorkflow.id.in_(only_ids))
            rows = (
                (
                    await db.execute(
                        query.order_by(AIReviewWorkflow.id).with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                await cancel_activity(db, row.activity_id)
                finish(
                    row, "cancelled", "Owning task ended or was explicitly cancelled"
                )

    async def recover_orphans(self) -> None:
        # Terminal owners cannot retain the global open-subject guard forever.
        async with get_database() as db:
            rows = (
                await db.execute(
                    select(
                        AIReviewWorkflow.id,
                        AIReviewWorkflow.parent_task_uuid,
                        AIReviewWorkflow.generation,
                    )
                    .join(
                        Task,
                        Task.task_uuid == AIReviewWorkflow.parent_task_uuid,
                    )
                    .where(
                        AIReviewWorkflow.status == "open",
                        Task.status.in_(
                            (
                                TaskStatus.CANCELLED,
                                TaskStatus.FAILED,
                                TaskStatus.COMPLETED,
                            ),
                        ),
                    )
                    .order_by(AIReviewWorkflow.id)
                    .limit(PAGE_SIZE)
                )
            ).all()
        grouped: dict[tuple[str, int], list[int]] = {}
        for work_id, parent, generation in rows:
            grouped.setdefault((parent, generation), []).append(work_id)
        for (parent, generation), work_ids in grouped.items():
            await ReviewWorkflowRepository(
                self.policy, generation=generation
            ).cancel_open(parent, only_ids=work_ids, terminal_owner_only=True)
