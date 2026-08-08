#!/usr/bin/env python3
"""Seed only Iceland's ontology catalogue and source-series registry rows.

This scoped command is intentionally additive: it does not reconcile or
deactivate rows owned by other countries and it performs no historical fact
remaps. It is suitable for provisioning Iceland immediately before a first
source-series import. The command is read-only unless ``--apply`` is supplied.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import JSON, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sync_disease_ontology import (  # noqa: E402
    _catalogue_ids,
    _managed_mapping_rows,
    _read_standard_rows,
)
from src.core import get_database, init_app  # noqa: E402
from src.core.db_schema import (  # noqa: E402
    ensure_country_scope_for_code,
    ensure_country_scope_schema,
    ensure_disease_mapping_source_schema,
)
from src.domain import (  # noqa: E402
    Disease,
    DiseaseMapping,
    DiseaseSourceAvailability,
    DiseaseSurveillanceSeries,
    StandardDisease,
)
from src.ontology import load_disease_ontology  # noqa: E402
from src.services.crawl_pipelines.is_ import _ensure_is_country  # noqa: E402
from src.services.disease_ontology_sync_service import (  # noqa: E402
    build_disease_ontology_sync_payload,
)


CURRENT_SOURCE_IDS = {
    "SRC_IS_DOH_ANNUAL",
    "SRC_IS_DOH_STI",
    "SRC_IS_DOH_RESPIRATORY",
}


def _catalogue_rows(
    concept_ids: set[str], ontology
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    statuses = {
        concept_id: ontology.concept_detail(concept_id)["status"]
        for concept_id in ontology.concept_ids
    }
    standard_rows: list[dict[str, Any]] = []
    disease_rows: list[dict[str, Any]] = []
    for raw in _read_standard_rows():
        disease_id = str(raw.get("disease_id") or "").strip().upper()
        if disease_id not in concept_ids:
            continue
        description = str(raw.get("description") or "").strip()
        is_active = statuses.get(disease_id) != "deprecated" and (
            "deprecated duplicate" not in description.casefold()
        )
        category = str(raw.get("category") or "").strip()
        icd_10 = str(raw.get("icd_10") or "").strip() or None
        icd_11 = str(raw.get("icd_11") or "").strip() or None
        source = str(raw.get("source") or "").strip() or "Manual"
        standard_name_zh = str(raw.get("standard_name_zh") or "").strip() or None
        standard_rows.append(
            {
                "disease_id": disease_id,
                "standard_name_en": raw["standard_name_en"].strip(),
                "standard_name_zh": standard_name_zh,
                "category": category or None,
                "icd_10": icd_10,
                "icd_11": icd_11,
                "description": description or None,
                "source": source,
                "metadata": {
                    "origin": "configs/standard_diseases.csv",
                    "ontology_status": statuses.get(disease_id),
                },
                "is_active": is_active,
            }
        )
        disease_rows.append(
            {
                "name": disease_id,
                "name_en": raw["standard_name_en"].strip(),
                "category": category or "Other",
                "icd_10": icd_10,
                "icd_11": icd_11,
                "aliases": [],
                "keywords": [],
                "description": description or None,
                "metadata": {
                    "standard_name_zh": standard_name_zh,
                    "ontology_status": statuses.get(disease_id),
                },
                "is_active": is_active,
            }
        )
    missing = concept_ids - {row["disease_id"] for row in standard_rows}
    if missing:
        raise ValueError(
            "Iceland ontology concepts missing from standard catalogue: "
            + ", ".join(sorted(missing))
        )
    return standard_rows, disease_rows


async def _upsert(db, table, rows: list[dict[str, Any]], keys: list[str]) -> int:
    if not rows:
        return 0
    statement = pg_insert(table).values(rows)
    update_keys = [
        column.name
        for column in table.columns
        if column.name not in {"id", "created_at", *keys} and column.name in rows[0]
    ]
    statement = statement.on_conflict_do_update(
        index_elements=keys,
        set_={
            **{key: statement.excluded[key] for key in update_keys},
            "updated_at": func.now(),
        },
    )
    await db.execute(statement)
    return len(rows)


def _merged_metadata(statement, table):
    """Merge catalogue metadata while giving existing shared keys priority."""

    incoming = func.coalesce(
        cast(statement.excluded.metadata, JSONB),
        cast({}, JSONB),
    )
    existing = func.coalesce(cast(table.c.metadata, JSONB), cast({}, JSONB))
    return cast(incoming.op("||")(existing), JSON)


async def _insert_or_merge_standard_diseases(
    db, rows: list[dict[str, Any]]
) -> int:
    """Insert missing standards and fill gaps without redefining shared rows."""

    if not rows:
        return 0
    table = StandardDisease.__table__
    statement = pg_insert(table).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["disease_id"],
        set_={
            "standard_name_en": table.c.standard_name_en,
            "standard_name_zh": func.coalesce(
                table.c.standard_name_zh, statement.excluded.standard_name_zh
            ),
            "category": func.coalesce(table.c.category, statement.excluded.category),
            "icd_10": func.coalesce(table.c.icd_10, statement.excluded.icd_10),
            "icd_11": func.coalesce(table.c.icd_11, statement.excluded.icd_11),
            "description": func.coalesce(
                table.c.description, statement.excluded.description
            ),
            "source": table.c.source,
            "metadata": _merged_metadata(statement, table),
            "is_active": table.c.is_active,
            "updated_at": func.now(),
        },
    )
    await db.execute(statement)
    return len(rows)


async def _insert_or_merge_diseases(db, rows: list[dict[str, Any]]) -> int:
    """Provision compatibility concepts without erasing shared enrichment."""

    if not rows:
        return 0
    table = Disease.__table__
    statement = pg_insert(table).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["name"],
        set_={
            "name_en": func.coalesce(table.c.name_en, statement.excluded.name_en),
            # Category is non-null in this compatibility table.  An existing
            # classification is shared state and must remain authoritative.
            "category": table.c.category,
            "icd_10": func.coalesce(table.c.icd_10, statement.excluded.icd_10),
            "icd_11": func.coalesce(table.c.icd_11, statement.excluded.icd_11),
            # Never replace curated aliases/keywords with the empty bootstrap
            # lists used by this country-scoped provisioning command.
            "aliases": table.c.aliases,
            "keywords": table.c.keywords,
            "description": func.coalesce(
                table.c.description, statement.excluded.description
            ),
            "metadata": _merged_metadata(statement, table),
            "is_active": table.c.is_active,
            "updated_at": func.now(),
        },
    )
    await db.execute(statement)
    return len(rows)


def _iceland_mapping_rows() -> list[dict[str, Any]]:
    """Return source-scoped Iceland mappings directly from checked-in config."""

    return [
        row
        for row in _managed_mapping_rows(_catalogue_ids())
        if row["country_code"] == "IS"
    ]


def _validate_current_mapping_coverage(
    series_rows: list[dict[str, Any]], mapping_rows: list[dict[str, Any]]
) -> None:
    """Fail before mutation if a fresh DB could not project a current series."""

    mapping_keys = {
        (
            str(row.get("source_id") or ""),
            str(row.get("series_id") or ""),
            str(row.get("local_name") or ""),
            str(row.get("disease_id") or ""),
        )
        for row in mapping_rows
    }
    missing: list[str] = []
    for row in series_rows:
        source_id = str(row.get("source_system") or "")
        if source_id not in CURRENT_SOURCE_IDS:
            continue
        metadata = (
            row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        )
        local_codes = {
            str(value)
            for value in metadata.get("local_codes", [])
            if str(value).strip()
        }
        # Compatibility mappings consume the source's local code, while the
        # registry payload intentionally uses the ontology series ID as its
        # stable ``source_series_code``.  Validate against metadata.local_codes
        # to mirror SeriesObservationStore and mapping_lookup behavior.
        if not local_codes:
            local_codes.add(str(row.get("source_series_code") or ""))
        expected_prefix = (
            source_id,
            str(row.get("series_code") or ""),
        )
        disease_id = str(row.get("disease_id") or "")
        if not any(
            (expected_prefix[0], expected_prefix[1], local_code, disease_id)
            in mapping_keys
            for local_code in local_codes
        ):
            missing.append(
                str(row.get("series_code") or row.get("source_series_code"))
            )
    if missing:
        raise ValueError(
            "Iceland current series missing source-code compatibility mappings: "
            + ", ".join(sorted(missing))
        )


async def sync(*, apply: bool) -> None:
    ontology = load_disease_ontology()
    payload = build_disease_ontology_sync_payload(ontology)
    series_rows = [
        row for row in payload.surveillance_series if row["country_code"] == "IS"
    ]
    availability_rows = [
        row for row in payload.source_availability if row["country_code"] == "IS"
    ]
    concept_ids = {
        str(row["disease_id"])
        for row in series_rows
        if row.get("disease_id") is not None
    }
    standard_rows, disease_rows = _catalogue_rows(concept_ids, ontology)
    mapping_rows = _iceland_mapping_rows()
    _validate_current_mapping_coverage(series_rows, mapping_rows)

    if not apply:
        print(
            "Plan: Iceland catalogue="
            f"{len(standard_rows)} series={len(series_rows)} "
            f"availability={len(availability_rows)} mappings={len(mapping_rows)}"
        )
        return

    await init_app()
    async with get_database() as db:
        await _ensure_is_country(db)
        await ensure_country_scope_schema(db)
        await ensure_country_scope_for_code(db, "IS")
        await ensure_disease_mapping_source_schema(db)
        await _insert_or_merge_standard_diseases(db, standard_rows)
        await _insert_or_merge_diseases(db, disease_rows)
        await _upsert(
            db,
            DiseaseMapping.__table__,
            mapping_rows,
            ["disease_id", "country_code", "source_id", "local_name"],
        )
        await _upsert(
            db,
            DiseaseSurveillanceSeries.__table__,
            series_rows,
            ["series_code"],
        )
        await _upsert(
            db,
            DiseaseSourceAvailability.__table__,
            availability_rows,
            ["availability_code"],
        )
        await db.commit()

    async with get_database() as db:
        stored_series = int(
            (
                await db.execute(
                    select(func.count()).select_from(DiseaseSurveillanceSeries).where(
                        DiseaseSurveillanceSeries.country_code == "IS"
                    )
                )
            ).scalar_one()
        )
        stored_availability = int(
            (
                await db.execute(
                    select(func.count()).select_from(DiseaseSourceAvailability).where(
                        DiseaseSourceAvailability.country_code == "IS"
                    )
                )
            ).scalar_one()
        )
    if stored_series < len(series_rows) or stored_availability < len(availability_rows):
        raise RuntimeError(
            "Iceland registry verification failed: "
            f"series={stored_series}/{len(series_rows)} "
            f"availability={stored_availability}/{len(availability_rows)}"
        )
    print(
        "Applied: Iceland catalogue="
        f"{len(standard_rows)} series={stored_series} "
        f"availability={stored_availability} mappings={len(mapping_rows)}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist Iceland rows; without this flag the command is read-only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(sync(apply=args.apply))


if __name__ == "__main__":
    main()
