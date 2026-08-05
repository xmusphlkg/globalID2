"""Fail-closed lookup for legacy country-scoped disease mappings.

The legacy importers still need integer ``diseases.id`` values while the
series-first migration is in progress.  Historically each importer built a
plain dictionary and silently let the last database row win when two active
aliases normalized to the same text.  That makes query order part of disease
semantics.  This module centralizes normalization and refuses ambiguity.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MappingConflictError(ValueError):
    """Raised when one active source label points at multiple concepts."""


WILDCARD_SOURCE_ID = "*"


def normalize_mapping_key(value: object) -> str:
    """Return a stable Unicode, whitespace, and case normalized lookup key."""

    normalized = unicodedata.normalize("NFKC", str(value or "").replace("\ufeff", ""))
    return " ".join(normalized.split()).strip().casefold()


def normalize_source_id(value: object) -> str:
    """Normalize an optional source identifier to the compatibility wildcard."""

    source_id = str(value or "").strip()
    return (
        source_id.upper()
        if source_id and source_id != WILDCARD_SOURCE_ID
        else WILDCARD_SOURCE_ID
    )


def build_mapping_lookup(
    rows: Iterable[Any], *, country_code: str, source_id: str | None = None
) -> dict[str, int]:
    """Build a source-aware lookup and reject every ambiguous resolution.

    Rows may use the historical three-column shape or append ``source_id`` and
    ``series_id``.  With an explicit source, an exact-source mapping overrides
    the wildcard.  Without one, different source-specific targets are
    intentionally ambiguous and ingestion fails closed.
    """

    candidates: dict[str, dict[str, dict[int, tuple[str, str]]]] = {}
    for row in rows:
        local_name, disease_db_id, disease_code = row[:3]
        key = normalize_mapping_key(local_name)
        if not key:
            continue
        numeric_id = int(disease_db_id)
        row_source_id = normalize_source_id(row[3] if len(row) > 3 else None)
        candidates.setdefault(key, {}).setdefault(row_source_id, {})[numeric_id] = (
            str(disease_code),
            str(local_name),
        )

    # A conflict inside any individual source (including wildcard) is a broken
    # mapping set even when the current explicit-source request would not use it.
    for key, by_source in candidates.items():
        for candidate_source, targets in by_source.items():
            if len(targets) > 1:
                descriptions = ", ".join(
                    f"{code} via {raw!r}" for code, raw in targets.values()
                )
                raise MappingConflictError(
                    "Ambiguous active disease mapping for "
                    f"{country_code.upper()} source {candidate_source!r} "
                    f"label normalized={key!r}: {descriptions}"
                )

    requested_source = normalize_source_id(source_id) if source_id is not None else None
    mapping: dict[str, int] = {}
    for key, by_source in candidates.items():
        if requested_source is not None:
            selected = by_source.get(requested_source) or by_source.get(WILDCARD_SOURCE_ID)
            if selected:
                mapping[key] = next(iter(selected))
            continue

        all_targets = {
            numeric_id for targets in by_source.values() for numeric_id in targets
        }
        if len(all_targets) > 1:
            descriptions = ", ".join(
                f"{candidate_source}:{next(iter(targets.values()))[0]}"
                for candidate_source, targets in sorted(by_source.items())
            )
            raise MappingConflictError(
                "Source is required for ambiguous active disease mapping for "
                f"{country_code.upper()} label normalized={key!r}: {descriptions}"
            )
        if all_targets:
            mapping[key] = next(iter(all_targets))
    return mapping


async def load_country_mapping_dict(
    db: AsyncSession, country_code: str, *, source_id: str | None = None
) -> dict[str, int]:
    """Load active mappings deterministically and fail closed on ambiguity.

    The column probe keeps ingestion readable during an additive deployment in
    which application code may start before ``source_id`` has been added.  Such
    legacy rows are treated as wildcard mappings.
    """

    column_result = await db.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'disease_mappings'
                  AND column_name = 'source_id'
            )
            """
        )
    )
    source_column_exists = bool(column_result.scalar())
    source_expression = "COALESCE(dm.source_id, '*')" if source_column_exists else "'*'"

    result = await db.execute(
        text(
            f"""
            SELECT dm.local_name, d.id, dm.disease_id, {source_expression} AS source_id
            FROM disease_mappings dm
            JOIN diseases d ON dm.disease_id = d.name
            WHERE dm.country_code = :code
              AND dm.is_active = true
              AND d.is_active = true
            ORDER BY dm.priority DESC, dm.local_name, dm.disease_id
            """
        ),
        {"code": country_code.upper()},
    )
    return build_mapping_lookup(
        result, country_code=country_code, source_id=source_id
    )


__all__ = [
    "MappingConflictError",
    "WILDCARD_SOURCE_ID",
    "build_mapping_lookup",
    "load_country_mapping_dict",
    "normalize_mapping_key",
    "normalize_source_id",
]
