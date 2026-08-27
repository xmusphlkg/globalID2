"""Fail-closed, source-bounded AI review for public weekly briefs.

This is a quality-control signal, not an editorial signature.  The model sees
only fields that are already eligible for public release and is never asked to
use outside knowledge.  Raw model output and chain-of-thought are not stored.
"""

from __future__ import annotations

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


ROOT = Path(__file__).resolve().parents[2]
WEEKLY_AI_REVIEW_REGISTRY_PATH = ROOT / "configs" / "literature" / "weekly_ai_reviews.json"
WEEKLY_BRIEF_DIR = ROOT / "astro-site" / "src" / "data" / "research" / "weekly"
AI_REVIEW_PROTOCOL_VERSION = "research-weekly-ai-review.v1"
_WEEK = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_INTERNAL_SOURCE = re.compile(r"^/research/articles/[a-z0-9][a-z0-9-]*/$")
_MAX_PACKET_BYTES = 96_000
_MAX_ISSUES = 12

AI_REVIEW_ISSUE_CODES = frozenset({
    "causal_inference",
    "evidence_gap_overreach",
    "incomplete_methodology",
    "invalid_finding_provenance",
    "invalid_internal_source_reference",
    "invalid_monitoring_relation",
    "missing_bilingual_finding",
    "missing_bilingual_gap",
    "missing_cited_findings",
    "monitoring_claim_overreach",
    "risk_assessment_language",
    "unsupported_claim",
    "bilingual_mismatch",
    "content_too_large",
})
_ALLOWED_RELATIONS = {
    "exact_disease_geography", "disease_context", "historical_context", "context", "candidate",
}


class WeeklyAIReviewError(RuntimeError):
    """Stable error safe to include in task output."""


class WeeklyBriefReviewAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(name="weekly_brief_public_evidence_reviewer", temperature=0.0, max_tokens=500)

    async def process(self, **kwargs: Any) -> dict[str, Any]:
        response = await self.complete(
            prompt=kwargs["prompt"],
            system=kwargs["system"],
            use_cache=True,
            preferred_models=kwargs.get("preferred_models"),
            wait_for_model_recovery=False,
            max_attempts_per_model=1,
            max_quota_recovery_rounds=0,
            model_request_timeout_seconds=kwargs.get("timeout_seconds", 60),
        )
        return {"raw_response": response}


def _text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:maximum] if normalized else None


def _public_geographies(value: Any) -> list[Any]:
    output: list[Any] = []
    for item in list(value or [])[:20] if isinstance(value, list) else []:
        if isinstance(item, str):
            text = _text(item, 160)
            if text:
                output.append(text)
        elif isinstance(item, Mapping):
            projected = {
                key: _text(item.get(key), 160)
                for key in ("code", "country_code", "name_en", "name_zh")
                if _text(item.get(key), 160)
            }
            if projected:
                output.append(projected)
    return output


def public_review_packet(brief: Mapping[str, Any]) -> dict[str, Any]:
    """Project only the four public evidence surfaces permitted to leave the app."""
    findings = []
    for row in list(brief.get("cited_findings") or [])[:5]:
        if isinstance(row, Mapping):
            findings.append({
                key: _text(row.get(key), 4_000 if key.startswith("finding_") else 500)
                for key in (
                    "article_id", "title", "finding_en", "finding_zh", "source_url", "doi", "provenance",
                )
            })
    context = []
    for row in list(brief.get("monitoring_context") or [])[:50]:
        if isinstance(row, Mapping):
            context.append({
                **{
                    key: _text(row.get(key), 500)
                    for key in ("signal_id", "disease_id", "data_through", "relation_level")
                },
                "geographies": _public_geographies(row.get("geographies")),
            })
    gaps = []
    for row in list(brief.get("evidence_gaps") or [])[:50]:
        if isinstance(row, Mapping):
            gaps.append({
                **{
                    key: _text(row.get(key), 4_000 if key.startswith("note_") else 500)
                    for key in ("gap_id", "signal_id", "disease_id", "gap_type", "note_en", "note_zh")
                },
                "geographies": _public_geographies(row.get("geographies")),
            })
    methodology = brief.get("methodology") if isinstance(brief.get("methodology"), Mapping) else {}
    return {
        "cited_findings": findings,
        "monitoring_context": context,
        "evidence_gaps": gaps,
        "methodology": {"en": _text(methodology.get("en"), 4_000), "zh": _text(methodology.get("zh"), 4_000)},
    }


def deterministic_review_issues(packet: Mapping[str, Any]) -> list[str]:
    issues: set[str] = set()
    findings = packet.get("cited_findings") or []
    if not findings:
        issues.add("missing_cited_findings")
    for finding in findings:
        if finding.get("provenance") != "published_bilingual_structured_summary":
            issues.add("invalid_finding_provenance")
        if not _text(finding.get("finding_en"), 4000) or not _text(finding.get("finding_zh"), 4000):
            issues.add("missing_bilingual_finding")
        if not _INTERNAL_SOURCE.fullmatch(str(finding.get("source_url") or "")):
            issues.add("invalid_internal_source_reference")
    for relation in packet.get("monitoring_context") or []:
        if str(relation.get("relation_level") or "") not in _ALLOWED_RELATIONS:
            issues.add("invalid_monitoring_relation")
    for gap in packet.get("evidence_gaps") or []:
        if not _text(gap.get("note_en"), 4000) or not _text(gap.get("note_zh"), 4000):
            issues.add("missing_bilingual_gap")
    methodology = packet.get("methodology") or {}
    if not _text(methodology.get("en"), 4000) or not _text(methodology.get("zh"), 4000):
        issues.add("incomplete_methodology")
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > _MAX_PACKET_BYTES:
        issues.add("content_too_large")
    return sorted(issues)


def parse_ai_review_response(value: str) -> dict[str, Any]:
    """Accept one small exact JSON object; prose/fences/extra keys fail closed."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > 8_192:
        raise WeeklyAIReviewError("ai_review_response_invalid")
    try:
        parsed = json.loads(value.strip())
    except json.JSONDecodeError as exc:
        raise WeeklyAIReviewError("ai_review_response_invalid") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"verdict", "issue_codes"}:
        raise WeeklyAIReviewError("ai_review_schema_invalid")
    verdict = parsed.get("verdict")
    codes = parsed.get("issue_codes")
    if verdict not in {"pass", "needs_editorial_review"} or not isinstance(codes, list):
        raise WeeklyAIReviewError("ai_review_schema_invalid")
    if len(codes) > _MAX_ISSUES or any(not isinstance(code, str) for code in codes):
        raise WeeklyAIReviewError("ai_review_schema_invalid")
    normalized = list(dict.fromkeys(codes))
    if any(code not in AI_REVIEW_ISSUE_CODES for code in normalized):
        raise WeeklyAIReviewError("ai_review_unknown_issue_code")
    if (verdict == "pass" and normalized) or (verdict != "pass" and not normalized):
        raise WeeklyAIReviewError("ai_review_verdict_inconsistent")
    return {"verdict": verdict, "issue_codes": normalized}


def project_weekly_ai_review(value: Any, *, now: datetime | None = None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("verdict") != "pass":
        return None
    codes = value.get("issue_codes")
    if codes != [] or value.get("protocol_version") != AI_REVIEW_PROTOCOL_VERSION:
        return None
    reviewed_at = value.get("reviewed_at")
    try:
        parsed = datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    current = now or datetime.now(timezone.utc)
    if parsed.tzinfo is None or parsed > current + timedelta(minutes=5):
        return None
    model = _text(value.get("model"), 160)
    provider = _text(value.get("provider"), 80)
    if not model or not provider:
        return None
    return {
        "verdict": "pass",
        "issue_codes": [],
        "reviewed_at": parsed.astimezone(timezone.utc).isoformat(),
        "protocol_version": AI_REVIEW_PROTOCOL_VERSION,
        "model": model,
        "provider": provider,
    }


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
            existing_status = _valid_stored_review(existing.get("review"))
            incoming_status = _valid_stored_review(record.get("review"))
            # Concurrent disagreement resolves toward human attention. A pass
            # can never race-overwrite a valid needs-review verdict.
            if existing_status == "needs_editorial_review" and incoming_status == "ai_reviewed":
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


class WeeklyAIReviewRunner:
    def __init__(self, agent: WeeklyBriefReviewAgent | None = None) -> None:
        self.agent = agent

    async def review(
        self, brief: dict[str, Any], *, preferred_models: list[str] | None = None,
        timeout_seconds: int = 60, max_attempts: int = 2,
    ) -> dict[str, Any]:
        packet = public_review_packet(brief)
        deterministic = deterministic_review_issues(packet)
        if deterministic:
            return {"verdict": "needs_editorial_review", "issue_codes": deterministic, "model": None, "provider": None}
        agent = self.agent or WeeklyBriefReviewAgent()
        system = (
            f"Protocol {AI_REVIEW_PROTOCOL_VERSION}. Review only the supplied public JSON packet. "
            "Do not use outside knowledge, browsing, retrieval, memory, hidden context, or unstated facts. "
            "Check whether claims stay within cited findings, monitoring links are disclosed as non-causal, "
            "gaps describe catalogue coverage only, and English/Chinese meanings align. Return JSON only with "
            "exact keys verdict and issue_codes. Never return reasoning."
        )
        prompt = json.dumps({
            "task": "Bounded public weekly-brief quality review",
            "allowed_issue_codes": sorted(AI_REVIEW_ISSUE_CODES),
            "output_schema": {"verdict": "pass|needs_editorial_review", "issue_codes": "allowlisted string[]"},
            "packet": packet,
        }, ensure_ascii=False, separators=(",", ":"))
        attempts = max(1, min(int(max_attempts), 2))
        per_attempt_timeout = max(1, int(timeout_seconds) // attempts)
        last_error: Exception | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                for _ in range(attempts):
                    try:
                        response = await agent.process(
                            prompt=prompt,
                            system=system,
                            preferred_models=preferred_models or [],
                            timeout_seconds=per_attempt_timeout,
                        )
                        result = parse_ai_review_response(str(response.get("raw_response") or ""))
                        conversation = agent.get_latest_conversation() or {}
                        model = _text(conversation.get("model") or getattr(agent, "model", None), 160)
                        provider = _text(conversation.get("provider") or getattr(agent, "provider", None), 80)
                        if not model or not provider:
                            raise WeeklyAIReviewError("ai_review_model_identity_missing")
                        # Public artifacts disclose that Model Center performed
                        # the check without exposing internal route/provider ids.
                        result.update({"model": "configured-review-model", "provider": "model-center"})
                        return result
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
    apply: bool = True,
    runner: WeeklyAIReviewRunner | None = None,
) -> dict[str, Any]:
    """Review generated public brief files with bounded, idempotent writes."""
    # Local import avoids coupling the fingerprint/projector to model clients.
    from .weekly_briefs import (
        load_weekly_review_registry, project_weekly_editorial_review, weekly_brief_review_fingerprint,
    )

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
    human = load_weekly_review_registry(human_registry_path) if human_registry_path else load_weekly_review_registry()
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
            human_row = human.get(week)
            if (
                isinstance(human_row, dict)
                and human_row.get("brief_fingerprint") == fingerprint
                and project_weekly_editorial_review(human_row.get("review")) is not None
            ):
                counts["skipped"] += 1
                outcomes.append({"week": week, "status": "skipped", "reason": "human_review_precedence"})
                continue
            existing_row = existing.get(week)
            if isinstance(existing_row, dict) and existing_row.get("brief_fingerprint") == fingerprint:
                previous_status = _valid_stored_review(existing_row.get("review"))
                if previous_status:
                    counts["skipped"] += 1
                    outcomes.append({
                        "week": week, "status": "skipped",
                        "reason": f"unchanged_fingerprint_{previous_status}",
                    })
                    continue
            verdict = await reviewer.review(
                value, preferred_models=preferred_models, timeout_seconds=timeout_seconds, max_attempts=max_attempts,
            )
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
    "WeeklyAIReviewError", "WeeklyAIReviewRunner", "bound_weekly_ai_review",
    "deterministic_review_issues", "load_weekly_ai_review_registry", "parse_ai_review_response",
    "project_weekly_ai_review", "public_review_packet", "write_weekly_ai_review",
    "review_weekly_brief_files",
]
