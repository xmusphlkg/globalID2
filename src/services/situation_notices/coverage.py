"""Public coverage tags derived from published facts and named report sections.

The source document is read only inside the exporter. No document text or
matching excerpts are returned, and an authority's location is not coverage.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from src.services.global_sources.catalogue import (
    country_catalogue,
    disease_catalogue,
    match_countries,
    match_diseases,
)


_NON_COVERAGE = re.compile(
    r"\b(?:background|historical|history|references?|bibliography|travel advice|travel recommendations?|"
    r"contact(?:s| details)?|acknowledg(?:e)?ments?|(?:who|regional|country) office|publisher)\b", re.I,
)


def _coverage_headings(text: str) -> str:
    """Use headings/contents entries, excluding ordinary background prose."""
    headings = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 200 or _NON_COVERAGE.search(line):
            continue
        markdown = bool(re.match(r"^#{1,6}\s", line))
        contents = bool(re.search(r"\.{3,}\s*\d+\s*$", line))
        # Report converters also preserve plain topic-country headings. A dash
        # in a sentence is not enough: require a short non-sentence line.
        topic_place = bool(re.search(r"\s[-–—]\s", line)) and len(line) <= 120 and not re.search(r"[.!?;]", line)
        if markdown or contents or topic_place:
            headings.append(line)
    return "\n".join(headings)


def monitoring_coverage(notice: Any, records: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Tag monitoring even when routine facts produce no map marker.

    Published alert identities (including routine records) and validated
    document reference coverage are strongest. Named titles and report sections
    extend those tags for roundups whose extraction intentionally has no alerts.
    Unknown dimensions are omitted so legacy consumers can retain their fallback.
    """
    disease_ids: set[str] = set()
    country_codes: set[str] = set()
    for record in records:
        if record.get("disease_id"):
            disease_ids.add(record["disease_id"])
        if record.get("country_code"):
            country_codes.add(record["country_code"])
    document = getattr(notice, "document", None)
    ref = getattr(document, "ref", None) or {}
    if isinstance(ref, dict):
        valid_diseases = {row["disease_id"] for row in disease_catalogue()}
        valid_countries = set(country_catalogue())
        explicit_diseases = ref.get("disease_ids")
        explicit_countries = ref.get("country_codes")
        if isinstance(explicit_diseases, list):
            disease_ids.update(value for value in explicit_diseases if isinstance(value, str) and value in valid_diseases)
        if isinstance(explicit_countries, list):
            country_codes.update(value.upper() for value in explicit_countries if isinstance(value, str) and value.upper() in valid_countries)
    title = getattr(notice, "title_en", None) or ""
    text = "\n".join((title if not _NON_COVERAGE.search(title) else "", _coverage_headings(getattr(document, "text", None) or "")))
    disease_ids.update(row["disease_id"] for row in match_diseases(text))
    country_codes.update(row["code"] for row in match_countries(text))
    return {
        **({"disease_ids": sorted(disease_ids)} if disease_ids else {}),
        **({"country_codes": sorted(country_codes)} if country_codes else {}),
    }
