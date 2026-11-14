"""Pure knowledge brief prompt construction."""
from __future__ import annotations

import json
import re
from typing import Any
from .evidence import EvidenceManifest


def system_prompt(_language: str, *, protocol_version: int) -> str:
    return (
        f"Knowledge-profile protocol {protocol_version}; "
        "schema version 4. Produce a conservative public-health profile using only the supplied "
        "evidence_manifest and catalogue context—never outside knowledge. A fragment may support "
        "only its supported_sections; inherited fragments remain limited to their allowed scope. "
        "If evidence does not support a field, return null for that field and never write an absence "
        "explanation or placeholder. Respect profile applicability: classification entities need "
        "scope boundaries; occupational, injury, poisoning, and violence entities need exposure "
        "mechanisms rather than invented infection language. Do not provide diagnosis, treatment, "
        "dosing, or personal advice. Paraphrase rather than copying source passages.\n"
        "Epidemiology must describe the target disease/entity itself: occurrence, burden, "
        "distribution, outbreaks, affected settings, or surveillance-relevant distribution. Do not "
        "use complications, opportunistic infections, co-infections, or comorbid conditions as the "
        "main epidemiology unless the target entity is explicitly that condition. If evidence is "
        "indirect, state only directly supported disease-level facts and leave unsupported details "
        "null.\n"
        "surveillance_note is required when the evidence_manifest contains surveillance, reporting, "
        "outbreak, monitoring, case definition, public-health response, source-data interpretation, "
        "or country/source-series interpretation signals. If supported, write 2-4 concise sentences "
        "explaining how this disease/entity should be interpreted in surveillance or public-health "
        "data. Return null only when no such evidence is present.\n"
        "Citations are mandatory in every non-null field. Use only sequential citation_ref markers "
        "such as [1] or [1][2], immediately after the supported claim. Never cite database IDs.\n"
        "Write fluent scholarly prose in the payload's output_language. Return JSON only with "
        "exactly these keys: "
        "brief, definition, clinical_features, epidemiology, transmission, prevention, "
        "surveillance_note, risk_groups. Every key must be present; never omit a key. For each "
        "key listed in target_sections, return substantive cited prose when the evidence supports it; "
        "use null only when the evidence does not support that field. In particular, do not leave a "
        "supported required_fields entry null or omit it. Values are substantive strings or null. Prefer concise "
        "2-4 sentence sections; a brief may contain 2-5 sentences. For prevention, risk_groups, "
        "and surveillance_note, avoid one-sentence minimal answers when evidence supports more "
        "detail. Every key outside target_sections must be null; do not expand a narrow repair "
        "into a full profile."
    )



def citation_repair_prompt(
    *,
    prompt_payload: dict[str, Any],
    previous_response: str,
    failures: list[str],
) -> str:
    repair_payload = {
        "output_language": prompt_payload.get("output_language"),
        "target_sections": prompt_payload.get("target_sections"),
        "evidence_manifest": prompt_payload.get("evidence_manifest"),
        "failures": failures,
        "previous_json": previous_response,
    }
    return (
        "Repair the previous JSON using this validation payload. Rewrite an invalid field only "
        "from fragments that explicitly support it, otherwise set it to null. Do not attach a new "
        "citation to an unchanged unsupported claim. Return all eight keys as JSON only.\n"
        f"{json.dumps(repair_payload, ensure_ascii=False, separators=(',', ':'))}"
    )



def quality_repair_prompt(
    *,
    prompt_payload: dict[str, Any],
    failures: list[dict[str, str]],
) -> str:
    failure_fields = {
        str(item.get("field") or "").strip()
        for item in failures
        if str(item.get("field") or "").strip()
    }
    manifest = prompt_payload.get("evidence_manifest")
    fragments = manifest.get("fragments") if isinstance(manifest, dict) else []
    targeted_fragments = [
        fragment
        for fragment in fragments
        if isinstance(fragment, dict)
        and failure_fields.intersection(
            str(section) for section in fragment.get("supported_sections") or []
        )
    ]
    repair_payload = {
        "output_language": prompt_payload.get("output_language"),
        "quality_failures": failures,
        "evidence_manifest": {
            "target_sections": sorted(failure_fields),
            "fragments": targeted_fragments,
        },
    }
    return (
        "Repair only the fields named in quality_failures. Each named field has explicit "
        "support in this minimal evidence_manifest and must become substantive cited prose in "
        "the output language. Use only supporting fragments, and set a field to null if it "
        "cannot be supported exactly. Return a JSON object containing only named failure fields.\n"
        f"{json.dumps(repair_payload, ensure_ascii=False, separators=(',', ':'))}"
    )



def json_format_repair_prompt(
    *,
    previous_response: str,
    language: str,
    target_sections: list[str],
) -> str:
    return (
        "Convert the supplied model response into one valid JSON object with exactly these keys: "
        "brief, definition, clinical_features, epidemiology, transmission, prevention, "
        "surveillance_note, risk_groups. Preserve wording and citation markers for target_sections "
        "only; do not add facts. All non-target keys must be null. Use null for missing values and "
        "output JSON only. "
        f"Language: {language}. Target sections: {json.dumps(target_sections)}. Response: {previous_response}"
    )
