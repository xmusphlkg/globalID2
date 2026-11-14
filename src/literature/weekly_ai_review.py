"""Fail-closed, source-bounded AI review for public weekly briefs.

This is a quality-control signal, not an editorial signature.  The model sees
only fields that are already eligible for public release and is never asked to
use outside knowledge.  Raw model output and chain-of-thought are not stored.
"""

from __future__ import annotations

from .weekly_review_contracts import (
    AI_REVIEW_PROTOCOL_VERSION,
    _INTERNAL_SOURCE,
    _MAX_PACKET_BYTES,
    _MAX_ISSUES,
    AI_REVIEW_ISSUE_CODES,
    _RUNNER_ONLY_CODES,
    MODEL_ISSUE_CODES,
    _ALLOWED_RELATIONS,
    WeeklyAIReviewError,
    WeeklyAIReviewRejected,
    _text,
    _public_geographies,
    public_review_packet,
    deterministic_review_issues,
    parse_ai_review_response,
    project_weekly_ai_review,
    sanitize_weekly_verdict,
)

import asyncio
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from src.ai.agents.base import BaseAgent
from src.ai.runtime.contracts import RoutingRequirements


ROOT = Path(__file__).resolve().parents[2]
WEEKLY_AI_REVIEW_REGISTRY_PATH = ROOT / "configs" / "literature" / "weekly_ai_reviews.json"
WEEKLY_BRIEF_DIR = ROOT / "astro-site" / "src" / "data" / "research" / "weekly"
_WEEK = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")

# Recorded by the runner when the two-model review rejects a brief; never offered to the models.






class WeeklyBriefReviewAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(name="weekly_brief_public_evidence_reviewer", temperature=0.0, max_tokens=500)

    async def process(self, **kwargs: Any) -> dict[str, Any]:
        response = await self.complete(
            prompt=kwargs["prompt"],
            system=kwargs["system"],
            use_cache=True,
            routing=RoutingRequirements(preferred_models=tuple(kwargs.get("preferred_models") or ())),
            model_request_timeout_seconds=kwargs.get("timeout_seconds", 60),
        )
        return {"raw_response": response}














def load_weekly_ai_review_registry(path: Path = WEEKLY_AI_REVIEW_REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("reviews"), list):
        return {}
    output: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in payload["reviews"]:
        if not isinstance(row, dict):
            continue
        week, fingerprint, review = row.get("week"), row.get("brief_fingerprint"), row.get("review")
        if not isinstance(week, str) or not _WEEK.fullmatch(week) or not isinstance(fingerprint, str):
            continue
        if not _FINGERPRINT.fullmatch(fingerprint) or not isinstance(review, dict):
            continue
        if week in output:
            duplicates.add(week)
        else:
            output[week] = {"brief_fingerprint": fingerprint, "review": review}
    for week in duplicates:
        output.pop(week, None)
    return output


def bound_weekly_ai_review(value: Any, brief: dict[str, Any], *, fingerprint: str, now: datetime | None = None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("brief_fingerprint") != fingerprint:
        return None
    return project_weekly_ai_review(value.get("review"), now=now)


def _registry_payload(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "reviews": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise WeeklyAIReviewError("ai_review_registry_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("reviews"), list):
        raise WeeklyAIReviewError("ai_review_registry_invalid")
    return value


def _valid_stored_review(value: Any, *, now: datetime | None = None) -> str | None:
    if project_weekly_ai_review(value, now=now) is not None:
        return "ai_reviewed"
    if not isinstance(value, Mapping) or value.get("verdict") != "needs_editorial_review":
        return None
    if value.get("protocol_version") != AI_REVIEW_PROTOCOL_VERSION:
        return None
    codes = value.get("issue_codes")
    if not isinstance(codes, list) or not codes or len(codes) > _MAX_ISSUES:
        return None
    if any(not isinstance(code, str) or code not in AI_REVIEW_ISSUE_CODES for code in codes):
        return None
    try:
        parsed = datetime.fromisoformat(str(value.get("reviewed_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    current = now or datetime.now(timezone.utc)
    return "needs_editorial_review" if parsed.tzinfo is not None and parsed <= current + timedelta(minutes=5) else None


def _stored_reviewed_at(value: Any) -> datetime | None:
    if not isinstance(value, Mapping):
        return None
    try:
        parsed = datetime.fromisoformat(str(value.get("reviewed_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def write_weekly_ai_review(
    path: Path, record: dict[str, Any], *, current_brief_path: Path | None = None,
) -> None:
    """Atomically upsert one week while holding a cross-process registry lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if current_brief_path is not None:
            from .weekly_briefs import weekly_brief_review_fingerprint

            try:
                current_brief = json.loads(current_brief_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WeeklyAIReviewError("weekly_brief_changed_during_review") from exc
            if not isinstance(current_brief, dict) or weekly_brief_review_fingerprint(current_brief) != record.get("brief_fingerprint"):
                raise WeeklyAIReviewError("weekly_brief_changed_during_review")
        payload = _registry_payload(path)
        for existing in payload["reviews"]:
            if not isinstance(existing, dict):
                continue
            if existing.get("week") != record["week"] or existing.get("brief_fingerprint") != record["brief_fingerprint"]:
                continue
            existing_review = existing.get("review")
            incoming_review = record.get("review")
            existing_status = _valid_stored_review(existing_review)
            incoming_status = _valid_stored_review(incoming_review)
            existing_at = _stored_reviewed_at(existing_review)
            incoming_at = _stored_reviewed_at(incoming_review)
            # Only a strictly newer valid review may replace an existing
            # decision. This permits scheduled AI rechecks to clear an older
            # hold without allowing a delayed concurrent response to win.
            if (
                existing_status is not None
                and (
                    incoming_status is None
                    or existing_at is None
                    or incoming_at is None
                    or incoming_at <= existing_at
                )
            ):
                return
        payload["reviews"] = [
            row for row in payload["reviews"]
            if not isinstance(row, dict) or row.get("week") != record["week"]
        ]
        payload["reviews"].append(record)
        payload["reviews"].sort(key=lambda row: str(row.get("week") or ""))
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)


WEEKLY_REVIEW_SYSTEM = (
    f"Protocol {AI_REVIEW_PROTOCOL_VERSION}. Review only the supplied public JSON packet. "
    "Do not use outside knowledge, browsing, retrieval, memory, hidden context, or unstated facts. "
    "Check whether claims stay within cited findings, monitoring links are disclosed as non-causal, "
    "gaps describe catalogue coverage only, and all supplied language meanings align. Return JSON only with "
    "exact keys verdict and issue_codes. An empty evidence_gaps array is valid; missing_bilingual_gap "
    "applies only when a present gap row lacks English or Chinese text. Never return reasoning."
)




class WeeklyAIReviewRunner:
    """Shared two-model peer review (:mod:`src.ai.peer_review`) of one public brief."""

    def __init__(self, completer: Any | None = None) -> None:
        self.completer = completer

    async def review(
        self, brief: dict[str, Any], *, preferred_models: list[str] | None = None,
        timeout_seconds: int = 120, max_attempts: int = 2,
    ) -> dict[str, Any]:
        from src.ai.peer_review import build_author_prompt, build_reviewer_prompt, peer_review, reviewer_system

        packet = public_review_packet(brief)
        deterministic = deterministic_review_issues(packet)
        if deterministic:
            return {
                "verdict": "needs_editorial_review", "issue_codes": deterministic,
                "model": None, "provider": None, "review_mode": None,
            }
        task = {
            "task": "Bounded public weekly-brief quality review",
            "allowed_issue_codes": sorted(MODEL_ISSUE_CODES),
            "packet": packet,
        }
        last_error: Exception | None = None
        try:
            from src.ai.runtime import current_parent
            async with asyncio.timeout(None if current_parent() is not None else timeout_seconds):
                for _ in range(max(1, min(int(max_attempts), 2))):
                    try:
                        if self.completer is None:
                            from src.ai.peer_review_completer import ModelCenterCompleter

                            self.completer = await ModelCenterCompleter.from_model_center(
                                line="research_weekly_brief", max_tokens=600
                            )
                        reviewed = await peer_review(
                            self.completer,
                            author_system=WEEKLY_REVIEW_SYSTEM,
                            reviewer_system=reviewer_system("public infectious-disease research weekly briefs"),
                            author_prompt=lambda notes, previous: build_author_prompt(
                                prompt_version=AI_REVIEW_PROTOCOL_VERSION,
                                task=task,
                                required_output={
                                    "verdict": "pass|needs_editorial_review",
                                    "issue_codes": "allowlisted string[]",
                                },
                                rules=["pass requires an empty issue_codes list; any problem needs at least one code."],
                                reviewer_notes=notes,
                                previous_draft=previous,
                            ),
                            reviewer_prompt=lambda draft: build_reviewer_prompt(
                                prompt_version=AI_REVIEW_PROTOCOL_VERSION,
                                task=task,
                                draft=draft,
                                rules=["Audit the verdict: a pass that misses a real issue, or a code the packet does not support, needs revise."],
                            ),
                            sanitize=sanitize_weekly_verdict,
                            usable=lambda draft: draft.get("verdict") is not None,
                        )
                        if not reviewed.published:
                            raise WeeklyAIReviewRejected("ai_review_peer_review_rejected")
                        # Public artifacts disclose that Model Center performed
                        # the check without exposing internal route/provider ids.
                        return {
                            **reviewed.payload,
                            "model": "configured-review-model",
                            "provider": "model-center",
                            "review_mode": reviewed.mode,
                        }
                    except WeeklyAIReviewRejected:
                        raise
                    except Exception as exc:  # bounded retry, then fail closed
                        last_error = exc
        except TimeoutError as exc:
            last_error = exc
        raise WeeklyAIReviewError("ai_review_model_unavailable_or_invalid") from last_error


async def review_weekly_brief_files(
    *,
    weekly_dir: Path = WEEKLY_BRIEF_DIR,
    registry_path: Path = WEEKLY_AI_REVIEW_REGISTRY_PATH,
    human_registry_path: Path | None = None,
    weeks: list[str] | None = None,
    limit: int = 2,
    preferred_models: list[str] | None = None,
    timeout_seconds: int = 60,
    max_attempts: int = 2,
    recheck_after_hours: int = 6,
    apply: bool = True,
    runner: WeeklyAIReviewRunner | None = None,
) -> dict[str, Any]:
    """Review generated public brief files with bounded, idempotent writes."""
    # Local import avoids coupling the fingerprint/projector to model clients.
    from .weekly_briefs import weekly_brief_review_fingerprint

    selected = [week for week in (weeks or []) if _WEEK.fullmatch(str(week))]
    if not selected:
        try:
            selected = sorted(
                (path.stem for path in weekly_dir.glob("*.json") if _WEEK.fullmatch(path.stem)),
                reverse=True,
            )[: max(1, min(int(limit), 8))]
        except OSError as exc:
            raise WeeklyAIReviewError("weekly_brief_directory_unreadable") from exc
    existing = load_weekly_ai_review_registry(registry_path)
    del human_registry_path  # kept for callers; human reviews no longer take precedence
    reviewer = runner or WeeklyAIReviewRunner()
    counts = {"selected": len(selected), "ai_reviewed": 0, "needs_editorial_review": 0, "skipped": 0, "failed": 0}
    outcomes: list[dict[str, Any]] = []
    for week in selected:
        path = weekly_dir / f"{week}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("week") != week:
                raise WeeklyAIReviewError("weekly_brief_invalid")
            fingerprint = weekly_brief_review_fingerprint(value)
            # Every brief goes through the shared peer review; a human registry
            # entry no longer replaces it.
            existing_row = existing.get(week)
            if isinstance(existing_row, dict) and existing_row.get("brief_fingerprint") == fingerprint:
                existing_review = existing_row.get("review")
                previous_status = _valid_stored_review(existing_review)
                reviewed_at = _stored_reviewed_at(existing_review)
                recheck_due = bool(
                    previous_status == "needs_editorial_review"
                    and reviewed_at is not None
                    and reviewed_at + timedelta(hours=max(1, int(recheck_after_hours)))
                    <= datetime.now(timezone.utc)
                )
                if previous_status and not recheck_due:
                    counts["skipped"] += 1
                    outcomes.append({
                        "week": week, "status": "skipped",
                        "reason": (
                            f"unchanged_fingerprint_{previous_status}_recheck_pending"
                            if previous_status == "needs_editorial_review"
                            else f"unchanged_fingerprint_{previous_status}"
                        ),
                    })
                    continue
            try:
                verdict = await reviewer.review(
                    value, preferred_models=preferred_models, timeout_seconds=timeout_seconds, max_attempts=max_attempts,
                )
            except WeeklyAIReviewRejected:
                # Held, not failed: saving it lets the recheck window throttle
                # retries instead of re-running the review on every pass.
                verdict = {
                    "verdict": "needs_editorial_review", "issue_codes": ["peer_review_unresolved"],
                    "model": "configured-review-model", "provider": "model-center", "review_mode": None,
                }
            reviewed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            record = {
                "week": week,
                "brief_fingerprint": fingerprint,
                "review": {
                    "verdict": verdict["verdict"],
                    "issue_codes": verdict["issue_codes"],
                    "reviewed_at": reviewed_at,
                    "protocol_version": AI_REVIEW_PROTOCOL_VERSION,
                    "model": verdict.get("model"),
                    "provider": verdict.get("provider"),
                    "review_mode": verdict.get("review_mode"),
                },
            }
            if apply:
                write_weekly_ai_review(registry_path, record, current_brief_path=path)
                existing[week] = {"brief_fingerprint": fingerprint, "review": record["review"]}
            status = "ai_reviewed" if verdict["verdict"] == "pass" else "needs_editorial_review"
            counts[status] += 1
            outcomes.append({"week": week, "status": status, "issue_codes": verdict["issue_codes"]})
        except Exception:
            # Do not persist raw provider errors or model output. A failure is
            # an unreviewed brief, never a partially trusted review.
            counts["failed"] += 1
            outcomes.append({"week": week, "status": "failed", "reason": "ai_review_failed_closed"})
    return {"mode": "weekly_brief_ai_review", "applied": apply, "counts": counts, "outcomes": outcomes}


__all__ = [
    "AI_REVIEW_ISSUE_CODES", "AI_REVIEW_PROTOCOL_VERSION", "WEEKLY_AI_REVIEW_REGISTRY_PATH",
    "WeeklyAIReviewError", "WeeklyAIReviewRejected", "WeeklyAIReviewRunner", "bound_weekly_ai_review",
    "deterministic_review_issues", "load_weekly_ai_review_registry", "parse_ai_review_response",
    "project_weekly_ai_review", "public_review_packet", "write_weekly_ai_review",
    "review_weekly_brief_files",
]
