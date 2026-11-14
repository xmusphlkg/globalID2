"""Public weekly-review projections and validation; registry locking stays with its writer."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


AI_REVIEW_PROTOCOL_VERSION = "research-weekly-ai-review.v1"



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
    "missing_french_finding",
    "missing_french_gap",
    "french_mismatch",
    "content_too_large",
    "peer_review_unresolved",
})



_RUNNER_ONLY_CODES = frozenset({"peer_review_unresolved"})



MODEL_ISSUE_CODES = AI_REVIEW_ISSUE_CODES - _RUNNER_ONLY_CODES



_ALLOWED_RELATIONS = {
    "exact_disease_geography", "disease_context", "historical_context", "context", "candidate",
}



class WeeklyAIReviewError(RuntimeError):
    """Stable error safe to include in task output."""



class WeeklyAIReviewRejected(WeeklyAIReviewError):
    """The peer review ran and did not accept a verdict: a judgement, not an outage."""



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
            projected = {
                key: _text(row.get(key), 4_000 if key.startswith("finding_") else 500)
                for key in (
                    "article_id", "title", "finding_en", "finding_zh", "source_url", "doi", "provenance",
                )
            }
            if row.get("finding_fr"):
                projected["finding_fr"] = _text(row.get("finding_fr"), 4_000)
            findings.append(projected)
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
            projected = {
                **{
                    key: _text(row.get(key), 4_000 if key.startswith("note_") else 500)
                    for key in ("gap_id", "signal_id", "disease_id", "gap_type", "note_en", "note_zh")
                },
                "geographies": _public_geographies(row.get("geographies")),
            }
            if row.get("note_fr"):
                projected["note_fr"] = _text(row.get("note_fr"), 4_000)
            gaps.append(projected)
    methodology = brief.get("methodology") if isinstance(brief.get("methodology"), Mapping) else {}
    result = {
        "cited_findings": findings,
        "monitoring_context": context,
        "evidence_gaps": gaps,
        "methodology": {"en": _text(methodology.get("en"), 4_000), "zh": _text(methodology.get("zh"), 4_000)},
    }
    if methodology.get("fr"):
        result["methodology"]["fr"] = _text(methodology.get("fr"), 4_000)
    return result



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
        if "finding_fr" in finding and not _text(finding.get("finding_fr"), 4000):
            issues.add("missing_french_finding")
        if not _INTERNAL_SOURCE.fullmatch(str(finding.get("source_url") or "")):
            issues.add("invalid_internal_source_reference")
    for relation in packet.get("monitoring_context") or []:
        if str(relation.get("relation_level") or "") not in _ALLOWED_RELATIONS:
            issues.add("invalid_monitoring_relation")
    for gap in packet.get("evidence_gaps") or []:
        if not _text(gap.get("note_en"), 4000) or not _text(gap.get("note_zh"), 4000):
            issues.add("missing_bilingual_gap")
        if "note_fr" in gap and not _text(gap.get("note_fr"), 4000):
            issues.add("missing_french_gap")
    methodology = packet.get("methodology") or {}
    if not _text(methodology.get("en"), 4000) or not _text(methodology.get("zh"), 4000):
        issues.add("incomplete_methodology")
    if "fr" in methodology and not _text(methodology.get("fr"), 4000):
        issues.add("incomplete_methodology")
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > _MAX_PACKET_BYTES:
        issues.add("content_too_large")
    return sorted(issues)



def parse_ai_review_response(value: str) -> dict[str, Any]:
    """Accept one small exact JSON object, optionally in one JSON code fence."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > 8_192:
        raise WeeklyAIReviewError("ai_review_response_invalid")
    payload = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", payload, flags=re.IGNORECASE | re.DOTALL)
    if fenced is not None:
        payload = fenced.group(1).strip()
    try:
        parsed = json.loads(payload)
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



def sanitize_weekly_verdict(draft: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Guardrail for both peer-review roles: allowlisted codes, consistent verdict."""

    notes: list[str] = []
    verdict = draft.get("verdict") if draft.get("verdict") in {"pass", "needs_editorial_review"} else None
    if verdict is None:
        notes.append(f"guardrail: invalid verdict {str(draft.get('verdict'))[:40]}")
    raw_codes = draft.get("issue_codes") if isinstance(draft.get("issue_codes"), list) else []
    codes: list[str] = []
    for code in raw_codes[:_MAX_ISSUES]:
        if isinstance(code, str) and code in MODEL_ISSUE_CODES and code not in codes:
            codes.append(code)
        else:
            notes.append(f"guardrail: dropped unknown issue code {str(code)[:40]}")
    if (verdict == "pass" and codes) or (verdict == "needs_editorial_review" and not codes):
        notes.append("guardrail: verdict and issue_codes are inconsistent")
        verdict = None
    return {"verdict": verdict, "issue_codes": codes}, notes
