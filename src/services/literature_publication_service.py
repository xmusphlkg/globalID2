"""Publish the reviewed Research Radar projection into the Astro data tree.

This is intentionally a small release boundary: it exports only public
literature state, never abstracts, raw provider payloads, or private review
records.  The normal data-release pipeline remains responsible for building
and deploying the complete site.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.config import get_config
from src.core.database import get_db
from src.generation.site_data_literature import (
    attach_surveillance_evidence,
    collect_literature_export,
    write_literature_artifacts,
)
from src.services.situation_room import latest_snapshot


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DATA_ROOT = ROOT / "astro-site" / "src" / "data"


def _catalogue(output: Path) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    disease_index = output / "diseases" / "index.json"
    if not disease_index.exists():
        raise RuntimeError("Public disease catalogue is missing; run the normal site-data release first")
    diseases = json.loads(disease_index.read_text(encoding="utf-8"))
    by_id = {str(item["disease_id"]): item for item in diseases}
    coverage: dict[str, set[str]] = {}
    for disease_id in by_id:
        path = output / "diseases" / f"{disease_id.lower()}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        coverage[disease_id] = set((payload.get("country_series") or {}).keys())
    return by_id, coverage


async def export_public_research_artifacts(
    *,
    output: Path = PUBLIC_DATA_ROOT,
) -> dict[str, Any]:
    """Regenerate public Research Radar JSON from current reviewed state."""
    diseases_by_id, coverage = _catalogue(output)
    async with get_db() as db:
        payload = await collect_literature_export(
            db,
            diseases_by_id=diseases_by_id,
            surveillance_coverage=coverage,
            limit=get_config().literature.public_article_limit,
        )
    payload = attach_surveillance_evidence(
        payload,
        await latest_snapshot(),
        diseases_by_id=diseases_by_id,
    )
    write_literature_artifacts(payload, output)
    return {
        "articles": len(payload.get("articles") or []),
        "diseases": len(payload.get("disease_articles") or {}),
        "countries": len(payload.get("country_articles") or {}),
        "topics": len(payload.get("topic_articles") or {}),
        "weekly_briefs": len(payload.get("weekly_briefs") or []),
        "signal_visibility": (payload.get("surveillance_evidence") or {}).get("visibility"),
    }


__all__ = ["PUBLIC_DATA_ROOT", "export_public_research_artifacts"]
