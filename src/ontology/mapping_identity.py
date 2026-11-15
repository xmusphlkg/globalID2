"""Stable identities and source-label normalization for the mapping registry."""
from __future__ import annotations
import hashlib
import unicodedata
from typing import Any, Mapping

def normalize_source_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "").replace("\ufeff", ""))
    return " ".join(normalized.split()).strip().casefold()

def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""

def _category_key(source_id: str, source_code: str, definition_version: str) -> str:
    identity = f"{source_id}\x1f{source_code}\x1f{definition_version}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    safe_source = "".join(ch if ch.isalnum() else "_" for ch in source_id.upper()).strip("_")
    return f"CAT_{safe_source}_{digest}"

def _assertion_key(category_key: str, target_kind: str, target_code: str, relation: str) -> str:
    raw = f"{category_key}\x1f{target_kind}\x1f{target_code}\x1f{relation}"
    return f"MAP_{hashlib.sha256(raw.encode()).hexdigest()[:32]}"

def _source_id_for_row(row: Mapping[str, Any], source_id: str | Mapping[str, str] | None) -> str:
    if isinstance(source_id, Mapping):
        source_name = normalize_source_text(row.get("Source") or row.get("DataSource"))
        for label, resolved in source_id.items():
            if normalize_source_text(label) == source_name:
                return str(resolved).strip().upper()
        return ""
    return str(source_id or "").strip().upper()
