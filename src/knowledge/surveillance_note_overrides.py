"""Curator-reviewed source interpretation notes for public disease pages."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OVERRIDES_PATH = ROOT / "configs" / "disease_surveillance_note_overrides.json"

_MARKERS = {
    "en": "GlobalID source-data note:",
    "zh": "GlobalID 来源数据说明：",
}


@lru_cache(maxsize=4)
def load_surveillance_note_overrides(path: str = str(DEFAULT_OVERRIDES_PATH)) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {"schema_version": 1, "review_version": None, "notes": {}}
    with config_path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("schema_version") != 1 or not isinstance(document.get("notes"), dict):
        raise ValueError("disease surveillance note overrides must use schema_version 1")
    return document


def apply_surveillance_note_override(payload: dict[str, Any]) -> dict[str, Any]:
    """Append one stable reviewed block while preserving the knowledge note."""

    result = dict(payload)
    disease_id = str(result.get("disease_id") or "").upper()
    language = "zh" if str(result.get("language") or "").lower() == "zh" else "en"
    document = load_surveillance_note_overrides()
    entry = document.get("notes", {}).get(disease_id)
    reviewed_note = entry.get(language) if isinstance(entry, dict) else None
    if not isinstance(reviewed_note, str) or not reviewed_note.strip():
        return result

    marker = _MARKERS[language]
    current = str(result.get("surveillance_note") or "").strip()
    # Regeneration may receive an existing overlaid note through repair mode.
    # Remove only our prior terminal block, never curator/AI prose above it.
    current = re.sub(
        rf"\n\n{re.escape(marker)}\s.*\Z",
        "",
        current,
        flags=re.DOTALL,
    ).strip()
    reviewed_block = f"{marker} {reviewed_note.strip()}"
    result["surveillance_note"] = (
        f"{current}\n\n{reviewed_block}" if current else reviewed_block
    )
    result["metadata"] = {
        **(result.get("metadata") or {}),
        "source_data_note_review_version": document.get("review_version"),
        "source_data_note_reviewed_at": document.get("reviewed_at"),
    }
    return result


__all__ = ["apply_surveillance_note_override", "load_surveillance_note_overrides"]
