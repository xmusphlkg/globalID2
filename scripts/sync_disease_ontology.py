#!/usr/bin/env python3
"""Synchronize the disease ontology and migrate known legacy concept facts.

The command is dry-run by default.  ``--apply`` creates the additive ontology
tables, upserts the CSV catalogue and managed mappings, seeds the validated JSON
registry, and performs only pre-declared fact remaps after collision checks.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.core.disease_mutation_lock import (  # noqa: E402
    acquire_disease_data_mutation_lock,
)
from src.core.db_schema import (  # noqa: E402
    ensure_country_scope_for_code,
    ensure_country_scope_schema,
    ensure_disease_mapping_source_schema,
)
from src.domain import (  # noqa: E402
    Base,
    Disease,
    DiseaseConceptAssignment,
    DiseaseConceptRelation,
    DiseaseMapping,
    DiseaseSourceAvailability,
    DiseaseSurveillanceSeries,
    DiseaseTaxonomyEdge,
    DiseaseTaxonomyNode,
    StandardDisease,
)
from src.ontology import load_disease_ontology  # noqa: E402
from src.data.processors.mapping_lookup import (  # noqa: E402
    build_mapping_lookup,
    normalize_mapping_key,
)
from src.services.disease_ontology_sync_service import (  # noqa: E402
    build_disease_ontology_sync_payload,
)

STANDARD_CSV = ROOT / "configs" / "standard_diseases.csv"
MAPPING_DIR = ROOT / "configs" / "mapping"
TRANSITION_CSV = ROOT / "configs" / "disease_mapping_transitions.csv"
LEGACY_INACTIVE_IDS = frozenset({"D012", "D067", "D160", "D176", "D179"})
BR_NTRA_REPAIR_REASON = (
    "remove legacy NTRA row-count projection from work-related mental disorder; "
    "NTRA is a trachoma survey whose positive-case metric requires source reingestion"
)


@dataclass(frozen=True)
class FactRemap:
    old_id: str
    new_id: str
    reason: str
    country_code: str | None = None
    source_id: str | None = None
    mapping_local_name: str | None = None
    evidence_field: str | None = None
    evidence_value: str | None = None
    action: str = "remap_legacy"
    # Compatibility-only diagnostic field.  The migration query never uses
    # this fuzzy pattern; source-scoped remaps use exact structured evidence.
    raw_label_pattern: str | None = None


@dataclass(frozen=True)
class MappingTransition:
    country_code: str
    source_id: str
    local_name: str
    old_id: str
    new_id: str
    action: str
    evidence_field: str | None
    evidence_value: str | None
    reason: str


@dataclass(frozen=True)
class SeriesGeographyRemap:
    series_pattern: str
    old_key: str
    new_key: str
    reason: str
    source_system: str | None = None
    evidence_fields: tuple[str, ...] = ()
    evidence_values: tuple[str, ...] = ()


def _read_mapping_transitions() -> tuple[MappingTransition, ...]:
    allowed_actions = {"remap_legacy", "remap_and_reingest", "source_reingest"}
    transitions: list[MappingTransition] = []
    seen: set[tuple[str, str, str]] = set()
    with TRANSITION_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for line_number, raw in enumerate(csv.DictReader(handle), start=2):
            action = str(raw.get("action") or "").strip()
            if action not in allowed_actions:
                raise ValueError(
                    f"{TRANSITION_CSV}:{line_number}: invalid action {action!r}"
                )
            transition = MappingTransition(
                country_code=str(raw.get("country_code") or "").strip().upper(),
                source_id=str(raw.get("source_id") or "*").strip().upper() or "*",
                local_name=str(raw.get("local_name") or "").strip(),
                old_id=str(raw.get("old_disease_id") or "").strip().upper(),
                new_id=str(raw.get("new_disease_id") or "").strip().upper(),
                action=action,
                evidence_field=str(raw.get("evidence_field") or "").strip() or None,
                evidence_value=str(raw.get("evidence_value") or "").strip() or None,
                reason=str(raw.get("reason") or "").strip(),
            )
            if not all(
                (
                    transition.country_code,
                    transition.local_name,
                    transition.old_id,
                    transition.new_id,
                    transition.reason,
                )
            ):
                raise ValueError(
                    f"{TRANSITION_CSV}:{line_number}: incomplete transition"
                )
            if transition.action.startswith("remap") and not (
                transition.evidence_field and transition.evidence_value
            ):
                raise ValueError(
                    f"{TRANSITION_CSV}:{line_number}: remap requires exact evidence"
                )
            key = (
                transition.country_code,
                normalize_mapping_key(transition.local_name),
                transition.source_id,
            )
            if key in seen:
                raise ValueError(
                    f"{TRANSITION_CSV}:{line_number}: duplicate transition key {key}"
                )
            seen.add(key)
            transitions.append(transition)
    return tuple(transitions)


MAPPING_TRANSITIONS = _read_mapping_transitions()


def _source_evidence_registry() -> dict[str, dict[str, Any]]:
    """Return exact legacy source fingerprints declared by the Registry.

    ``disease_records`` predates Registry source IDs, so migrations bridge the
    two layers with exact, versioned ``data_source`` values.  A source-scoped
    remap is refused when that bridge is absent instead of silently falling
    back to country-and-label matching.
    """

    document = load_disease_ontology().to_dict()
    return {
        str(source["id"]): {
            "country_code": str(source["country_code"]),
            "data_sources": tuple(
                str(value).strip()
                for value in source.get("legacy_data_sources", [])
                if str(value).strip()
            ),
        }
        for source in document["sources"]
    }


SOURCE_EVIDENCE_BY_ID = _source_evidence_registry()


def _source_evidence_values(source_id: str | None) -> tuple[str, ...]:
    if not source_id or source_id == "*":
        return ()
    source = SOURCE_EVIDENCE_BY_ID.get(source_id)
    if not source or not source["data_sources"]:
        raise ValueError(
            f"Registry source {source_id} lacks exact legacy_data_sources evidence"
        )
    return tuple(str(value).casefold() for value in source["data_sources"])


WHOLE_CONCEPT_REMAPS = (
    FactRemap("D067", "D007", "deprecated exact hepatitis A duplicate"),
    FactRemap("D012", "D071", "legacy unspecified hepatitis label"),
    FactRemap("D160", "D115", "legacy VTEC/STEC duplicate"),
    FactRemap("D179", "D085", "legacy toxoplasmosis duplicate"),
)


# Every source-scoped UPDATE is generated from a versioned declaration with an
# exact JSON field/value predicate.  D012 is already handled as a whole-concept
# duplicate and is therefore not emitted a second time for CN.
FACT_REMAPS = WHOLE_CONCEPT_REMAPS + tuple(
    FactRemap(
        old_id=item.old_id,
        new_id=item.new_id,
        reason=item.reason,
        country_code=item.country_code,
        source_id=item.source_id,
        mapping_local_name=item.local_name,
        evidence_field=item.evidence_field,
        evidence_value=item.evidence_value,
        action=item.action,
        raw_label_pattern=f"%{item.evidence_value}%" if item.evidence_value else None,
    )
    for item in MAPPING_TRANSITIONS
    if item.action in {"remap_legacy", "remap_and_reingest"}
    and not (item.old_id == "D012" and item.new_id == "D071")
)

SERIES_GEOGRAPHY_REMAPS = (
    SeriesGeographyRemap(
        series_pattern="SER_CN_%",
        old_key="country:CN:source-area:china",
        new_key="country:CN:national",
        reason="China in the CN national reports is the national geography",
    ),
    SeriesGeographyRemap(
        series_pattern="SER_US_%",
        source_system="SRC_US_NNDSS",
        old_key="country:US:national",
        new_key="source:SRC_US_NNDSS:reporting-area:total",
        evidence_fields=("ReportingArea", "Reporting Area"),
        evidence_values=("TOTAL",),
        reason=(
            "NNDSS TOTAL is a source-wide reporting aggregate, not the "
            "US-resident national population"
        ),
    ),
)


def _read_standard_rows() -> list[dict[str, str]]:
    with STANDARD_CSV.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _catalogue_ids() -> set[str]:
    return {
        str(row.get("disease_id") or "").strip().upper()
        for row in _read_standard_rows()
        if str(row.get("disease_id") or "").strip()
    }


def _split_aliases(*values: object) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in str(value or "").split("|"):
            normalized = item.strip()
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def _managed_mapping_rows(managed_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(MAPPING_DIR.glob("*.csv")):
        scope_code = path.stem.upper()
        if scope_code == "EN":
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                disease_id = str(raw.get("disease_id") or "").strip().upper()
                if disease_id not in managed_ids:
                    continue
                local_name = str(raw.get("local_name") or "").strip()
                metadata = {
                    "origin": str(path.relative_to(ROOT)),
                    "local_code": str(raw.get("local_code") or "").strip(),
                    "notes": str(raw.get("notes") or "").strip(),
                }
                source_id = str(raw.get("source_id") or "").strip().upper() or "*"
                series_id = str(raw.get("series_id") or "").strip() or None
                candidates = []
                if local_name:
                    candidates.append((local_name, True, False, 100))
                for alias in _split_aliases(raw.get("local_code"), raw.get("aliases")):
                    if alias != local_name:
                        candidates.append((alias, False, True, 50))
                for name, is_primary, is_alias, priority in candidates:
                    rows.append(
                        {
                            "disease_id": disease_id,
                            "country_code": scope_code,
                            "local_name": name,
                            "source_id": source_id,
                            "series_id": series_id,
                            "is_primary": is_primary,
                            "is_alias": is_alias,
                            "priority": priority,
                            "usage_count": 0,
                            "confidence_score": 1.0,
                            "category": str(raw.get("category") or "").strip() or None,
                            "source": str(raw.get("data_source") or "").strip()
                            or "Disease ontology mapping",
                            "metadata": metadata,
                            "is_active": True,
                        }
                    )
    deduplicated: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            row["disease_id"],
            row["country_code"],
            row["source_id"],
            row["local_name"],
        )
        previous = deduplicated.get(key)
        if previous is None or row["priority"] > previous["priority"]:
            deduplicated[key] = row
        elif row["priority"] == previous["priority"] and row["is_primary"]:
            deduplicated[key] = row
    return [deduplicated[key] for key in sorted(deduplicated)]


def build_plan() -> dict[str, Any]:
    ontology = load_disease_ontology()
    document = ontology.to_dict()
    payload = build_disease_ontology_sync_payload(ontology)
    # Country CSV files are the authoritative compatibility mapping layer,
    # including concepts that have not yet received rich ontology facets.
    managed_ids = _catalogue_ids()
    mapping_rows = _managed_mapping_rows(managed_ids)
    return {
        "mode": "dry_run",
        "registry_id": document["registry_id"],
        "schema_version": document["schema_version"],
        "release_version": document.get("release_version"),
        "catalogue_rows": len(_read_standard_rows()),
        "managed_mapping_rows": len(mapping_rows),
        "managed_mapping_scopes": sorted({row["country_code"] for row in mapping_rows}),
        "ontology_rows": payload.summary(),
        "mapping_transitions": [asdict(item) for item in MAPPING_TRANSITIONS],
        "fact_remaps": [asdict(item) for item in FACT_REMAPS],
        "series_geography_remaps": [asdict(item) for item in SERIES_GEOGRAPHY_REMAPS],
        "semantic_repairs": [
            {
                "country_code": "BR",
                "old_disease_id": "D193",
                "source_code": "NTRA",
                "action": "remove_invalid_legacy_projection",
                "reason": BR_NTRA_REPAIR_REASON,
            }
        ],
    }


def _declared_transition(
    *, country_code: str, old_id: str, new_id: str
) -> MappingTransition | None:
    for item in MAPPING_TRANSITIONS:
        if (
            item.country_code == country_code.upper()
            and item.old_id == old_id
            and item.new_id == new_id
        ):
            return item
    return None


def _mapping_target_changes(
    existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Compare active DB semantics with the authoritative source-aware config."""

    incoming_by_label: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in incoming_rows:
        key = (
            str(row["country_code"]).upper(),
            normalize_mapping_key(row["local_name"]),
        )
        incoming_by_label.setdefault(key, []).append(row)

    changes: dict[tuple[int, str, str], dict[str, Any]] = {}
    for existing in existing_rows:
        country_code = str(existing["country_code"]).upper()
        old_id = str(existing["disease_id"]).upper()
        old_source = str(existing.get("source_id") or "*").strip().upper() or "*"
        key = (country_code, normalize_mapping_key(existing["local_name"]))
        for incoming in incoming_by_label.get(key, []):
            incoming_source = (
                str(incoming.get("source_id") or "*").strip().upper() or "*"
            )
            if old_source != "*" and incoming_source not in {"*", old_source}:
                continue
            new_id = str(incoming["disease_id"]).upper()
            if new_id == old_id:
                continue
            declaration = _declared_transition(
                country_code=country_code, old_id=old_id, new_id=new_id
            )
            change_key = (int(existing["id"]), new_id, incoming_source)
            changes[change_key] = {
                "existing_mapping_id": int(existing["id"]),
                "country_code": country_code,
                "existing_source_id": old_source,
                "source_id": incoming_source,
                "local_name": str(incoming["local_name"]),
                "old_disease_id": old_id,
                "new_disease_id": new_id,
                "declared": declaration is not None,
                "migration_action": declaration.action if declaration else None,
            }
    return [changes[key] for key in sorted(changes)]


def _transition_configuration_errors() -> list[str]:
    configured: set[tuple[str, str, str]] = set()
    for path in sorted(MAPPING_DIR.glob("*.csv")):
        if path.stem.casefold() == "en":
            continue
        country_code = path.stem.upper()
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                configured.add(
                    (
                        country_code,
                        normalize_mapping_key(row.get("local_name")),
                        str(row.get("disease_id") or "").strip().upper(),
                    )
                )
    errors = []
    for item in MAPPING_TRANSITIONS:
        key = (
            item.country_code,
            normalize_mapping_key(item.local_name),
            item.new_id,
        )
        if key not in configured:
            errors.append(
                "declared transition does not match current mapping config: "
                f"{item.country_code}:{item.local_name}:{item.old_id}->{item.new_id}"
            )
        if item.source_id != "*":
            source = SOURCE_EVIDENCE_BY_ID.get(item.source_id)
            if source is None:
                errors.append(
                    f"declared transition references unknown source: {item.source_id}"
                )
            elif source["country_code"] != item.country_code:
                errors.append(
                    "declared transition source-country mismatch: "
                    f"{item.source_id}={source['country_code']} vs {item.country_code}"
                )
            elif item.action.startswith("remap") and not source["data_sources"]:
                errors.append(
                    "source-scoped remap lacks exact legacy source evidence: "
                    f"{item.source_id}"
                )
    return errors


async def _load_active_mapping_rows_for_preflight(db) -> list[dict[str, Any]]:
    source_column_exists = bool((await db.execute(text("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'disease_mappings'
                          AND column_name = 'source_id'
                    )
                    """))).scalar())
    source_expression = "COALESCE(source_id, '*')" if source_column_exists else "'*'"
    rows = (await db.execute(text(f"""
                SELECT id, country_code, local_name, disease_id,
                       {source_expression} AS source_id, metadata
                FROM disease_mappings
                WHERE is_active = true
                ORDER BY country_code, local_name, disease_id
                """))).mappings().all()
    return [dict(row) for row in rows]


async def _preflight_series_geography_remap(
    db, remap: SeriesGeographyRemap
) -> dict[str, Any]:
    params = _series_geography_params(remap)
    where = _series_geography_filter_sql(remap, alias="old_observation")
    selected = int(
        (
            await db.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM disease_series_observations old_observation
                    JOIN disease_surveillance_series old_series
                      ON old_series.series_code = old_observation.series_code
                    WHERE {where}
                    """),
                params,
            )
        ).scalar()
        or 0
    )
    collisions = int(
        (
            await db.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM disease_series_observations old_observation
                    JOIN disease_surveillance_series old_series
                      ON old_series.series_code = old_observation.series_code
                    JOIN disease_series_observations new_observation
                      ON new_observation.time = old_observation.time
                     AND new_observation.series_code = old_observation.series_code
                     AND new_observation.dimension_key = old_observation.dimension_key
                     AND new_observation.geography_key = :new_key
                    WHERE {where}
                    """),
                params,
            )
        ).scalar()
        or 0
    )
    return {
        **asdict(remap),
        "selected": selected,
        "collisions": collisions,
        "errors": [f"target_fact_key_collisions={collisions}"] if collisions else [],
        "status": "blocked" if collisions else "ready",
    }


def _series_geography_params(remap: SeriesGeographyRemap) -> dict[str, Any]:
    return {
        "series_pattern": remap.series_pattern,
        "old_key": remap.old_key,
        "new_key": remap.new_key,
        "source_system": remap.source_system,
        "evidence_values": [value.casefold() for value in remap.evidence_values],
    }


def _series_geography_filter_sql(remap: SeriesGeographyRemap, *, alias: str) -> str:
    """Return the exact, source-aware selector for a declared geography move."""

    clauses = [
        f"{alias}.series_code LIKE :series_pattern",
        f"{alias}.geography_key = :old_key",
    ]
    if remap.source_system:
        clauses.append("old_series.source_system = :source_system")
    if remap.evidence_fields or remap.evidence_values:
        if not remap.evidence_fields or not remap.evidence_values:
            raise ValueError(
                "Series geography evidence requires both fields and values: "
                f"{remap.old_key}->{remap.new_key}"
            )
        field_expressions = [
            f"NULLIF(btrim(COALESCE({alias}.raw_data ->> "
            f"'{field.replace(chr(39), chr(39) * 2)}', '')), '')"
            for field in remap.evidence_fields
        ]
        evidence_expression = "COALESCE(" + ", ".join(field_expressions + ["''"]) + ")"
        clauses.append(f"lower({evidence_expression}) = ANY(:evidence_values)")
    return " AND ".join(clauses)


async def preflight_sync() -> dict[str, Any]:
    """Perform the real DB migration preview before any schema or data write."""

    plan = build_plan()
    incoming_mappings = _managed_mapping_rows(_catalogue_ids())
    configuration_errors = _transition_configuration_errors()
    awaitable_errors: list[str] = []

    async with get_db() as db:
        existing_mappings = await _load_active_mapping_rows_for_preflight(db)
        mapping_changes = _mapping_target_changes(existing_mappings, incoming_mappings)
        mapping_deactivations = _mapping_deactivation_plan(
            existing_mappings, incoming_mappings
        )
        undeclared_changes = [item for item in mapping_changes if not item["declared"]]
        if undeclared_changes:
            awaitable_errors.append(
                f"undeclared_mapping_target_changes={len(undeclared_changes)}"
            )

        fact_remaps = []
        for remap in FACT_REMAPS:
            fact_remaps.append(await _preflight_fact_remap(db, remap))
        geography_remaps = []
        for remap in SERIES_GEOGRAPHY_REMAPS:
            geography_remaps.append(await _preflight_series_geography_remap(db, remap))
        semantic_repairs = [await _preflight_br_ntra_legacy_projection(db)]

        legacy_totals = (await db.execute(text("""
                    SELECT COUNT(*) AS observations,
                           COALESCE(SUM(cases), 0) AS cases
                    FROM disease_records
                    """))).mappings().one()
        series_totals = (await db.execute(text("""
                    SELECT COUNT(*) AS observations,
                           COUNT(*) FILTER (WHERE suppressed) AS suppressed,
                           COALESCE(SUM(value), 0) AS value_sum
                    FROM disease_series_observations
                    """))).mappings().one()

    remap_errors = [
        f"{item['country_code'] or '*'}:{item['old_id']}->{item['new_id']}: "
        + "; ".join(item["errors"])
        for item in fact_remaps
        if item["errors"]
    ]
    geography_errors = [
        f"{item['old_key']}->{item['new_key']}: " + "; ".join(item["errors"])
        for item in geography_remaps
        if item["errors"]
    ]
    semantic_repair_errors = [
        f"{item['country_code']}:{item['source_code']}: " + "; ".join(item["errors"])
        for item in semantic_repairs
        if item["errors"]
    ]
    errors = (
        configuration_errors
        + awaitable_errors
        + remap_errors
        + geography_errors
        + semantic_repair_errors
    )
    plan.update(
        {
            "mode": "database_preflight",
            "status": "blocked" if errors else "ready",
            "errors": errors,
            "database_snapshot": {
                "legacy_observations": int(legacy_totals["observations"] or 0),
                "legacy_cases": int(legacy_totals["cases"] or 0),
                "series_observations": int(series_totals["observations"] or 0),
                "series_suppressed": int(series_totals["suppressed"] or 0),
                "series_value_sum": float(series_totals["value_sum"] or 0),
            },
            "mapping_preflight": {
                "active_rows": len(existing_mappings),
                "target_changes": mapping_changes,
                "undeclared_target_changes": undeclared_changes,
                "deactivation_count": len(mapping_deactivations),
                "deactivation_reason_counts": {
                    reason: sum(
                        1 for item in mapping_deactivations if item["reason"] == reason
                    )
                    for reason in sorted(
                        {item["reason"] for item in mapping_deactivations}
                    )
                },
                "deactivations": mapping_deactivations,
                "declared_reingestion_plans": [
                    asdict(item)
                    for item in MAPPING_TRANSITIONS
                    if item.action in {"source_reingest", "remap_and_reingest"}
                ],
            },
            "fact_remaps": fact_remaps,
            "series_geography_remaps": geography_remaps,
            "semantic_repairs": semantic_repairs,
        }
    )
    return plan


async def _upsert_rows(db, table, rows: list[dict[str, Any]], keys: list[str]) -> int:
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


async def _sync_catalogue(db, ontology) -> int:
    statuses = {
        concept_id: ontology.concept_detail(concept_id)["status"]
        for concept_id in ontology.concept_ids
    }
    standard_rows: list[dict[str, Any]] = []
    disease_rows: list[dict[str, Any]] = []
    for raw in _read_standard_rows():
        disease_id = raw["disease_id"].strip().upper()
        description = str(raw.get("description") or "").strip()
        is_deprecated = statuses.get(disease_id) == "deprecated" or (
            "deprecated duplicate" in description.casefold()
        )
        is_active = not is_deprecated
        standard_rows.append(
            {
                "disease_id": disease_id,
                "standard_name_en": raw["standard_name_en"].strip(),
                "standard_name_zh": str(raw.get("standard_name_zh") or "").strip()
                or None,
                "category": str(raw.get("category") or "").strip() or None,
                "icd_10": str(raw.get("icd_10") or "").strip() or None,
                "icd_11": str(raw.get("icd_11") or "").strip() or None,
                "description": description or None,
                "source": str(raw.get("source") or "").strip() or "Manual",
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
                "category": str(raw.get("category") or "").strip() or "Other",
                "icd_10": str(raw.get("icd_10") or "").strip() or None,
                "icd_11": str(raw.get("icd_11") or "").strip() or None,
                "aliases": [],
                "keywords": [],
                "description": description or None,
                "metadata": {
                    "standard_name_zh": str(raw.get("standard_name_zh") or "").strip()
                    or None,
                    "ontology_status": statuses.get(disease_id),
                },
                "is_active": is_active,
            }
        )

    await _upsert_rows(db, StandardDisease.__table__, standard_rows, ["disease_id"])
    await _upsert_rows(db, Disease.__table__, disease_rows, ["name"])
    await db.execute(
        text("""
            UPDATE standard_diseases
            SET is_active = false,
                metadata = (
                    COALESCE(metadata, '{}'::json)::jsonb ||
                    jsonb_build_object('replaced_by', CASE disease_id
                        WHEN 'D012' THEN 'D071'
                        WHEN 'D067' THEN 'D007'
                        WHEN 'D160' THEN 'D115'
                        WHEN 'D176' THEN 'D145'
                        WHEN 'D179' THEN 'D085'
                    END)
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE disease_id = ANY(:legacy_ids)
            """),
        {"legacy_ids": sorted(LEGACY_INACTIVE_IDS)},
    )
    await db.execute(
        text("""
            UPDATE diseases
            SET is_active = false, updated_at = CURRENT_TIMESTAMP
            WHERE name = ANY(:legacy_ids)
            """),
        {"legacy_ids": sorted(LEGACY_INACTIVE_IDS)},
    )
    return len(standard_rows)


def _mapping_deactivation_plan(
    existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Explain every active mapping that authoritative reconciliation will stop.

    A registry-owned row disappears when it is removed from CSV.  A manual or
    bootstrap row is retained unless its normalized label conflicts with the
    current authoritative CSV target.
    """

    incoming: dict[tuple[str, str, str], str] = {}
    for row in incoming_rows:
        key = (
            str(row["country_code"]).upper(),
            str(row.get("source_id") or "*").strip().upper(),
            normalize_mapping_key(row["local_name"]),
        )
        previous = incoming.setdefault(key, str(row["disease_id"]))
        if previous != str(row["disease_id"]):
            raise RuntimeError(
                f"Authoritative mapping conflict for {key}: "
                f"{previous} vs {row['disease_id']}"
            )

    incoming_by_label: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in incoming_rows:
        label_key = (
            str(row["country_code"]).upper(),
            normalize_mapping_key(row["local_name"]),
        )
        incoming_by_label.setdefault(label_key, []).append(row)

    plan: list[dict[str, Any]] = []
    for row in existing_rows:
        country_code = str(row["country_code"]).upper()
        source_id = str(row.get("source_id") or "*").strip().upper()
        normalized_label = normalize_mapping_key(row["local_name"])
        key = (
            country_code,
            source_id,
            normalized_label,
        )
        expected = incoming.get(key)
        label_candidates = incoming_by_label.get((country_code, normalized_label), [])
        metadata = _json_object(row.get("metadata"))
        origin = str(metadata.get("origin") or "")
        registry_owned = origin.startswith("configs/mapping/")
        conflicts = expected is not None and expected != str(row["disease_id"])
        removed_from_registry = registry_owned and expected is None
        wildcard_shadow_conflict = (
            source_id == "*"
            and expected is None
            and any(
                str(candidate.get("source_id") or "*").strip().upper() != "*"
                and str(candidate["disease_id"]) != str(row["disease_id"])
                and _declared_transition(
                    country_code=country_code,
                    old_id=str(row["disease_id"]),
                    new_id=str(candidate["disease_id"]),
                )
                is not None
                for candidate in label_candidates
            )
        )
        if not (conflicts or removed_from_registry or wildcard_shadow_conflict):
            continue

        replacements = sorted(
            {
                (
                    str(candidate.get("source_id") or "*").strip().upper(),
                    str(candidate["disease_id"]),
                    str(candidate["local_name"]),
                )
                for candidate in label_candidates
            }
        )
        if conflicts:
            reason = "target_conflict"
        elif wildcard_shadow_conflict:
            reason = "wildcard_shadow_conflict"
        elif not replacements:
            reason = "removed_from_registry"
        else:
            source_changed = any(item[0] != source_id for item in replacements)
            target_changed = any(
                item[1] != str(row["disease_id"]) for item in replacements
            )
            if source_changed and target_changed:
                reason = "target_and_source_rekey"
            elif source_changed:
                reason = "source_rekey"
            elif target_changed:
                reason = "target_rekey"
            else:
                reason = "registry_identity_rekey"
        plan.append(
            {
                "mapping_id": int(row["id"]),
                "country_code": country_code,
                "source_id": source_id,
                "local_name": str(row["local_name"]),
                "normalized_local_name": normalized_label,
                "old_disease_id": str(row["disease_id"]),
                "origin": origin or None,
                "reason": reason,
                "replacement_candidates": [
                    {
                        "source_id": replacement_source,
                        "disease_id": replacement_disease,
                        "local_name": replacement_name,
                    }
                    for replacement_source, replacement_disease, replacement_name in replacements
                ],
            }
        )
    return sorted(plan, key=lambda item: item["mapping_id"])


def _mapping_rows_to_deactivate(
    existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> list[int]:
    return [
        item["mapping_id"]
        for item in _mapping_deactivation_plan(existing_rows, incoming_rows)
    ]


async def _sync_mappings(db, managed_ids: set[str]) -> tuple[int, list[int]]:
    rows = _managed_mapping_rows(managed_ids)
    scopes = sorted({row["country_code"] for row in rows})
    for scope_code in scopes:
        await ensure_country_scope_for_code(db, scope_code)

    existing = (
        (
            await db.execute(
                text("""
                SELECT id, country_code, local_name, disease_id,
                       source_id, series_id, metadata
                FROM disease_mappings
                WHERE is_active = true
                  AND country_code = ANY(:scopes)
                """),
                {"scopes": scopes},
            )
        )
        .mappings()
        .all()
    )
    stale_ids = _mapping_rows_to_deactivate([dict(row) for row in existing], rows)
    if stale_ids:
        await db.execute(
            text("""
                UPDATE disease_mappings
                SET is_active = false, updated_at = CURRENT_TIMESTAMP
                WHERE id = ANY(:stale_ids)
                """),
            {"stale_ids": stale_ids},
        )
    await db.execute(
        text("""
            UPDATE disease_mappings
            SET is_active = false, updated_at = CURRENT_TIMESTAMP
            WHERE disease_id = ANY(:legacy_ids)
            """),
        {"legacy_ids": sorted(LEGACY_INACTIVE_IDS)},
    )
    upserted = await _upsert_rows(
        db,
        DiseaseMapping.__table__,
        rows,
        ["disease_id", "country_code", "source_id", "local_name"],
    )
    return upserted, stale_ids


def _registry_owned_rows(
    rows: list[dict[str, Any]], registry_id: str
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        copy = dict(row)
        metadata = _json_object(copy.get("metadata"))
        metadata["registry_id"] = registry_id
        copy["metadata"] = metadata
        result.append(copy)
    return result


async def _reconcile_ontology_tables(db, payload, registry_id: str) -> dict[str, int]:
    """Deactivate registry-owned rows removed from the authoritative config."""

    node_keys = [row["node_code"] for row in payload.taxonomy_nodes]
    edge_keys = [
        "\x1f".join(
            (row["parent_node_code"], row["child_node_code"], row["relation_type"])
        )
        for row in payload.taxonomy_edges
    ]
    assignment_keys = [
        "\x1f".join((row["disease_id"], row["node_code"]))
        for row in payload.concept_assignments
    ]
    relation_keys = [
        "\x1f".join(
            (
                row["subject_disease_id"],
                row["relation_type"],
                row["object_disease_id"],
            )
        )
        for row in payload.concept_relations
    ]
    series_keys = [row["series_code"] for row in payload.surveillance_series]
    availability_keys = [
        row["availability_code"] for row in payload.source_availability
    ]
    source_ids = sorted(
        {
            str(row["source_system"])
            for row in payload.surveillance_series + payload.source_availability
        }
    )

    statements = {
        "deactivated_taxonomy_nodes": (
            """
            UPDATE disease_taxonomy_nodes
            SET is_active = false, updated_at = CURRENT_TIMESTAMP
            WHERE taxonomy_code = :registry_id
              AND NOT (node_code = ANY(:keys))
              AND is_active = true
            """,
            node_keys,
        ),
        "deactivated_taxonomy_edges": (
            """
            UPDATE disease_taxonomy_edges edge
            SET is_active = false, updated_at = CURRENT_TIMESTAMP
            WHERE edge.is_active = true
              AND EXISTS (
                  SELECT 1 FROM disease_taxonomy_nodes node
                  WHERE node.node_code = edge.parent_node_code
                    AND node.taxonomy_code = :registry_id
              )
              AND NOT (
                  concat_ws(E'\\x1f', edge.parent_node_code,
                            edge.child_node_code, edge.relation_type) = ANY(:keys)
              )
            """,
            edge_keys,
        ),
        "deprecated_concept_assignments": (
            """
            UPDATE disease_concept_assignments
            SET assertion_status = 'deprecated', updated_at = CURRENT_TIMESTAMP
            WHERE asserted_by = :registry_id
              AND assertion_status <> 'deprecated'
              AND NOT (
                  concat_ws(E'\\x1f', disease_id, node_code) = ANY(:keys)
              )
            """,
            assignment_keys,
        ),
        "deprecated_concept_relations": (
            """
            UPDATE disease_concept_relations
            SET assertion_status = 'deprecated', updated_at = CURRENT_TIMESTAMP
            WHERE asserted_by = :registry_id
              AND assertion_status <> 'deprecated'
              AND NOT (
                  concat_ws(E'\\x1f', subject_disease_id, relation_type,
                            object_disease_id) = ANY(:keys)
              )
            """,
            relation_keys,
        ),
        "deactivated_surveillance_series": (
            """
            UPDATE disease_surveillance_series
            SET is_active = false,
                availability_status = 'discontinued',
                metadata = (
                    COALESCE(metadata, '{}'::json)::jsonb ||
                    jsonb_build_object('registry_reconciled_as_removed', true)
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE is_active = true
              AND (
                  COALESCE(metadata::jsonb ->> 'registry_id', '') = :registry_id
                  OR definition_version LIKE (:registry_id || ':%')
              )
              AND NOT (series_code = ANY(:keys))
            """,
            series_keys,
        ),
        "deactivated_source_availability": (
            """
            UPDATE disease_source_availability
            SET is_active = false,
                metadata = (
                    COALESCE(metadata, '{}'::json)::jsonb ||
                    jsonb_build_object('registry_reconciled_as_removed', true)
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE is_active = true
              AND source_system = ANY(:source_ids)
              AND (
                  COALESCE(metadata::jsonb ->> 'registry_id', '') = :registry_id
                  OR metadata::jsonb ? 'release_version'
              )
              AND NOT (availability_code = ANY(:keys))
            """,
            availability_keys,
        ),
    }
    counts: dict[str, int] = {}
    for name, (statement, keys) in statements.items():
        result = await db.execute(
            text(statement),
            {
                "registry_id": registry_id,
                "keys": keys,
                "source_ids": source_ids,
            },
        )
        counts[name] = int(result.rowcount or 0)
    return counts


async def _sync_ontology_tables(db, payload, registry_id: str) -> dict[str, int]:
    taxonomy_nodes = _registry_owned_rows(payload.taxonomy_nodes, registry_id)
    taxonomy_edges = _registry_owned_rows(payload.taxonomy_edges, registry_id)
    concept_assignments = _registry_owned_rows(payload.concept_assignments, registry_id)
    concept_relations = _registry_owned_rows(payload.concept_relations, registry_id)
    surveillance_series = _registry_owned_rows(payload.surveillance_series, registry_id)
    source_availability = _registry_owned_rows(payload.source_availability, registry_id)
    results = {
        "taxonomy_nodes": await _upsert_rows(
            db,
            DiseaseTaxonomyNode.__table__,
            taxonomy_nodes,
            ["node_code"],
        ),
        "taxonomy_edges": await _upsert_rows(
            db,
            DiseaseTaxonomyEdge.__table__,
            taxonomy_edges,
            ["parent_node_code", "child_node_code", "relation_type"],
        ),
        "concept_assignments": await _upsert_rows(
            db,
            DiseaseConceptAssignment.__table__,
            concept_assignments,
            ["disease_id", "node_code"],
        ),
        "concept_relations": await _upsert_rows(
            db,
            DiseaseConceptRelation.__table__,
            concept_relations,
            ["subject_disease_id", "relation_type", "object_disease_id"],
        ),
        "surveillance_series": await _upsert_rows(
            db,
            DiseaseSurveillanceSeries.__table__,
            surveillance_series,
            ["series_code"],
        ),
        "source_availability": await _upsert_rows(
            db,
            DiseaseSourceAvailability.__table__,
            source_availability,
            ["availability_code"],
        ),
    }
    results.update(await _reconcile_ontology_tables(db, payload, registry_id))
    return results


async def _ensure_ontology_schema_evolution(db) -> None:
    """Apply additive changes that ``create_all`` cannot add to existing tables."""

    await db.execute(text("""
            ALTER TABLE disease_surveillance_series
                ADD COLUMN IF NOT EXISTS target_group_code VARCHAR(120)
            """))
    await db.execute(text("""
            ALTER TABLE disease_surveillance_series
                ALTER COLUMN disease_id DROP NOT NULL
            """))
    await db.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_disease_surveillance_series_exactly_one_target'
                ) THEN
                    ALTER TABLE disease_surveillance_series
                    ADD CONSTRAINT ck_disease_surveillance_series_exactly_one_target
                    CHECK (
                        (disease_id IS NOT NULL AND target_group_code IS NULL) OR
                        (disease_id IS NULL AND target_group_code IS NOT NULL)
                    );
                END IF;
            END $$
            """))
    await db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_disease_surveillance_series_group
            ON disease_surveillance_series (target_group_code)
            """))


async def _ensure_migration_audit_schema(db) -> None:
    """Create the reversible before-image ledger inside the apply transaction."""

    await db.execute(text("""
            CREATE TABLE IF NOT EXISTS disease_migration_audit (
                id BIGSERIAL PRIMARY KEY,
                migration_run_id VARCHAR(160) NOT NULL,
                migration_key VARCHAR(500) NOT NULL,
                entity_table VARCHAR(160) NOT NULL,
                operation VARCHAR(80) NOT NULL,
                identity_key VARCHAR(500) NOT NULL,
                identity JSONB NOT NULL,
                before_data JSONB NOT NULL,
                after_data JSONB,
                reason TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                restored_at TIMESTAMPTZ,
                CONSTRAINT uq_disease_migration_audit_run_identity
                    UNIQUE (migration_run_id, migration_key, identity_key)
            )
            """))
    # Upgrade an early development version whose uniqueness omitted run ID.
    await db.execute(text("""
            ALTER TABLE disease_migration_audit
            DROP CONSTRAINT IF EXISTS
                disease_migration_audit_migration_key_identity_key_key
            """))
    await db.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_disease_migration_audit_run_identity'
                      AND conrelid =
                          'public.disease_migration_audit'::regclass
                ) THEN
                    ALTER TABLE disease_migration_audit
                    ADD CONSTRAINT uq_disease_migration_audit_run_identity
                    UNIQUE (migration_run_id, migration_key, identity_key);
                END IF;
            END $$
            """))
    await db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_disease_migration_audit_run
            ON disease_migration_audit (migration_run_id, migration_key)
            """))


def _fact_migration_key(remap: FactRemap) -> str:
    scope = remap.country_code or "ALL"
    source = remap.source_id or "ALL"
    evidence = (
        f":{remap.evidence_field}={remap.evidence_value}"
        if remap.evidence_field
        else ""
    )
    return f"fact_remap:{scope}:{source}:{remap.old_id}->{remap.new_id}{evidence}"


async def _snapshot_fact_remap(db, remap: FactRemap, *, migration_run_id: str) -> int:
    params = {
        "migration_run_id": migration_run_id,
        "migration_key": _fact_migration_key(remap),
        "old_id": remap.old_id,
        "new_id": remap.new_id,
        "country_code": remap.country_code,
        "source_values": list(_source_evidence_values(remap.source_id)),
        "evidence_fields": str(remap.evidence_field or "").split("|"),
        "evidence_value": remap.evidence_value,
        "reason": remap.reason,
    }
    where = _fact_filter_sql(remap)
    result = await db.execute(
        text(f"""
            INSERT INTO disease_migration_audit (
                migration_run_id, migration_key, entity_table, operation,
                identity_key, identity, before_data, after_data, reason
            )
            SELECT :migration_run_id, :migration_key, 'disease_records',
                   'fact_remap',
                   concat(country.code, '|', old_disease.name, '|',
                          old_record.time::text),
                   jsonb_build_object(
                       'time', old_record.time,
                       'country_id', old_record.country_id,
                       'country_code', country.code,
                       'old_disease_id', old_record.disease_id,
                       'old_disease_code', old_disease.name,
                       'new_disease_id', new_disease.id,
                       'new_disease_code', new_disease.name
                   ),
                   to_jsonb(old_record),
                   NULL,
                   :reason
            FROM disease_records old_record
            JOIN diseases old_disease
              ON old_disease.id = old_record.disease_id
            JOIN diseases new_disease ON new_disease.name = :new_id
            JOIN countries country ON country.id = old_record.country_id
            WHERE old_disease.name = :old_id {where}
            ON CONFLICT (migration_run_id, migration_key, identity_key)
            DO NOTHING
            """),
        params,
    )
    return int(result.rowcount or 0)


async def _capture_fact_remap_after_images(
    db, remap: FactRemap, *, migration_run_id: str
) -> int:
    result = await db.execute(
        text("""
            UPDATE disease_migration_audit audit
            SET after_data = to_jsonb(current_record)
            FROM disease_records current_record
            WHERE audit.migration_run_id = :migration_run_id
              AND audit.migration_key = :migration_key
              AND audit.restored_at IS NULL
              AND current_record.time =
                  CAST(audit.identity ->> 'time' AS timestamptz)
              AND current_record.country_id =
                  CAST(audit.identity ->> 'country_id' AS integer)
              AND current_record.disease_id =
                  CAST(audit.identity ->> 'new_disease_id' AS integer)
            """),
        {
            "migration_run_id": migration_run_id,
            "migration_key": _fact_migration_key(remap),
        },
    )
    return int(result.rowcount or 0)


def _series_geography_migration_key(remap: SeriesGeographyRemap) -> str:
    source = remap.source_system or "ALL"
    evidence = ",".join(value.casefold() for value in remap.evidence_values) or "ALL"
    return (
        f"series_geography:{source}:{remap.old_key}->{remap.new_key}:"
        f"evidence={evidence}"
    )


async def _snapshot_series_geography_remap(
    db, remap: SeriesGeographyRemap, *, migration_run_id: str
) -> int:
    params = {
        **_series_geography_params(remap),
        "migration_run_id": migration_run_id,
        "migration_key": _series_geography_migration_key(remap),
        "reason": remap.reason,
    }
    where = _series_geography_filter_sql(remap, alias="old_observation")
    result = await db.execute(
        text(f"""
            INSERT INTO disease_migration_audit (
                migration_run_id, migration_key, entity_table, operation,
                identity_key, identity, before_data, after_data, reason
            )
            SELECT :migration_run_id, :migration_key,
                   'disease_series_observations', 'series_geography_remap',
                   concat(
                       old_observation.series_code, '|',
                       md5(concat_ws(
                           '|', old_observation.time::text,
                           old_observation.series_code,
                           old_observation.geography_key,
                           old_observation.dimension_key
                       ))
                   ),
                   jsonb_build_object(
                       'time', old_observation.time,
                       'series_code', old_observation.series_code,
                       'old_geography_key', old_observation.geography_key,
                       'new_geography_key', CAST(:new_key AS text),
                       'dimension_key', old_observation.dimension_key,
                       'source_system', old_series.source_system
                   ),
                   to_jsonb(old_observation), NULL, :reason
            FROM disease_series_observations old_observation
            JOIN disease_surveillance_series old_series
              ON old_series.series_code = old_observation.series_code
            WHERE {where}
            ON CONFLICT (migration_run_id, migration_key, identity_key)
            DO NOTHING
            """),
        params,
    )
    return int(result.rowcount or 0)


async def _capture_series_geography_after_images(
    db, remap: SeriesGeographyRemap, *, migration_run_id: str
) -> int:
    result = await db.execute(
        text("""
            UPDATE disease_migration_audit audit
            SET after_data = to_jsonb(current_observation)
            FROM disease_series_observations current_observation
            WHERE audit.migration_run_id = :migration_run_id
              AND audit.migration_key = :migration_key
              AND audit.restored_at IS NULL
              AND current_observation.time =
                  CAST(audit.identity ->> 'time' AS timestamptz)
              AND current_observation.series_code =
                  audit.identity ->> 'series_code'
              AND current_observation.geography_key =
                  audit.identity ->> 'new_geography_key'
              AND current_observation.dimension_key =
                  audit.identity ->> 'dimension_key'
            """),
        {
            "migration_run_id": migration_run_id,
            "migration_key": _series_geography_migration_key(remap),
        },
    )
    return int(result.rowcount or 0)


def _fact_filter_sql(
    remap: FactRemap,
    alias: str = "old_record",
    *,
    include_source: bool = True,
) -> str:
    clauses = []
    if remap.country_code:
        clauses.append("country.code = :country_code")
    if include_source and remap.source_id and remap.source_id != "*":
        _source_evidence_values(remap.source_id)
        clauses.append(
            f"lower(btrim(COALESCE({alias}.data_source, ''))) = " "ANY(:source_values)"
        )
    if remap.evidence_field or remap.evidence_value:
        if not (remap.evidence_field and remap.evidence_value):
            raise ValueError(
                f"Incomplete exact evidence for {remap.old_id}->{remap.new_id}"
            )
        clauses.append(
            "EXISTS ("
            " SELECT 1"
            " FROM jsonb_array_elements("
            "   CASE jsonb_typeof(COALESCE("
            f"     {alias}.raw_data::jsonb, 'null'::jsonb))"
            "   WHEN 'array' THEN "
            f"     {alias}.raw_data::jsonb"
            "   WHEN 'object' THEN "
            f"     jsonb_build_array({alias}.raw_data::jsonb)"
            "   ELSE '[]'::jsonb END"
            " ) AS exact_evidence(row_data)"
            " CROSS JOIN LATERAL jsonb_each_text("
            "   exact_evidence.row_data"
            " ) AS exact_field(field_name, field_value)"
            " WHERE exact_field.field_name = ANY(:evidence_fields)"
            "   AND lower(btrim(exact_field.field_value)) = "
            "       lower(:evidence_value)"
            ")"
        )
    if remap.country_code and not remap.evidence_field:
        raise ValueError(
            f"Country-scoped remap {remap.old_id}->{remap.new_id} lacks exact evidence"
        )
    return " AND " + " AND ".join(clauses) if clauses else ""


def _raw_observation_value(row: dict[str, Any]) -> int | None:
    for key in (
        "Cases",
        "Current week",
        "Current Week",
        "Current MMWR week",
        "Current week, current MMWR year",
        "m1",
    ):
        if key not in row:
            continue
        value = row.get(key)
        if value is None or str(value).strip() in {"", "-", "*"}:
            return None
        return _legacy_case_count(value)
    return None


def _fact_evidence_errors(record: dict[str, Any], remap: FactRemap) -> list[str]:
    """Reject a source-scoped whole-fact move unless it is one pure component."""

    if not remap.country_code:
        return []
    source_values = _source_evidence_values(remap.source_id)
    actual_source = str(record.get("data_source") or "").strip()
    if source_values and actual_source.casefold() not in source_values:
        return [
            f"data_source={actual_source!r} does not match Registry source "
            f"{remap.source_id}"
        ]
    raw_rows = _legacy_raw_rows(record.get("raw_data"))
    if len(raw_rows) != 1:
        return [f"raw_component_count={len(raw_rows)} (expected exactly 1)"]
    raw_row = raw_rows[0]
    evidence_fields = str(remap.evidence_field or "").split("|")
    actual_evidence = next(
        (
            str(raw_row.get(field) or "").strip()
            for field in evidence_fields
            if str(raw_row.get(field) or "").strip()
        ),
        "",
    )
    if normalize_mapping_key(actual_evidence) != normalize_mapping_key(
        remap.evidence_value
    ):
        return [
            f"exact_evidence={actual_evidence!r} "
            f"(expected {remap.evidence_value!r})"
        ]
    raw_value = _raw_observation_value(raw_row)
    if raw_value is None:
        return ["raw component has no recognized observation value"]
    stored_value = int(record.get("cases") or 0)
    if raw_value != stored_value:
        return [f"raw_value={raw_value} differs from stored_cases={stored_value}"]
    return []


async def _preflight_fact_remap(db, remap: FactRemap) -> dict[str, Any]:
    params = {
        "old_id": remap.old_id,
        "new_id": remap.new_id,
        "country_code": remap.country_code,
        "source_values": list(_source_evidence_values(remap.source_id)),
        "evidence_fields": str(remap.evidence_field or "").split("|"),
        "evidence_value": remap.evidence_value,
    }
    where = _fact_filter_sql(remap)
    selected = (
        (
            await db.execute(
                text(f"""
                SELECT old_record.time, old_record.country_id,
                       old_record.disease_id, old_record.cases,
                       old_record.data_source, old_record.raw_data,
                       old_record.metadata
                FROM disease_records old_record
                JOIN diseases old_disease
                  ON old_disease.id = old_record.disease_id
                JOIN countries country ON country.id = old_record.country_id
                WHERE old_disease.name = :old_id {where}
                ORDER BY old_record.time
                """),
                params,
            )
        )
        .mappings()
        .all()
    )

    source_mismatches: list[dict[str, Any]] = []
    if remap.source_id and remap.source_id != "*":
        without_source = _fact_filter_sql(remap, include_source=False)
        mismatch_rows = (
            (
                await db.execute(
                    text(f"""
                    SELECT old_record.data_source, COUNT(*) AS observations,
                           COALESCE(SUM(old_record.cases), 0) AS cases
                    FROM disease_records old_record
                    JOIN diseases old_disease
                      ON old_disease.id = old_record.disease_id
                    JOIN countries country ON country.id = old_record.country_id
                    WHERE old_disease.name = :old_id {without_source}
                      AND lower(btrim(COALESCE(old_record.data_source, '')))
                          <> ALL(:source_values)
                    GROUP BY old_record.data_source
                    ORDER BY observations DESC, old_record.data_source
                    """),
                    params,
                )
            )
            .mappings()
            .all()
        )
        source_mismatches = [dict(row) for row in mismatch_rows]

    collision_count = int(
        (
            await db.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM disease_records old_record
                    JOIN diseases old_disease
                      ON old_disease.id = old_record.disease_id
                    JOIN countries country ON country.id = old_record.country_id
                    JOIN diseases new_disease ON new_disease.name = :new_id
                    JOIN disease_records new_record
                      ON new_record.disease_id = new_disease.id
                     AND new_record.country_id = old_record.country_id
                     AND new_record.time = old_record.time
                    WHERE old_disease.name = :old_id {where}
                    """),
                params,
            )
        ).scalar()
        or 0
    )

    errors: list[str] = []
    evidence_examples: list[dict[str, Any]] = []
    for row in selected:
        record = dict(row)
        record_errors = _fact_evidence_errors(record, remap)
        if not record_errors:
            continue
        errors.extend(record_errors)
        if len(evidence_examples) < 5:
            evidence_examples.append(
                {
                    "time": str(record.get("time")),
                    "cases": int(record.get("cases") or 0),
                    "errors": record_errors,
                    "raw_data": record.get("raw_data"),
                }
            )
    if collision_count:
        errors.append(f"target_fact_key_collisions={collision_count}")
    if source_mismatches:
        errors.append(
            "source_evidence_mismatches="
            f"{sum(int(row['observations']) for row in source_mismatches)}"
        )

    times = [row["time"] for row in selected]
    return {
        **asdict(remap),
        "selected": len(selected),
        "selected_cases": sum(int(row["cases"] or 0) for row in selected),
        "selected_zero_observations": sum(
            1 for row in selected if int(row["cases"] or 0) == 0
        ),
        "first_time": str(min(times)) if times else None,
        "last_time": str(max(times)) if times else None,
        "collisions": collision_count,
        "evidence_pure": not evidence_examples,
        "evidence_error_examples": evidence_examples,
        "source_evidence_values": list(_source_evidence_values(remap.source_id)),
        "source_mismatch_examples": source_mismatches[:20],
        "errors": sorted(set(errors)),
        "status": "blocked" if errors else "ready",
    }


async def _apply_fact_remap(
    db, remap: FactRemap, *, migration_run_id: str
) -> dict[str, Any]:
    params = {
        "old_id": remap.old_id,
        "new_id": remap.new_id,
        "country_code": remap.country_code,
        "source_values": list(_source_evidence_values(remap.source_id)),
        "evidence_fields": str(remap.evidence_field or "").split("|"),
        "evidence_value": remap.evidence_value,
    }
    where = _fact_filter_sql(remap)
    preflight = await _preflight_fact_remap(db, remap)
    if preflight["errors"]:
        raise RuntimeError(
            f"Refusing {remap.old_id}->{remap.new_id}: "
            + "; ".join(preflight["errors"])
        )
    selected_count = int(preflight["selected"])
    audit_snapshots = await _snapshot_fact_remap(
        db, remap, migration_run_id=migration_run_id
    )
    if audit_snapshots != selected_count:
        raise RuntimeError(
            f"Incomplete audit snapshot for {remap.old_id}->{remap.new_id}: "
            f"snapshots={audit_snapshots}, selected={selected_count}"
        )

    result = await db.execute(
        text(f"""
            UPDATE disease_records AS old_record
            SET disease_id = new_disease.id,
                metadata = (
                    COALESCE(old_record.metadata, '{{}}'::json)::jsonb ||
                    jsonb_build_object(
                        'ontology_migrated_from', CAST(:old_id AS text),
                        'ontology_migrated_to', CAST(:new_id AS text),
                        'ontology_migration_reason', CAST(:reason AS text),
                        'ontology_migration_history',
                        COALESCE(
                            old_record.metadata::jsonb -> 'ontology_migration_history',
                            '[]'::jsonb
                        ) || jsonb_build_array(
                            jsonb_build_object(
                                'from', CAST(:old_id AS text),
                                'to', CAST(:new_id AS text),
                                'reason', CAST(:reason AS text),
                                'migrated_at', CURRENT_TIMESTAMP
                            )
                        )
                    )
                )::json
            FROM diseases old_disease, diseases new_disease, countries country
            WHERE old_disease.id = old_record.disease_id
              AND new_disease.name = :new_id
              AND country.id = old_record.country_id
              AND old_disease.name = :old_id {where}
            """),
        {**params, "reason": remap.reason},
    )
    updated_count = int(result.rowcount or 0)
    if updated_count != selected_count:
        raise RuntimeError(
            f"Remap count changed during transaction for {remap.old_id}->{remap.new_id}: "
            f"selected={selected_count}, updated={updated_count}"
        )
    after_images = await _capture_fact_remap_after_images(
        db, remap, migration_run_id=migration_run_id
    )
    if after_images != updated_count:
        raise RuntimeError(
            f"Incomplete audit after-image for {remap.old_id}->{remap.new_id}: "
            f"after_images={after_images}, updated={updated_count}"
        )
    return {
        **preflight,
        "audit_snapshots_created": audit_snapshots,
        "audit_after_images_captured": after_images,
        "updated": updated_count,
        "status": "applied",
    }


async def _apply_series_geography_remap(
    db, remap: SeriesGeographyRemap, *, migration_run_id: str
) -> dict[str, Any]:
    """Move a declared geography with exact evidence and reversible images."""

    params = _series_geography_params(remap)
    preflight = await _preflight_series_geography_remap(db, remap)
    selected_count = int(preflight["selected"])
    collision_count = int(preflight["collisions"])
    if collision_count:
        raise RuntimeError(
            "Refusing disease series geography remap "
            f"{remap.old_key}->{remap.new_key}: {collision_count} "
            "target fact-key collisions require manual review"
        )

    audit_snapshots = await _snapshot_series_geography_remap(
        db, remap, migration_run_id=migration_run_id
    )
    if audit_snapshots != selected_count:
        raise RuntimeError(
            "Incomplete series geography before-image audit: "
            f"selected={selected_count}, snapshots={audit_snapshots}"
        )

    where = _series_geography_filter_sql(remap, alias="old_observation")
    result = await db.execute(
        text(f"""
            UPDATE disease_series_observations AS old_observation
            SET geography_key = :new_key,
                metadata = (
                    COALESCE(old_observation.metadata, '{{}}'::json)::jsonb ||
                    jsonb_build_object(
                        'geography_migrated_from', CAST(:old_key AS text),
                        'geography_migration_reason', CAST(:reason AS text)
                    )
                )::json,
                updated_at = CURRENT_TIMESTAMP
            FROM disease_surveillance_series old_series
            WHERE old_series.series_code = old_observation.series_code
              AND {where}
            """),
        {**params, "reason": remap.reason},
    )
    updated_count = int(result.rowcount or 0)
    if updated_count != selected_count:
        raise RuntimeError(
            "Series geography remap count changed during transaction: "
            f"selected={selected_count}, updated={updated_count}"
        )
    after_images = await _capture_series_geography_after_images(
        db, remap, migration_run_id=migration_run_id
    )
    if after_images != updated_count:
        raise RuntimeError(
            "Incomplete series geography after-image audit: "
            f"updated={updated_count}, after_images={after_images}"
        )
    return {
        **preflight,
        "audit_snapshots_created": audit_snapshots,
        "audit_after_images_captured": after_images,
        "selected": selected_count,
        "updated": updated_count,
        "collisions": collision_count,
        "status": "applied",
    }


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _legacy_raw_rows(value: object) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, dict):
        nested_rows = value.get("rows")
        value = nested_rows if isinstance(nested_rows, list) else [value]
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _legacy_case_count(value: object) -> int:
    try:
        parsed = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _partition_br_ntra_rows(
    raw_data: object,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Split invalid legacy NTRA rows from a BR flat fact.

    Historic NTRA ``Cases`` values were DBF row counts, not the source's
    ``NU_CASOPOS`` positive-case metric.  They are therefore removed rather
    than reclassified as trachoma facts.
    """

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in _legacy_raw_rows(raw_data):
        code = str(row.get("DiseaseCode") or row.get("local_code") or "")
        target = removed if code.strip().upper() == "NTRA" else kept
        target.append(row)
    kept_cases = sum(_legacy_case_count(row.get("Cases")) for row in kept)
    removed_cases = sum(_legacy_case_count(row.get("Cases")) for row in removed)
    return kept, removed, kept_cases, removed_cases


def _br_repaired_metadata(
    existing: object, kept_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    metadata = _json_object(existing)

    def unique(field: str) -> list[str]:
        values: list[str] = []
        for row in kept_rows:
            for item in str(row.get(field) or "").split("|"):
                normalized = item.strip()
                if normalized and normalized not in values:
                    values.append(normalized)
        return values

    metadata.update(
        {
            "raw_disease_labels": unique("RawDiseaseLabel"),
            "disease_codes": unique("DiseaseCode"),
            "dataset_statuses": unique("DatasetStatus"),
            "source_files": unique("SourceFiles"),
            "source_urls": unique("SourceURLs"),
            "ontology_semantic_repair": "BR_D193_REMOVE_NTRA",
            "ontology_migration_reason": BR_NTRA_REPAIR_REASON,
        }
    )
    return metadata


async def _preflight_br_ntra_legacy_projection(db) -> dict[str, Any]:
    result = await db.execute(
        text("""
            SELECT record.time, record.disease_id, record.country_id,
                   record.cases, record.raw_data, record.metadata
            FROM disease_records record
            JOIN diseases disease ON disease.id = record.disease_id
            JOIN countries country ON country.id = record.country_id
            WHERE country.code = 'BR'
              AND disease.name = 'D193'
              AND lower(btrim(COALESCE(record.data_source, ''))) =
                  ANY(:source_values)
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      CASE jsonb_typeof(COALESCE(record.raw_data::jsonb,
                                                'null'::jsonb))
                      WHEN 'array' THEN record.raw_data::jsonb
                      WHEN 'object' THEN jsonb_build_array(record.raw_data::jsonb)
                      ELSE '[]'::jsonb END
                  ) AS source_row(value)
                  WHERE upper(btrim(source_row.value ->> 'DiseaseCode')) = 'NTRA'
              )
            ORDER BY record.time
            """),
        {"source_values": list(_source_evidence_values("SRC_BR_SINAN"))},
    )
    rows = result.mappings().all()
    source_mismatches = (
        (
            await db.execute(
                text("""
                SELECT record.data_source, COUNT(*) AS observations
                FROM disease_records record
                JOIN diseases disease ON disease.id = record.disease_id
                JOIN countries country ON country.id = record.country_id
                WHERE country.code = 'BR'
                  AND disease.name = 'D193'
                  AND NOT (
                      lower(btrim(COALESCE(record.data_source, ''))) =
                      ANY(:source_values)
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          CASE jsonb_typeof(COALESCE(record.raw_data::jsonb,
                                                    'null'::jsonb))
                          WHEN 'array' THEN record.raw_data::jsonb
                          WHEN 'object' THEN jsonb_build_array(record.raw_data::jsonb)
                          ELSE '[]'::jsonb END
                      ) AS source_row(value)
                      WHERE upper(btrim(source_row.value ->> 'DiseaseCode')) = 'NTRA'
                  )
                GROUP BY record.data_source
                ORDER BY observations DESC, record.data_source
                """),
                {"source_values": list(_source_evidence_values("SRC_BR_SINAN"))},
            )
        )
        .mappings()
        .all()
    )
    updated = 0
    deleted = 0
    removed_source_rows = 0
    removed_legacy_value = 0
    errors: list[str] = []
    examples: list[dict[str, Any]] = []
    if source_mismatches:
        errors.append(
            "source_evidence_mismatches="
            f"{sum(int(row['observations']) for row in source_mismatches)}"
        )
    for record in rows:
        kept, removed, kept_cases, removed_cases = _partition_br_ntra_rows(
            record["raw_data"]
        )
        if not removed:
            errors.append(f"{record['time']}: exact NTRA evidence was not partitioned")
            continue
        expected_stored = kept_cases + removed_cases
        if int(record["cases"] or 0) != expected_stored:
            message = (
                f"{record['time']}: stored_cases={int(record['cases'] or 0)} "
                f"raw_component_sum={expected_stored}"
            )
            errors.append(message)
            if len(examples) < 5:
                examples.append({"time": str(record["time"]), "error": message})
        removed_source_rows += len(removed)
        removed_legacy_value += removed_cases
        if kept:
            updated += 1
        else:
            deleted += 1
    return {
        "country_code": "BR",
        "old_disease_id": "D193",
        "source_code": "NTRA",
        "action": "remove_invalid_legacy_projection_with_audit",
        "selected_facts": len(rows),
        "would_update_facts": updated,
        "would_delete_facts": deleted,
        "removed_source_rows": removed_source_rows,
        "removed_legacy_value": removed_legacy_value,
        "requires_source_reingestion": True,
        "reason": BR_NTRA_REPAIR_REASON,
        "errors": errors,
        "error_examples": examples,
        "source_evidence_values": list(_source_evidence_values("SRC_BR_SINAN")),
        "source_mismatch_examples": [dict(row) for row in source_mismatches[:20]],
        "status": "blocked" if errors else "ready",
    }


async def _snapshot_br_ntra_record(
    db,
    *,
    record: dict[str, Any],
    migration_run_id: str,
) -> int | None:
    result = await db.execute(
        text("""
            INSERT INTO disease_migration_audit (
                migration_run_id, migration_key, entity_table, operation,
                identity_key, identity, before_data, after_data, reason
            ) VALUES (
                :migration_run_id, :migration_key, 'disease_records',
                'legacy_projection_repair', :identity_key,
                CAST(:identity AS jsonb), CAST(:before_data AS jsonb),
                NULL, :reason
            )
            ON CONFLICT (migration_run_id, migration_key, identity_key)
            DO NOTHING
            RETURNING id
            """),
        {
            "migration_run_id": migration_run_id,
            "migration_key": "semantic_repair:BR:D193:NTRA_INVALID_ROW_COUNT",
            "identity_key": f"BR|D193|{record['time']}",
            "identity": json.dumps(
                {
                    "time": str(record["time"]),
                    "country_id": record["country_id"],
                    "disease_id": record["disease_id"],
                    "country_code": "BR",
                    "disease_code": "D193",
                },
                ensure_ascii=False,
            ),
            "before_data": json.dumps(
                record["before_data"], ensure_ascii=False, default=str
            ),
            "reason": BR_NTRA_REPAIR_REASON,
        },
    )
    audit_id = result.scalar_one_or_none()
    return int(audit_id) if audit_id is not None else None


async def _capture_br_ntra_after_image(db, *, audit_id: int) -> int:
    result = await db.execute(
        text("""
            UPDATE disease_migration_audit audit
            SET after_data = to_jsonb(current_record)
            FROM disease_records current_record
            WHERE audit.id = :audit_id
              AND current_record.time =
                  CAST(audit.identity ->> 'time' AS timestamptz)
              AND current_record.country_id =
                  CAST(audit.identity ->> 'country_id' AS integer)
              AND current_record.disease_id =
                  CAST(audit.identity ->> 'disease_id' AS integer)
            """),
        {"audit_id": audit_id},
    )
    return int(result.rowcount or 0)


async def _repair_br_ntra_legacy_projection(
    db, *, migration_run_id: str
) -> dict[str, Any]:
    """Remove known-invalid NTRA row counts from legacy BR D193 facts."""

    result = await db.execute(
        text("""
            SELECT record.time, record.disease_id, record.country_id,
                   record.cases, record.raw_data, record.metadata,
                   to_jsonb(record) AS before_data
            FROM disease_records record
            JOIN diseases disease ON disease.id = record.disease_id
            JOIN countries country ON country.id = record.country_id
            WHERE country.code = 'BR'
              AND disease.name = 'D193'
              AND lower(btrim(COALESCE(record.data_source, ''))) =
                  ANY(:source_values)
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      CASE jsonb_typeof(COALESCE(record.raw_data::jsonb,
                                                'null'::jsonb))
                      WHEN 'array' THEN record.raw_data::jsonb
                      WHEN 'object' THEN jsonb_build_array(record.raw_data::jsonb)
                      ELSE '[]'::jsonb END
                  ) AS source_row(value)
                  WHERE upper(btrim(source_row.value ->> 'DiseaseCode')) = 'NTRA'
              )
            FOR UPDATE OF record
            """),
        {"source_values": list(_source_evidence_values("SRC_BR_SINAN"))},
    )
    rows = result.mappings().all()
    updated = 0
    deleted = 0
    removed_source_rows = 0
    removed_legacy_value = 0
    audit_snapshots = 0
    audit_after_images = 0
    for record in rows:
        kept, removed, kept_cases, removed_cases = _partition_br_ntra_rows(
            record["raw_data"]
        )
        if not removed:
            continue
        removed_source_rows += len(removed)
        removed_legacy_value += removed_cases
        identity = {
            "time": record["time"],
            "disease_id": record["disease_id"],
            "country_id": record["country_id"],
        }
        audit_id = await _snapshot_br_ntra_record(
            db,
            record=dict(record),
            migration_run_id=migration_run_id,
        )
        if audit_id is None:
            raise RuntimeError(
                "Refusing BR NTRA repair without a fresh audit before-image: "
                f"{record['time']}"
            )
        audit_snapshots += 1
        if not kept:
            delete_result = await db.execute(
                text("""
                    DELETE FROM disease_records
                    WHERE time = :time
                      AND disease_id = :disease_id
                      AND country_id = :country_id
                    """),
                identity,
            )
            delete_count = int(delete_result.rowcount or 0)
            if delete_count != 1:
                raise RuntimeError(
                    f"BR NTRA delete count changed for {record['time']}: "
                    f"expected=1, deleted={delete_count}"
                )
            deleted += delete_count
            continue
        metadata = _br_repaired_metadata(record["metadata"], kept)
        update_result = await db.execute(
            text("""
                UPDATE disease_records
                SET cases = :cases,
                    raw_data = CAST(:raw_data AS json),
                    metadata = CAST(:metadata AS json)
                WHERE time = :time
                  AND disease_id = :disease_id
                  AND country_id = :country_id
                """),
            {
                **identity,
                "cases": kept_cases,
                "raw_data": json.dumps(kept, ensure_ascii=False),
                "metadata": json.dumps(metadata, ensure_ascii=False),
            },
        )
        update_count = int(update_result.rowcount or 0)
        if update_count != 1:
            raise RuntimeError(
                f"BR NTRA update count changed for {record['time']}: "
                f"expected=1, updated={update_count}"
            )
        updated += update_count
        captured = await _capture_br_ntra_after_image(db, audit_id=audit_id)
        if captured != 1:
            raise RuntimeError(
                f"BR NTRA after-image capture failed for {record['time']}: "
                f"captured={captured}"
            )
        audit_after_images += captured

    if audit_snapshots != updated + deleted:
        raise RuntimeError(
            "BR NTRA audit snapshot count does not match mutations: "
            f"snapshots={audit_snapshots}, updated={updated}, deleted={deleted}"
        )
    if audit_after_images != updated:
        raise RuntimeError(
            "BR NTRA audit after-image count does not match updates: "
            f"after_images={audit_after_images}, updated={updated}"
        )

    return {
        "country_code": "BR",
        "old_disease_id": "D193",
        "source_code": "NTRA",
        "selected_facts": len(rows),
        "updated_facts": updated,
        "deleted_facts": deleted,
        "removed_source_rows": removed_source_rows,
        "removed_legacy_value": removed_legacy_value,
        "audit_snapshots_created": audit_snapshots,
        "audit_after_images_captured": audit_after_images,
        "requires_source_reingestion": True,
        "reason": BR_NTRA_REPAIR_REASON,
    }


async def _verify_applied_state(
    db, *, preflight: dict[str, Any], payload
) -> dict[str, Any]:
    """Enforce post-write conservation and authoritative-state invariants."""

    totals = (await db.execute(text("""
                SELECT COUNT(*) AS observations, COALESCE(SUM(cases), 0) AS cases
                FROM disease_records
                """))).mappings().one()
    removed_observations = sum(
        int(item.get("would_delete_facts") or 0)
        for item in preflight["semantic_repairs"]
    )
    removed_cases = sum(
        int(item.get("removed_legacy_value") or 0)
        for item in preflight["semantic_repairs"]
    )
    expected_observations = (
        int(preflight["database_snapshot"]["legacy_observations"])
        - removed_observations
    )
    expected_cases = int(preflight["database_snapshot"]["legacy_cases"]) - removed_cases
    actual_observations = int(totals["observations"] or 0)
    actual_cases = int(totals["cases"] or 0)
    if (actual_observations, actual_cases) != (
        expected_observations,
        expected_cases,
    ):
        raise RuntimeError(
            "Post-migration legacy conservation failed: "
            f"expected observations/cases={expected_observations}/{expected_cases}, "
            f"actual={actual_observations}/{actual_cases}"
        )

    residuals = []
    for remap in FACT_REMAPS:
        check = await _preflight_fact_remap(db, remap)
        if check["selected"]:
            residuals.append(
                {
                    "country_code": remap.country_code,
                    "old_id": remap.old_id,
                    "new_id": remap.new_id,
                    "remaining": check["selected"],
                }
            )
    if residuals:
        raise RuntimeError(
            "Post-migration remap residuals remain: "
            + json.dumps(residuals[:20], ensure_ascii=False, sort_keys=True)
        )

    geography_residuals = []
    for remap in SERIES_GEOGRAPHY_REMAPS:
        check = await _preflight_series_geography_remap(db, remap)
        if check["selected"]:
            geography_residuals.append(
                {
                    "source_system": remap.source_system,
                    "old_key": remap.old_key,
                    "new_key": remap.new_key,
                    "remaining": check["selected"],
                }
            )
    if geography_residuals:
        raise RuntimeError(
            "Post-migration series geography residuals remain: "
            + json.dumps(geography_residuals[:20], ensure_ascii=False, sort_keys=True)
        )

    series_totals = (await db.execute(text("""
                SELECT COUNT(*) AS observations,
                       COUNT(*) FILTER (WHERE suppressed) AS suppressed,
                       COALESCE(SUM(value), 0) AS value_sum
                FROM disease_series_observations
                """))).mappings().one()
    expected_series_totals = (
        int(preflight["database_snapshot"]["series_observations"]),
        int(preflight["database_snapshot"]["series_suppressed"]),
        float(preflight["database_snapshot"]["series_value_sum"]),
    )
    actual_series_totals = (
        int(series_totals["observations"] or 0),
        int(series_totals["suppressed"] or 0),
        float(series_totals["value_sum"] or 0),
    )
    if actual_series_totals != expected_series_totals:
        raise RuntimeError(
            "Post-migration series conservation failed: "
            f"expected observations/suppressed/value={expected_series_totals}, "
            f"actual={actual_series_totals}"
        )

    mapping_rows = (await db.execute(text("""
                SELECT dm.id, dm.country_code, dm.source_id, dm.local_name,
                       disease.id AS disease_db_id, dm.disease_id, dm.metadata
                FROM disease_mappings dm
                JOIN diseases disease ON disease.name = dm.disease_id
                WHERE dm.is_active = true AND disease.is_active = true
                ORDER BY dm.country_code, dm.source_id, dm.priority DESC,
                         dm.local_name, dm.disease_id
                """))).mappings().all()
    active_mapping_rows = [dict(row) for row in mapping_rows]
    authoritative_mappings = _managed_mapping_rows(_catalogue_ids())
    unresolved_reconciliation = _mapping_deactivation_plan(
        active_mapping_rows, authoritative_mappings
    )
    if unresolved_reconciliation:
        raise RuntimeError(
            "Post-migration mapping reconciliation remains incomplete: "
            + json.dumps(
                unresolved_reconciliation[:20],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    active_identities = {
        (
            str(row["country_code"]),
            str(row["source_id"]),
            normalize_mapping_key(row["local_name"]),
            str(row["disease_id"]),
        )
        for row in active_mapping_rows
    }
    missing_authoritative = [
        {
            "country_code": row["country_code"],
            "source_id": row["source_id"],
            "local_name": row["local_name"],
            "disease_id": row["disease_id"],
        }
        for row in authoritative_mappings
        if (
            str(row["country_code"]),
            str(row["source_id"]),
            normalize_mapping_key(row["local_name"]),
            str(row["disease_id"]),
        )
        not in active_identities
    ]
    if missing_authoritative:
        raise RuntimeError(
            "Post-migration authoritative mappings are missing: "
            + json.dumps(missing_authoritative[:20], ensure_ascii=False, sort_keys=True)
        )
    rows_by_scope: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
    for row in mapping_rows:
        rows_by_scope.setdefault(
            (str(row["country_code"]), str(row["source_id"])), []
        ).append(
            (
                row["local_name"],
                row["disease_db_id"],
                row["disease_id"],
                row["source_id"],
            )
        )
    for (country_code, source_id), rows in rows_by_scope.items():
        build_mapping_lookup(rows, country_code=country_code, source_id=source_id)

    expected_series = {row["series_code"] for row in payload.surveillance_series}
    stored_series = set(
        (
            await db.execute(
                text("""
                    SELECT series_code FROM disease_surveillance_series
                    WHERE series_code = ANY(:series_codes)
                    """),
                {"series_codes": sorted(expected_series)},
            )
        ).scalars()
    )
    expected_availability = {
        row["availability_code"] for row in payload.source_availability
    }
    stored_availability = set(
        (
            await db.execute(
                text("""
                    SELECT availability_code FROM disease_source_availability
                    WHERE availability_code = ANY(:availability_codes)
                    """),
                {"availability_codes": sorted(expected_availability)},
            )
        ).scalars()
    )
    missing_series = sorted(expected_series - stored_series)
    missing_availability = sorted(expected_availability - stored_availability)
    if missing_series or missing_availability:
        raise RuntimeError(
            "Post-migration registry rows missing: "
            f"series={missing_series[:20]}, availability={missing_availability[:20]}"
        )
    return {
        "legacy_observations": actual_observations,
        "legacy_cases": actual_cases,
        "expected_legacy_observations": expected_observations,
        "expected_legacy_cases": expected_cases,
        "fact_remap_residuals": 0,
        "series_geography_remap_residuals": 0,
        "series_observations": actual_series_totals[0],
        "series_suppressed": actual_series_totals[1],
        "series_value_sum": actual_series_totals[2],
        "active_mapping_scopes_validated": len(rows_by_scope),
        "authoritative_mapping_identities_present": len(authoritative_mappings),
        "mapping_reconciliation_residuals": 0,
        "registry_series_present": len(stored_series),
        "registry_availability_present": len(stored_availability),
    }


async def apply_sync(*, commit: bool = True) -> dict[str, Any]:
    preflight = await preflight_sync()
    if preflight["status"] != "ready":
        raise RuntimeError(
            "Database preflight blocked ontology synchronization: "
            + "; ".join(preflight["errors"])
        )
    ontology = load_disease_ontology()
    document = ontology.to_dict()
    payload = build_disease_ontology_sync_payload(ontology)
    managed_ids = _catalogue_ids()

    migration_run_id = ":".join(
        (
            document["registry_id"],
            document.get("release_version") or "unversioned",
            str(uuid.uuid4()),
        )
    )
    async with get_db() as db:
        await acquire_disease_data_mutation_lock(db)
        connection = await db.connection()
        await connection.run_sync(Base.metadata.create_all)
        await ensure_country_scope_schema(db)
        await ensure_disease_mapping_source_schema(db)
        await _ensure_ontology_schema_evolution(db)
        await _ensure_migration_audit_schema(db)
        await db.execute(
            text("ALTER TABLE diseases ALTER COLUMN icd_10 TYPE VARCHAR(40)")
        )
        catalogue_count = await _sync_catalogue(db, ontology)
        mapping_count, deactivated_mapping_ids = await _sync_mappings(db, managed_ids)
        ontology_counts = await _sync_ontology_tables(
            db, payload, document["registry_id"]
        )
        semantic_repair_results = [
            await _repair_br_ntra_legacy_projection(
                db, migration_run_id=migration_run_id
            )
        ]
        remap_results = []
        for remap in FACT_REMAPS:
            remap_results.append(
                await _apply_fact_remap(db, remap, migration_run_id=migration_run_id)
            )
        geography_remap_results = []
        for remap in SERIES_GEOGRAPHY_REMAPS:
            geography_remap_results.append(
                await _apply_series_geography_remap(
                    db, remap, migration_run_id=migration_run_id
                )
            )
        post_verification = await _verify_applied_state(
            db, preflight=preflight, payload=payload
        )
        if commit:
            await db.commit()
        else:
            await db.rollback()

    return {
        "mode": "applied" if commit else "rehearsed_rollback",
        "registry_id": document["registry_id"],
        "schema_version": document["schema_version"],
        "release_version": document.get("release_version"),
        "migration_run_id": migration_run_id,
        "preflight": {
            "status": preflight["status"],
            "database_snapshot": preflight["database_snapshot"],
            "mapping_target_changes": len(
                preflight["mapping_preflight"]["target_changes"]
            ),
            "mapping_deactivations": preflight["mapping_preflight"][
                "deactivation_count"
            ],
            "fact_remap_selected": sum(
                int(item["selected"]) for item in preflight["fact_remaps"]
            ),
            "fact_remap_selected_cases": sum(
                int(item["selected_cases"]) for item in preflight["fact_remaps"]
            ),
        },
        "catalogue_rows": catalogue_count,
        "managed_mapping_rows": mapping_count,
        "mapping_reconciliation": {
            "deactivated": len(deactivated_mapping_ids),
            "deactivated_mapping_ids": deactivated_mapping_ids,
        },
        "ontology_rows": ontology_counts,
        "semantic_repairs": semantic_repair_results,
        "fact_remaps": remap_results,
        "series_geography_remaps": geography_remap_results,
        "post_verification": post_verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the synchronization transaction (default is dry-run)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="print only the static configuration plan without querying the database",
    )
    parser.add_argument(
        "--rehearse",
        action="store_true",
        help="execute the full transaction and roll it back instead of committing",
    )
    args = parser.parse_args()
    if sum(bool(value) for value in (args.apply, args.offline, args.rehearse)) > 1:
        parser.error("--apply, --offline, and --rehearse are mutually exclusive")
    if args.apply:
        result = asyncio.run(apply_sync())
    elif args.rehearse:
        result = asyncio.run(apply_sync(commit=False))
    elif args.offline:
        result = build_plan()
    else:
        result = asyncio.run(preflight_sync())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if result.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
