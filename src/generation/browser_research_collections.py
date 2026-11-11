"""Complete, compact reviewed-paper collections for browser Ask scopes."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.generation.literature_visualizations import _facet_slug
from src.generation.site_data_writer import remove_stale_json_files, write_compact_json


# Keep this public boundary aligned with compactResearchArticle in the browser.
PUBLIC_ARTICLE_FIELDS = (
    "article_id", "slug", "title", "doi", "pmid", "detail_available", "content_tier",
    "journal", "publisher", "authors", "study_type", "published_at", "publication_week",
    "open_access_status", "open_access_url", "peer_review_status", "discovery_score",
    "diseases", "countries", "topics", "related_surveillance", "pathogens", "pathogen_types",
    "populations", "source_kind", "historical_baseline", "publication_date_label_en",
    "publication_date_label_zh", "publication_date_label_fr", "why_it_matters_en",
    "why_it_matters_zh", "why_it_matters_fr", "summary",
)
_LIST_FIELDS = {"authors", "diseases", "countries", "topics", "related_surveillance",
                "pathogens", "pathogen_types", "populations"}


def compact_public_research_article(article: dict[str, Any]) -> dict[str, Any]:
    result = {key: article[key] for key in PUBLIC_ARTICLE_FIELDS if key in article}
    for key in _LIST_FIELDS:
        result[key] = article.get(key) or []
    result["summary"] = article.get("summary") or {}
    return result


def publish_browser_research_collections(payload: dict[str, Any], directory: Path) -> dict[str, int]:
    """Reconcile scopes from the complete canonical reviewed evidence stream.

    Never merge the separate preprint, bibliographic or title-estimate streams.
    Recompute membership to propagate removed relations, withdrawals and deletes.
    """
    groups: dict[str, dict[str, list]] = {
        "diseases": defaultdict(list), "countries": defaultdict(list), "topics": defaultdict(list),
    }
    for article in payload.get("articles") or []:
        # Curated historical seeds in the canonical stream predate this field.
        if (article.get("editorial_status") not in (None, "published")
                or article.get("peer_review_status") == "preprint"
                or (article.get("peer_review_status") != "peer_reviewed"
                    and article.get("source_kind") != "historical_seed")
                or article.get("detail_available") is False
                or article.get("integrity_status") in {"retracted", "expression_of_concern", "withdrawn"}):
            continue
        compact = compact_public_research_article(article)
        scopes = {
            "diseases": {str(d.get("disease_id") or "").lower() for d in article.get("diseases") or []},
            "countries": {str(c.get("code") or "").lower() for c in article.get("countries") or []},
            "topics": {_facet_slug(str(t.get("name") or "")) for t in article.get("topics") or []},
        }
        for kind, keys in scopes.items():
            for key in sorted(keys):
                if key and not re.fullmatch(r"[a-z0-9_-]+", key):
                    raise ValueError(f"Invalid research collection scope: {kind}/{key}")
                if key:
                    groups[kind][key].append(compact)
    stats = {"written": 0, "unchanged": 0, "removed": 0, "scopes": 0, "bytes": 0}
    for kind, collections in groups.items():
        scope_dir = directory / kind
        scope_dir.mkdir(parents=True, exist_ok=True)
        for key, articles in sorted(collections.items()):
            path = scope_dir / f"{key}.json"
            changed = write_compact_json(path, {"articles": articles, "count": len(articles)})
            stats["written" if changed else "unchanged"] += 1
            stats["scopes"] += 1
            stats["bytes"] += path.stat().st_size
        stats["removed"] += remove_stale_json_files(scope_dir, {f"{key}.json" for key in collections})
    return stats
