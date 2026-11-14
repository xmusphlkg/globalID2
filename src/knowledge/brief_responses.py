"""Pure knowledge brief response parsing and repair diagnostics."""
from __future__ import annotations

import json
import re
from typing import Any
from .evidence import EvidenceManifest


def parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Some otherwise-valid providers append a short natural-language
        # acknowledgement after the requested object.  A greedy regex
        # joins that acknowledgement (or a second object) into invalid
        # JSON.  Decode one complete object instead, accepting only the
        # first structurally valid object in the response.
        decoder = json.JSONDecoder()
        parsed = None
        for match in re.finditer(r"\{", text):
            try:
                candidate, _ = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
        if parsed is None:
            raise
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object")
    return parsed



def field(payload: dict[str, Any], key: str) -> str | None:
    if key in payload and payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())



def citation_failure_fields(
    failures: list[str],
    *,
    target_sections: list[str],
) -> list[str]:
    allowed = set(target_sections)
    fields: list[str] = []
    for failure in failures:
        field, separator, _detail = str(failure).partition(":")
        field = field.strip()
        if separator and field in allowed and field not in fields:
            fields.append(field)
    return fields



def repairable_quality_failures(
    assessment: Any,
    *,
    target_sections: list[str],
    evidence_manifest: EvidenceManifest,
) -> list[dict[str, str]]:
    """Return target-field quality gaps that the supplied evidence can repair."""
    supported = {
        field
        for fragment in evidence_manifest.fragments
        for field in fragment.supported_sections
    }
    failures: list[dict[str, str]] = []
    for field in target_sections:
        field_assessment = assessment.fields.get(field)
        if field_assessment is None or field_assessment.available:
            continue
        if field not in supported:
            continue
        failures.append(
            {
                "field": field,
                "status": field_assessment.status,
                "reason": field_assessment.reason or "quality_gate_rejected",
            }
        )
    return failures
