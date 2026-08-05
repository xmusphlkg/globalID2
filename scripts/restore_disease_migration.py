#!/usr/bin/env python3
"""Preview or restore disease-record before-images from the migration ledger."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.core.disease_mutation_lock import (  # noqa: E402
    DISEASE_DATA_MUTATION_LOCK_KEY,
    acquire_disease_data_mutation_lock,
)

RESTORE_ADVISORY_LOCK_KEY = DISEASE_DATA_MUTATION_LOCK_KEY


async def _acquire_restore_lock(db) -> None:
    """Serialize previews and applies that lock overlapping audit rows.

    A restore validates complete after-images with ``FOR UPDATE`` before it
    changes anything. Two dependent restores can otherwise acquire old/new
    natural keys in opposite orders and deadlock. A transaction-scoped
    advisory lock gives every restore operation one deterministic outer lock;
    PostgreSQL releases it automatically on commit, rollback, or disconnect.
    """

    await acquire_disease_data_mutation_lock(db)


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _timestamp_value(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid audit timestamp: {value!r}") from exc


def _is_empty_json_object(value: object) -> bool:
    if isinstance(value, dict):
        return not value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(parsed, dict) and not parsed
    return False


async def _audit_table_exists(db) -> bool:
    return bool(
        (
            await db.execute(
                text("SELECT to_regclass('public.disease_migration_audit')")
            )
        ).scalar()
    )


async def list_migrations() -> dict[str, Any]:
    async with get_db() as db:
        if not await _audit_table_exists(db):
            return {"status": "not_applied", "migrations": []}
        rows = (await db.execute(text("""
                    SELECT migration_run_id, migration_key, operation,
                           COUNT(*) AS snapshots,
                           COUNT(*) FILTER (WHERE restored_at IS NOT NULL) AS restored
                    FROM disease_migration_audit
                    GROUP BY migration_run_id, migration_key, operation
                    ORDER BY migration_run_id, migration_key
                    """))).mappings().all()
    return {"status": "ok", "migrations": [dict(row) for row in rows]}


async def _load_selected(db, *, migration_key: str, run_id: str | None):
    clauses = ["migration_key = :migration_key", "restored_at IS NULL"]
    if run_id:
        clauses.append("migration_run_id = :run_id")
    return (
        (
            await db.execute(
                text(f"""
                SELECT * FROM disease_migration_audit
                WHERE {' AND '.join(clauses)}
                ORDER BY id DESC
                """),
                {"migration_key": migration_key, "run_id": run_id},
            )
        )
        .mappings()
        .all()
    )


async def _current_record(
    db,
    *,
    time_value: object,
    disease_id: int,
    country_id: int,
    for_update: bool = False,
):
    lock_clause = "FOR UPDATE" if for_update else ""
    return (
        await db.execute(
            text(f"""
                SELECT to_jsonb(record)
                FROM disease_records record
                WHERE time = CAST(:time AS timestamptz)
                  AND disease_id = :disease_id
                  AND country_id = :country_id
                {lock_clause}
                """),
            {
                "time": _timestamp_value(time_value),
                "disease_id": disease_id,
                "country_id": country_id,
            },
        )
    ).scalar()


async def _current_series_observation(
    db,
    *,
    time_value: object,
    series_code: str,
    geography_key: str,
    dimension_key: str,
    for_update: bool = False,
):
    lock_clause = "FOR UPDATE" if for_update else ""
    return (
        await db.execute(
            text(f"""
                SELECT to_jsonb(observation)
                FROM disease_series_observations observation
                WHERE time = CAST(:time AS timestamptz)
                  AND series_code = :series_code
                  AND geography_key = :geography_key
                  AND dimension_key = :dimension_key
                {lock_clause}
                """),
            {
                "time": _timestamp_value(time_value),
                "series_code": series_code,
                "geography_key": geography_key,
                "dimension_key": dimension_key,
            },
        )
    ).scalar()


def _same_observation_content(left: object, right: object) -> bool:
    """Compare complete post-migration records, failing closed on invalid JSON.

    Restore safety depends on the audit ledger's ``after_data`` being an exact
    before-image guard for the current row.  Comparing only cases/raw_data can
    silently overwrite later changes to deaths, metadata, provenance, or any
    other disease-record field.
    """

    def _strict_json_object(value: object) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            return dict(parsed) if isinstance(parsed, dict) else None
        return None

    a = _strict_json_object(left)
    b = _strict_json_object(right)
    return a is not None and b is not None and a == b


async def restore_migration(
    *, migration_key: str, run_id: str | None = None, apply: bool = False
) -> dict[str, Any]:
    if apply and not run_id:
        raise ValueError("Restore --apply requires an exact migration run_id")
    async with get_db() as db:
        if not await _audit_table_exists(db):
            return {
                "mode": "apply" if apply else "dry_run",
                "status": "not_applied",
                "migration_key": migration_key,
                "selected": 0,
            }
        await _acquire_restore_lock(db)
        rows = await _load_selected(db, migration_key=migration_key, run_id=run_id)
        blocked: list[dict[str, Any]] = []
        ready: list[dict[str, Any]] = []
        for audit in rows:
            identity = _json_object(audit["identity"])
            before = _json_object(audit["before_data"])
            expected_after = audit["after_data"]
            entity_table = str(audit.get("entity_table") or "disease_records")
            errors: list[str] = []
            expects_deleted_row = False
            if entity_table == "disease_records":
                old_id = int(before["disease_id"])
                country_id = int(before["country_id"])
                time_value = before["time"]
                existing_old = await _current_record(
                    db,
                    time_value=time_value,
                    disease_id=old_id,
                    country_id=country_id,
                    for_update=True,
                )
                current_id = (
                    int(identity["new_disease_id"])
                    if audit["operation"] == "fact_remap"
                    else old_id
                )
                current = (
                    existing_old
                    if current_id == old_id
                    else await _current_record(
                        db,
                        time_value=time_value,
                        disease_id=current_id,
                        country_id=country_id,
                        for_update=True,
                    )
                )
                if existing_old is not None and current_id != old_id:
                    errors.append("old fact key is already occupied")
                expects_deleted_row = (
                    audit["operation"] == "legacy_projection_repair"
                    and expected_after is None
                )
            elif (
                entity_table == "disease_series_observations"
                and audit["operation"] == "series_geography_remap"
            ):
                time_value = before["time"]
                series_code = str(before["series_code"])
                dimension_key = str(before["dimension_key"])
                old_geography_key = str(before["geography_key"])
                new_geography_key = str(identity["new_geography_key"])
                existing_old = await _current_series_observation(
                    db,
                    time_value=time_value,
                    series_code=series_code,
                    geography_key=old_geography_key,
                    dimension_key=dimension_key,
                    for_update=True,
                )
                current = await _current_series_observation(
                    db,
                    time_value=time_value,
                    series_code=series_code,
                    geography_key=new_geography_key,
                    dimension_key=dimension_key,
                    for_update=True,
                )
                if existing_old is not None:
                    errors.append("old series fact key is already occupied")
            elif (
                entity_table == "disease_series_observations"
                and audit["operation"] == "series_resident_backfill_insert"
            ):
                if not _is_empty_json_object(audit["before_data"]):
                    errors.append(
                        "resident backfill insert audit before-image is not empty"
                    )
                current = await _current_series_observation(
                    db,
                    time_value=identity["time"],
                    series_code=str(identity["series_code"]),
                    geography_key=str(identity["geography_key"]),
                    dimension_key=str(identity["dimension_key"]),
                    for_update=True,
                )
            else:
                current = None
                errors.append(
                    "unsupported audit entity/operation: "
                    f"{entity_table}/{audit['operation']}"
                )
            if expects_deleted_row:
                if current is not None:
                    errors.append("expected post-migration fact to be absent")
            elif expected_after is None:
                errors.append("audit post-migration after-image is missing")
            elif current is None:
                errors.append("expected post-migration fact is missing")
            elif not _same_observation_content(current, expected_after):
                errors.append("post-migration observation content has changed")
            item = {
                "audit_id": int(audit["id"]),
                "identity_key": audit["identity_key"],
                "operation": audit["operation"],
                "entity_table": entity_table,
                "errors": errors,
            }
            (blocked if errors else ready).append(item)

        if blocked:
            await db.rollback()
            return {
                "mode": "apply" if apply else "dry_run",
                "status": "blocked",
                "migration_key": migration_key,
                "selected": len(rows),
                "ready": len(ready),
                "blocked": blocked[:20],
            }

        restored = 0
        if apply:
            by_id = {int(row["id"]): row for row in rows}
            for item in ready:
                audit = by_id[item["audit_id"]]
                identity = _json_object(audit["identity"])
                before = _json_object(audit["before_data"])
                entity_table = str(audit.get("entity_table") or "disease_records")
                if entity_table == "disease_series_observations":
                    is_insert_restore = (
                        audit["operation"] == "series_resident_backfill_insert"
                    )
                    time_value = (
                        identity["time"] if is_insert_restore else before["time"]
                    )
                    series_code = str(
                        identity["series_code"]
                        if is_insert_restore
                        else before["series_code"]
                    )
                    geography_key = str(
                        identity["geography_key"]
                        if is_insert_restore
                        else identity["new_geography_key"]
                    )
                    dimension_key = str(
                        identity["dimension_key"]
                        if is_insert_restore
                        else before["dimension_key"]
                    )
                    delete_result = await db.execute(
                        text("""
                            DELETE FROM disease_series_observations
                            WHERE time = CAST(:time AS timestamptz)
                              AND series_code = :series_code
                              AND geography_key = :geography_key
                              AND dimension_key = :dimension_key
                            """),
                        {
                            "time": _timestamp_value(time_value),
                            "series_code": series_code,
                            "geography_key": geography_key,
                            "dimension_key": dimension_key,
                        },
                    )
                    if int(delete_result.rowcount or 0) != 1:
                        raise RuntimeError(
                            "Restore target changed after it was locked: "
                            f"audit_id={item['audit_id']}"
                        )
                    if not is_insert_restore:
                        await db.execute(
                            text("""
                                INSERT INTO disease_series_observations
                                SELECT (jsonb_populate_record(
                                    NULL::disease_series_observations,
                                    CAST(:before_data AS jsonb)
                                )).*
                                """),
                            {
                                "before_data": json.dumps(
                                    before, ensure_ascii=False, default=str
                                )
                            },
                        )
                else:
                    old_id = int(before["disease_id"])
                    current_id = (
                        int(identity["new_disease_id"])
                        if audit["operation"] == "fact_remap"
                        else old_id
                    )
                    expects_deleted_row = (
                        audit["operation"] == "legacy_projection_repair"
                        and audit["after_data"] is None
                    )
                    if not expects_deleted_row:
                        delete_result = await db.execute(
                            text("""
                                DELETE FROM disease_records
                                WHERE time = CAST(:time AS timestamptz)
                                  AND disease_id = :disease_id
                                  AND country_id = :country_id
                                """),
                            {
                                "time": _timestamp_value(before["time"]),
                                "disease_id": current_id,
                                "country_id": int(before["country_id"]),
                            },
                        )
                        if int(delete_result.rowcount or 0) != 1:
                            raise RuntimeError(
                                "Restore target changed after it was locked: "
                                f"audit_id={item['audit_id']}"
                            )
                    await db.execute(
                        text("""
                            INSERT INTO disease_records
                            SELECT (jsonb_populate_record(
                                NULL::disease_records, CAST(:before_data AS jsonb)
                            )).*
                            """),
                        {
                            "before_data": json.dumps(
                                before, ensure_ascii=False, default=str
                            )
                        },
                    )
                audit_result = await db.execute(
                    text("""
                        UPDATE disease_migration_audit
                        SET restored_at = CURRENT_TIMESTAMP
                        WHERE id = :audit_id
                          AND restored_at IS NULL
                        """),
                    {"audit_id": item["audit_id"]},
                )
                if int(audit_result.rowcount or 0) != 1:
                    raise RuntimeError(
                        "Restore audit row changed after selection: "
                        f"audit_id={item['audit_id']}"
                    )
                restored += 1
            await db.commit()
        else:
            await db.rollback()

    return {
        "mode": "apply" if apply else "dry_run",
        "status": "restored" if apply else "ready",
        "migration_key": migration_key,
        "selected": len(rows),
        "restored": restored,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="list restorable migrations"
    )
    parser.add_argument("--migration-key", help="exact migration key to restore")
    parser.add_argument(
        "--run-id",
        help="restrict to one migration run; required with --apply",
    )
    parser.add_argument(
        "--apply", action="store_true", help="apply the restore transaction"
    )
    args = parser.parse_args()
    if args.apply and not args.run_id:
        parser.error("--apply requires --run-id to select one exact migration run")
    if args.list:
        result = asyncio.run(list_migrations())
    else:
        if not args.migration_key:
            parser.error("--migration-key is required unless --list is used")
        result = asyncio.run(
            restore_migration(
                migration_key=args.migration_key,
                run_id=args.run_id,
                apply=args.apply,
            )
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") not in {"blocked"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
