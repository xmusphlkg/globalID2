"""Bounded public notice input for RSS, frozen with the Situation generation."""
from __future__ import annotations

from typing import Any, Iterable

FEED_RECORD_LIMIT = 200
_FIELDS = (
    "alert_id", "outbreak_id", "notice_id", "source_id", "source_label_en", "source_label_zh",
    "title_en", "url", "disease_id", "disease_slug", "disease_label", "disease_name_en",
    "disease_name_zh", "country_code", "country_name_en", "country_name_zh", "admin_area",
    "status", "published_date", "summary_en", "summary_zh", "summary_fr",
    "host_type", "host_species", "count_unit",
)


def build_feed_payload(records: Iterable[dict[str, Any]], *, generated_at: str, as_of: str) -> dict[str, Any]:
    """Keep the newest public event per alert identity; never copy private text.

    Entity feeds use their existing complete country/disease shards. This small
    global window avoids fetching every outbreak timeline for one feed request.
    The renderer limits each feed to 100 entries.
    """
    ordered = sorted(
        (record for record in records if record.get("status") != "routine" and record.get("alert_id")),
        key=lambda record: (str(record.get("published_date") or ""), str(record["alert_id"])),
        reverse=True,
    )
    alerts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in ordered:
        identity = str(record["alert_id"])
        if identity in seen:
            continue
        seen.add(identity)
        alerts.append({key: record[key] for key in _FIELDS if key in record})
        if len(alerts) >= FEED_RECORD_LIMIT:
            break
    return {
        "schema_version": "gids.situation_alerts.v1",
        "generated_at": generated_at,
        "as_of": as_of,
        "alerts": alerts,
    }
