"""Precise, versioned article invalidation within the formal-write transaction.

These markers describe complete article scope, not a public payload or a source
cursor. Unknown scope remains ``None`` so older writers force a full export.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import re
from typing import Any

_ARTICLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,47}\Z")
_KIND = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def build_literature_change_set(
    article_ids: Iterable[str], *, change_kind: str = "article",
    before_fingerprints: Mapping[str, str] | None = None,
    after_fingerprints: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a canonical marker; an empty complete scope means no work."""
    if isinstance(article_ids, (str, bytes)):
        raise ValueError("Literature article IDs must be a collection")
    ids = list(article_ids)
    if any(not isinstance(value, str) or not _ARTICLE_ID.fullmatch(value) for value in ids):
        raise ValueError("Invalid literature article ID")
    if not isinstance(change_kind, str) or not _KIND.fullmatch(change_kind):
        raise ValueError("Invalid literature change kind")
    ids = sorted(set(ids))
    for values in (before_fingerprints, after_fingerprints):
        if values is not None and (not isinstance(values, Mapping)
                or set(values) - set(ids)
                or any(not isinstance(value, str) or not _HASH.fullmatch(value) for value in values.values())):
            raise ValueError("Invalid literature change fingerprints")
    changes = []
    for article_id in ids:
        change = {"article_id": article_id, "change_kind": change_kind}
        for key, values in (("before_fingerprint", before_fingerprints), ("after_fingerprint", after_fingerprints)):
            if values is not None and article_id in values:
                change[key] = values[article_id]
        changes.append(change)
    return {"version": 1, "precise": True, "line": "literature", "article_ids": ids, "changes": changes}


def validate_literature_change_set(value: object) -> dict[str, Any]:
    """Reject incomplete or unknown contracts instead of narrowing their scope."""
    if (not isinstance(value, dict) or set(value) != {"version", "precise", "line", "article_ids", "changes"}
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("precise") is not True or value.get("line") != "literature"
            or not isinstance(value.get("article_ids"), list) or not isinstance(value.get("changes"), list)):
        raise ValueError("Invalid precise literature change set")
    ids = build_literature_change_set(value["article_ids"])["article_ids"]
    canonical = {}
    for change in value["changes"]:
        if (not isinstance(change, dict) or not {"article_id", "change_kind"} <= set(change)
                or set(change) - {"article_id", "change_kind", "before_fingerprint", "after_fingerprint"}):
            raise ValueError("Invalid literature article change")
        article_id = change["article_id"]
        built = build_literature_change_set([article_id], change_kind=change["change_kind"],
            before_fingerprints={article_id: change["before_fingerprint"]} if "before_fingerprint" in change else None,
            after_fingerprints={article_id: change["after_fingerprint"]} if "after_fingerprint" in change else None)["changes"][0]
        canonical[json.dumps(built, sort_keys=True, separators=(",", ":"))] = built
    changes = [canonical[key] for key in sorted(canonical)]
    if ids != sorted({change["article_id"] for change in changes}):
        raise ValueError("Literature article IDs do not cover their changes")
    return {"version": 1, "precise": True, "line": "literature", "article_ids": ids, "changes": changes}


def merge_literature_change_sets(values: Iterable[object]) -> dict[str, Any] | None:
    """Union selected markers; a legacy or invalid marker forces full export.

    Distinct before/after pairs are retained rather than inventing an ordering
    between concurrent transactions or treating equal fingerprints as a no-op.
    """
    changes = []
    ids: set[str] = set()
    for value in values:
        try:
            item = validate_literature_change_set(value)
        except (TypeError, ValueError):
            return None
        ids.update(item["article_ids"])
        changes.extend(item["changes"])
    return validate_literature_change_set({"version": 1, "precise": True, "line": "literature",
        "article_ids": sorted(ids), "changes": changes})


async def mark_literature_dirty(db, *, task_uuid: str, article_ids: Iterable[str] | None = None,
                                change_kind: str = "article", before_fingerprints=None,
                                after_fingerprints=None) -> None:
    """Keep facts and exact invalidation atomic; ``None`` retains coarse fallback."""
    from src.services.ingestion.stages import mark_dirty
    change_set = None if article_ids is None else build_literature_change_set(article_ids,
        change_kind=change_kind, before_fingerprints=before_fingerprints,
        after_fingerprints=after_fingerprints)
    if change_set is not None and not change_set["article_ids"]:
        return
    await mark_dirty(db, line="literature", scope_key="*", task_uuid=task_uuid, change_set=change_set)


__all__ = ["build_literature_change_set", "validate_literature_change_set",
           "merge_literature_change_sets", "mark_literature_dirty"]
