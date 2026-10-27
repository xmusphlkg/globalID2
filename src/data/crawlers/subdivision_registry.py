"""Read the shared official-label to ISO-subdivision registry."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=None)
def source_label_codes(parent_country_code: str) -> dict[str, str]:
    """Map an upstream region label to its child ISO code when registered."""

    path = ROOT / "configs/subdivision_registry.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    jurisdictions = payload.get("jurisdictions", payload)
    if not isinstance(jurisdictions, dict):
        return {}
    labels: dict[str, str] = {}
    for code, record in jurisdictions.items():
        if (
            not isinstance(record, dict)
            or str(record.get("parent_country_code") or "").upper()
            != parent_country_code.upper()
        ):
            continue
        for key in ("source_label", "name_en", "name_local"):
            label = str(record.get(key) or "").strip()
            if label:
                labels[label.casefold()] = str(code)
    return labels


def geography_key(parent_country_code: str, label: str) -> str:
    """Resolve a row geography to a stable parent or child jurisdiction key."""

    normalized = " ".join(str(label or "").split()).casefold()
    if normalized in {"総数", "totalt", "national", "total"}:
        return f"country:{parent_country_code.upper()}:national"
    child_code = source_label_codes(parent_country_code).get(normalized)
    if child_code:
        return f"country:{child_code}:national"
    # Retain an identifiable source geography during registry onboarding so
    # data is never mistaken for the national aggregate.
    return f"country:{parent_country_code.upper()}:source-region:{normalized.replace(' ', '-')[:80]}"


def source_label_for_code(parent_country_code: str, child_country_code: str) -> str | None:
    """Resolve a child jurisdiction code to its upstream row label."""

    path = ROOT / "configs/subdivision_registry.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    jurisdictions = payload.get("jurisdictions", payload) if isinstance(payload, dict) else {}
    record = jurisdictions.get(child_country_code.upper()) if isinstance(jurisdictions, dict) else None
    if (
        isinstance(record, dict)
        and str(record.get("parent_country_code") or "").upper() == parent_country_code.upper()
    ):
        value = str(record.get("source_label") or "").strip()
        return value or None
    return None
