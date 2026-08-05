"""2026.08 repair for invalid BR NTRA row-count projections.

This migration is intentionally versioned instead of becoming general ontology
sync behavior. Transaction ownership remains with the calling orchestrator.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

BR_NTRA_REPAIR_REASON = (
    "remove legacy NTRA row-count projection from work-related mental disorder; "
    "NTRA is a trachoma survey whose positive-case metric requires source reingestion"
)

SourceEvidenceValues = Callable[[str | None], tuple[str, ...]]


def json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def legacy_raw_rows(value: object) -> list[dict[str, Any]]:
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


def legacy_case_count(value: object) -> int:
    try:
        parsed = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def partition_rows(
    raw_data: object,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Split known-invalid NTRA rows from a legacy BR flat fact."""

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in legacy_raw_rows(raw_data):
        code = str(row.get("DiseaseCode") or row.get("local_code") or "")
        (removed if code.strip().upper() == "NTRA" else kept).append(row)
    kept_cases = sum(legacy_case_count(row.get("Cases")) for row in kept)
    removed_cases = sum(legacy_case_count(row.get("Cases")) for row in removed)
    return kept, removed, kept_cases, removed_cases


def repaired_metadata(
    existing: object, kept_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    metadata = json_object(existing)

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


async def preflight(db, source_evidence_values: SourceEvidenceValues) -> dict[str, Any]:
    source_values = list(source_evidence_values("SRC_BR_SINAN"))
    result = await db.execute(
        text("""
            SELECT record.time, record.disease_id, record.country_id,
                   record.cases, record.raw_data, record.metadata
            FROM disease_records record
            JOIN diseases disease ON disease.id = record.disease_id
            JOIN countries country ON country.id = record.country_id
            WHERE country.code = 'BR'
              AND disease.name = 'D193'
              AND lower(btrim(COALESCE(record.data_source, ''))) = ANY(:source_values)
              AND EXISTS (
                  SELECT 1 FROM jsonb_array_elements(
                      CASE jsonb_typeof(COALESCE(record.raw_data::jsonb, 'null'::jsonb))
                      WHEN 'array' THEN record.raw_data::jsonb
                      WHEN 'object' THEN jsonb_build_array(record.raw_data::jsonb)
                      ELSE '[]'::jsonb END
                  ) AS source_row(value)
                  WHERE upper(btrim(source_row.value ->> 'DiseaseCode')) = 'NTRA'
              )
            ORDER BY record.time
            """),
        {"source_values": source_values},
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
                WHERE country.code = 'BR' AND disease.name = 'D193'
                  AND NOT (lower(btrim(COALESCE(record.data_source, ''))) =
                           ANY(:source_values))
                  AND EXISTS (
                      SELECT 1 FROM jsonb_array_elements(
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
                {"source_values": source_values},
            )
        ).mappings().all()
    )
    updated = deleted = removed_source_rows = removed_legacy_value = 0
    errors: list[str] = []
    examples: list[dict[str, Any]] = []
    if source_mismatches:
        errors.append(
            "source_evidence_mismatches="
            f"{sum(int(row['observations']) for row in source_mismatches)}"
        )
    for record in rows:
        kept, removed, kept_cases, removed_cases = partition_rows(record["raw_data"])
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
        updated += bool(kept)
        deleted += not kept
    return {
        "country_code": "BR", "old_disease_id": "D193", "source_code": "NTRA",
        "action": "remove_invalid_legacy_projection_with_audit",
        "selected_facts": len(rows), "would_update_facts": updated,
        "would_delete_facts": deleted, "removed_source_rows": removed_source_rows,
        "removed_legacy_value": removed_legacy_value,
        "requires_source_reingestion": True, "reason": BR_NTRA_REPAIR_REASON,
        "errors": errors, "error_examples": examples,
        "source_evidence_values": source_values,
        "source_mismatch_examples": [dict(row) for row in source_mismatches[:20]],
        "status": "blocked" if errors else "ready",
    }


async def _snapshot(db, *, record: dict[str, Any], migration_run_id: str) -> int | None:
    result = await db.execute(text("""
        INSERT INTO disease_migration_audit (
            migration_run_id, migration_key, entity_table, operation,
            identity_key, identity, before_data, after_data, reason
        ) VALUES (
            :migration_run_id, :migration_key, 'disease_records',
            'legacy_projection_repair', :identity_key,
            CAST(:identity AS jsonb), CAST(:before_data AS jsonb), NULL, :reason
        ) ON CONFLICT (migration_run_id, migration_key, identity_key) DO NOTHING
        RETURNING id
        """), {
            "migration_run_id": migration_run_id,
            "migration_key": "semantic_repair:BR:D193:NTRA_INVALID_ROW_COUNT",
            "identity_key": f"BR|D193|{record['time']}",
            "identity": json.dumps({"time": str(record["time"]),
                "country_id": record["country_id"], "disease_id": record["disease_id"],
                "country_code": "BR", "disease_code": "D193"}, ensure_ascii=False),
            "before_data": json.dumps(record["before_data"], ensure_ascii=False, default=str),
            "reason": BR_NTRA_REPAIR_REASON,
        })
    audit_id = result.scalar_one_or_none()
    return int(audit_id) if audit_id is not None else None


async def _capture_after_image(db, *, audit_id: int) -> int:
    result = await db.execute(text("""
        UPDATE disease_migration_audit audit SET after_data = to_jsonb(current_record)
        FROM disease_records current_record
        WHERE audit.id = :audit_id
          AND current_record.time = CAST(audit.identity ->> 'time' AS timestamptz)
          AND current_record.country_id = CAST(audit.identity ->> 'country_id' AS integer)
          AND current_record.disease_id = CAST(audit.identity ->> 'disease_id' AS integer)
        """), {"audit_id": audit_id})
    return int(result.rowcount or 0)


async def apply(db, *, migration_run_id: str, source_evidence_values: SourceEvidenceValues) -> dict[str, Any]:
    """Apply the repair inside the caller-owned transaction."""
    result = await db.execute(text("""
        SELECT record.time, record.disease_id, record.country_id, record.cases,
               record.raw_data, record.metadata, to_jsonb(record) AS before_data
        FROM disease_records record
        JOIN diseases disease ON disease.id = record.disease_id
        JOIN countries country ON country.id = record.country_id
        WHERE country.code = 'BR' AND disease.name = 'D193'
          AND lower(btrim(COALESCE(record.data_source, ''))) = ANY(:source_values)
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(
                  CASE jsonb_typeof(COALESCE(record.raw_data::jsonb, 'null'::jsonb))
                  WHEN 'array' THEN record.raw_data::jsonb
                  WHEN 'object' THEN jsonb_build_array(record.raw_data::jsonb)
                  ELSE '[]'::jsonb END
              ) AS source_row(value)
              WHERE upper(btrim(source_row.value ->> 'DiseaseCode')) = 'NTRA'
          ) FOR UPDATE OF record
        """), {"source_values": list(source_evidence_values("SRC_BR_SINAN"))})
    rows = result.mappings().all()
    updated = deleted = removed_source_rows = removed_legacy_value = 0
    audit_snapshots = audit_after_images = 0
    for record in rows:
        kept, removed, kept_cases, removed_cases = partition_rows(record["raw_data"])
        if not removed:
            continue
        removed_source_rows += len(removed)
        removed_legacy_value += removed_cases
        identity = {key: record[key] for key in ("time", "disease_id", "country_id")}
        audit_id = await _snapshot(db, record=dict(record), migration_run_id=migration_run_id)
        if audit_id is None:
            raise RuntimeError(
                "Refusing BR NTRA repair without a fresh audit before-image: "
                f"{record['time']}"
            )
        audit_snapshots += 1
        if not kept:
            mutation = await db.execute(text("""
                DELETE FROM disease_records
                WHERE time = :time AND disease_id = :disease_id
                  AND country_id = :country_id
                """), identity)
            count = int(mutation.rowcount or 0)
            if count != 1:
                raise RuntimeError(f"BR NTRA delete count changed for {record['time']}: expected=1, deleted={count}")
            deleted += count
            continue
        mutation = await db.execute(text("""
            UPDATE disease_records SET cases = :cases,
                raw_data = CAST(:raw_data AS json), metadata = CAST(:metadata AS json)
            WHERE time = :time AND disease_id = :disease_id
              AND country_id = :country_id
            """), {**identity, "cases": kept_cases,
                "raw_data": json.dumps(kept, ensure_ascii=False),
                "metadata": json.dumps(repaired_metadata(record["metadata"], kept), ensure_ascii=False)})
        count = int(mutation.rowcount or 0)
        if count != 1:
            raise RuntimeError(f"BR NTRA update count changed for {record['time']}: expected=1, updated={count}")
        updated += count
        captured = await _capture_after_image(db, audit_id=audit_id)
        if captured != 1:
            raise RuntimeError(f"BR NTRA after-image capture failed for {record['time']}: captured={captured}")
        audit_after_images += captured
    if audit_snapshots != updated + deleted:
        raise RuntimeError("BR NTRA audit snapshot count does not match mutations: "
            f"snapshots={audit_snapshots}, updated={updated}, deleted={deleted}")
    if audit_after_images != updated:
        raise RuntimeError("BR NTRA audit after-image count does not match updates: "
            f"after_images={audit_after_images}, updated={updated}")
    return {"country_code": "BR", "old_disease_id": "D193", "source_code": "NTRA",
        "selected_facts": len(rows), "updated_facts": updated, "deleted_facts": deleted,
        "removed_source_rows": removed_source_rows, "removed_legacy_value": removed_legacy_value,
        "audit_snapshots_created": audit_snapshots,
        "audit_after_images_captured": audit_after_images,
        "requires_source_reingestion": True, "reason": BR_NTRA_REPAIR_REASON}
