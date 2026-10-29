"""Normalized event record builder shared by every authority adapter."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .catalogue import SOURCE_REGISTRY, compose_chinese_title, iso_week

SUMMARY_LIMIT = 320
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_html_fragment(value: str | None) -> str:
    """Strip markup from an agency fragment and collapse whitespace."""

    if not value:
        return ""
    # ``html.unescape`` also covers numeric entities such as ``&#8217;`` that
    # WordPress emits in rendered titles.
    text = html.unescape(_TAG.sub(" ", str(value))).replace("\xa0", " ")
    return _WS.sub(" ", text).strip()


def truncate_summary(value: str | None, limit: int = SUMMARY_LIMIT) -> str | None:
    text = clean_html_fragment(value)
    if not text:
        return None
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return clipped


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def build_event(
    *,
    source_id: str,
    native_id: str,
    title_en: str,
    url: str,
    published: date | None,
    diseases: Iterable[dict[str, Any]],
    countries: Iterable[dict[str, str]],
    record_type: str,
    summary_en: str | None = None,
    updated_at_source: str | None = None,
    keep_summary: bool = True,
    document_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one normalized international-authority event.

    ``keep_summary`` reflects the source's redistribution posture recorded in
    :data:`~src.services.global_sources.catalogue.SOURCE_REGISTRY`: sources with
    unverified terms never carry body text, only metadata and a link.
    """

    diseases = list(diseases)
    countries = list(countries)
    registry = SOURCE_REGISTRY.get(source_id, {})
    event: dict[str, Any] = {
        "event_id": f"{source_id}:{native_id}",
        "source_id": source_id,
        "native_id": native_id,
        "record_type": record_type,
        "title_en": title_en,
        "title_zh": compose_chinese_title(diseases, countries, source_id, published),
        "summary_en": truncate_summary(summary_en) if keep_summary else None,
        "summary_zh": None,
        "url": url,
        "source_label_en": (registry.get("label") or {}).get("en"),
        "source_label_zh": (registry.get("label") or {}).get("zh"),
        "agency_en": (registry.get("agency") or {}).get("en"),
        "agency_zh": (registry.get("agency") or {}).get("zh"),
        "language": "en",
        "published_date": published.isoformat() if published else None,
        "updated_at_source": updated_at_source,
        "diseases": [
            {
                "disease_id": row["disease_id"],
                "name_en": row["name_en"],
                "name_zh": row.get("name_zh"),
                "slug": row.get("slug") or "",
            }
            for row in diseases
        ],
        "countries": [
            {"code": row["code"], "name_en": row["name_en"], "name_zh": row.get("name_zh")}
            for row in countries
        ],
        "retrieved_at": utc_now().isoformat(),
        #: Where the full report lives; see ``situation_notices.documents``.
        "document_ref": document_ref or {},
    }
    if published:
        event.update(iso_week(published))
    else:
        event.update({"iso_year": None, "iso_week": None, "iso_week_key": None})
    event["fingerprint"] = hashlib.sha1(
        "|".join(
            [
                source_id,
                native_id,
                (published.isoformat() if published else ""),
                ",".join(sorted(row["disease_id"] for row in diseases)),
                ",".join(sorted(row["code"] for row in countries)),
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    return event
