"""Summarize quarantined source labels without hiding rejected observations."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence
from .catalogue import _text


def _quarantine_summary(quarantine: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_reason = Counter(_text(item.get("reason")) for item in quarantine)
    identities: dict[tuple[str, str, str, str], int] = Counter()
    for item in quarantine:
        identity = (
            _text(item.get("reason")),
            _text(item.get("raw_label_is") or item.get("RawDiseaseLabel")),
            _text(item.get("raw_label_en")),
            _text(item.get("icd10") or item.get("ICD10")),
        )
        identities[identity] += 1
    unmapped = [
        {
            "reason": reason,
            "raw_label_is": label_is,
            "raw_label_en": label_en,
            "icd10": icd10,
            "row_count": count,
        }
        for (reason, label_is, label_en, icd10), count in sorted(identities.items())
    ]
    return {
        "row_count": len(quarantine),
        "by_reason": dict(sorted(by_reason.items())),
        "identities": unmapped,
    }
