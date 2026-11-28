"""One file per country, disease and calendar window.

Country generation is the only writer. Disease catalogue entries point at those
same files, so a country update does not rebuild other countries into a
disease-wide blob.
"""
from __future__ import annotations

from pathlib import Path


def relative_is_cell(relative: str, *, country: str | None = None, disease: str | None = None) -> bool:
    """Accept only cells/<country>/<disease>/<window>.<format>."""

    parts = Path(relative).parts
    if len(parts) != 4 or parts[0] != "cells" or ".." in parts:
        return False
    if country is not None and parts[1] != country:
        return False
    if disease is not None and parts[2] != disease:
        return False
    return True


def index_parts(parts: list[dict]) -> dict[str, dict]:
    """Key parts so two diseases in one country do not share a window id."""

    indexed: dict[str, dict] = {}
    for part in parts or []:
        disease = part.get("disease_id")
        window = part.get("id")
        key = f"{disease}/{window}" if disease else str(window)
        indexed[key] = part
    return indexed


def parts_for_disease(existing_parts: dict[str, dict], disease_id: str) -> dict[str, dict]:
    """Return one disease's prior cells keyed by calendar-window id.

    Unit tests pass the country task's own parts back without going through
    the manifest index. Parts that do not name a disease stay usable for a
    single-disease rebuild.
    """

    disease_key = str(disease_id).lower()
    matched: dict[str, dict] = {}
    unscoped: dict[str, dict] = {}
    for part in existing_parts.values():
        owner = str(part.get("disease_id") or "")
        sample = next(iter((part.get("files") or {}).values()), {})
        pieces = Path(str(sample.get("relative_path") or "")).parts
        if not owner and len(pieces) == 4 and pieces[0] == "cells":
            owner = pieces[2]
        window_id = str(part.get("id") or "")
        if owner.lower() == disease_key:
            matched[window_id] = part
        elif not owner:
            unscoped[window_id] = part
    return matched or unscoped


def annotate_country_part(part: dict, *, disease_id: str, country_code: str) -> dict:
    """Label a country-page cell with its disease, leaving the window id intact."""

    calendar = str(part.get("label") or part.get("id") or "")
    prefix = f"{disease_id} · "
    calendar = calendar.removeprefix(prefix)
    return {
        **part,
        "label": f"{disease_id} · {calendar}",
        "disease_id": disease_id,
        "country_code": country_code,
    }


def assemble_disease_parts(country_entries: list[dict], disease_id: str) -> list[dict]:
    """Point a disease catalogue at the cells its countries already wrote."""

    prefix = f"{disease_id} · "
    assembled: list[dict] = []
    for entry in country_entries:
        code = str(entry.get("code") or "").upper()
        for part in entry.get("parts") or []:
            if str(part.get("disease_id") or "") != disease_id:
                continue
            label = str(part.get("label") or "")
            calendar = label.removeprefix(prefix)
            assembled.append({
                **part,
                "label": f"{code} · {calendar}",
                "files": {
                    name: dict(file_meta)
                    for name, file_meta in (part.get("files") or {}).items()
                },
            })
    return assembled


def sum_disease_counts(country_entries: list[dict], disease_id: str) -> dict[str, int]:
    """Add per-country cell totals. Series counts are distinct inside each country."""

    totals = {
        "record_count": 0,
        "projection_record_count": 0,
        "source_observation_count": 0,
        "source_series_count": 0,
    }
    for entry in country_entries:
        counts = (entry.get("disease_counts") or {}).get(disease_id) or {}
        for key in totals:
            totals[key] += int(counts.get(key) or 0)
    return totals
