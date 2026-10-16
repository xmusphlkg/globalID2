#!/usr/bin/env python3
"""Correct CHFL IDD observation identities without changing reported values.

The FOPH's CHFL configuration covers Switzerland *and* Liechtenstein. Older
ingestion wrote those facts under ``country:CH:national``. The default command
only audits. ``--apply`` moves current facts and their revision snapshots in
one transaction, recording every changed row in disease_migration_audit.
``--restore RUN_ID`` reverses that exact audited run after collision checks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from src.core import get_database
from src.core.disease_mutation_lock import acquire_disease_data_mutation_lock
from src.data.crawlers.ch import CHFL_GEOGRAPHY_KEY

SOURCE_ID = "SRC_CH_FOPH_IDD"
OLD_KEY = "country:CH:national"
NEW_KEY = CHFL_GEOGRAPHY_KEY
MIGRATION_KEY = "ch_foph_idd_chfl_geography_v1"
TABLES = (
    "disease_series_observations",
    "disease_series_observation_revisions",
)


def _identity_columns(table: str) -> tuple[str, ...]:
    columns = ("time", "series_code", "dimension_key")
    return (*columns, "recorded_at") if table == TABLES[1] else columns


async def _scope(db, table: str, *, key: str) -> list[dict]:
    query = text(f"""
        SELECT o.id, o.time, o.series_code, o.dimension_key
               {', o.recorded_at' if table == TABLES[1] else ''}
        FROM {table} o
        JOIN disease_surveillance_series s ON s.series_code=o.series_code
        WHERE s.source_system=:source_id AND s.country_code='CH'
          AND o.geography_key=:key AND o.raw_data->>'Geography'='CHFL'
        ORDER BY o.id
    """)
    return [dict(row) for row in (await db.execute(query, {"source_id": SOURCE_ID, "key": key})).mappings()]


async def _collisions(db, table: str, *, from_key: str, to_key: str) -> int:
    match = " AND ".join(f"target.{col}=source.{col}" for col in _identity_columns(table))
    query = text(f"""
        SELECT count(*) FROM {table} source
        JOIN disease_surveillance_series s ON s.series_code=source.series_code
        JOIN {table} target ON {match} AND target.geography_key=:to_key
        WHERE s.source_system=:source_id AND s.country_code='CH'
          AND source.geography_key=:from_key
          AND source.raw_data->>'Geography'='CHFL'
    """)
    return int((await db.execute(query, {
        "source_id": SOURCE_ID, "from_key": from_key, "to_key": to_key,
    })).scalar_one())


async def audit(db) -> dict:
    tables = {}
    for table in TABLES:
        old = await _scope(db, table, key=OLD_KEY)
        new = await _scope(db, table, key=NEW_KEY)
        collisions = await _collisions(db, table, from_key=OLD_KEY, to_key=NEW_KEY)
        tables[table] = {
            "mislabelled_chfl_rows": len(old),
            "already_correct_chfl_rows": len(new),
            "target_identity_collisions": collisions,
        }
    legacy = (await db.execute(text("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE dr.raw_data::jsonb->0->>'Geography'='CH') AS ch_only,
               count(*) FILTER (WHERE dr.raw_data::jsonb->0->>'Geography'='CHFL') AS chfl,
               count(*) FILTER (WHERE COALESCE(dr.raw_data::jsonb->0->>'Geography','')
                                      NOT IN ('CH','CHFL')) AS other
        FROM disease_records dr JOIN countries c ON c.id=dr.country_id
        WHERE c.code='CH' AND dr.data_source='Switzerland FOPH IDD Mandatory Reporting System'
    """))).mappings().one()
    return {
        "source_id": SOURCE_ID, "old_key": OLD_KEY, "new_key": NEW_KEY,
        "tables": tables,
        "legacy_disease_records": {key: int(value) for key, value in legacy.items()},
        "legacy_public_ch_predicate": "raw_data::jsonb->0->>'Geography' = 'CH'",
        "safe_to_apply": all(not x["target_identity_collisions"] for x in tables.values()),
    }


async def _ensure_audit_schema(db) -> None:
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


async def apply(db) -> dict:
    await acquire_disease_data_mutation_lock(db)
    before = await audit(db)
    if not before["safe_to_apply"]:
        raise RuntimeError("CHFL geography target identity collision; no rows changed")
    run_id = datetime.now(timezone.utc).strftime("CHFL-%Y%m%dT%H%M%S") + f"-{uuid.uuid4().hex[:8]}"
    changed = {}
    if any(x["mislabelled_chfl_rows"] for x in before["tables"].values()):
        await _ensure_audit_schema(db)
    for table in TABLES:
        expected = before["tables"][table]["mislabelled_chfl_rows"]
        if not expected:
            changed[table] = 0
            continue
        await db.execute(text(f"""
            INSERT INTO disease_migration_audit (
                migration_run_id, migration_key, entity_table, operation,
                identity_key, identity, before_data, after_data, reason
            )
            SELECT :run_id, :migration_key, :table_name, 'geography_identity_repair',
                   :table_name || ':' || o.id::text,
                   jsonb_build_object('id',o.id,'time',o.time,'series_code',o.series_code,
                                      'dimension_key',o.dimension_key)
                   {"|| jsonb_build_object('recorded_at',o.recorded_at)" if table == TABLES[1] else ''},
                   jsonb_build_object('geography_key',:old_key),
                   jsonb_build_object('geography_key',:new_key),
                   'FOPH CHFL means Switzerland plus Liechtenstein; source values are unchanged'
            FROM {table} o
            JOIN disease_surveillance_series s ON s.series_code=o.series_code
            WHERE s.source_system=:source_id AND s.country_code='CH'
              AND o.geography_key=:old_key AND o.raw_data->>'Geography'='CHFL'
        """), {"run_id": run_id, "migration_key": MIGRATION_KEY,
               "table_name": table, "source_id": SOURCE_ID,
               "old_key": OLD_KEY, "new_key": NEW_KEY})
        result = await db.execute(text(f"""
            UPDATE {table} o SET geography_key=:new_key
            FROM disease_surveillance_series s
            WHERE s.series_code=o.series_code AND s.source_system=:source_id
              AND s.country_code='CH' AND o.geography_key=:old_key
              AND o.raw_data->>'Geography'='CHFL'
        """), {"source_id": SOURCE_ID, "old_key": OLD_KEY, "new_key": NEW_KEY})
        changed[table] = int(result.rowcount)
        if changed[table] != expected:
            raise RuntimeError(f"{table}: expected {expected} changes, got {changed[table]}")
    after = await audit(db)
    for table in TABLES:
        if after["tables"][table]["mislabelled_chfl_rows"] != 0:
            raise RuntimeError(f"{table}: CHFL rows still use the CH-only key")
        if after["tables"][table]["already_correct_chfl_rows"] != (
            before["tables"][table]["already_correct_chfl_rows"] + changed[table]
        ):
            raise RuntimeError(f"{table}: after count differs from expected")
    return {"mode": "applied", "run_id": run_id, "changed": changed,
            "before": before, "after": after}


async def restore(db, run_id: str) -> dict:
    await acquire_disease_data_mutation_lock(db)
    await _ensure_audit_schema(db)
    changed = {}
    for table in TABLES:
        rows = (await db.execute(text("""
            SELECT identity_key, (identity->>'id')::bigint AS id
            FROM disease_migration_audit
            WHERE migration_run_id=:run_id AND migration_key=:migration_key
              AND entity_table=:table_name AND operation='geography_identity_repair'
              AND restored_at IS NULL
        """), {"run_id": run_id, "migration_key": MIGRATION_KEY, "table_name": table})).mappings().all()
        if not rows:
            changed[table] = 0
            continue
        match = " AND ".join(f"other.{col}=current.{col}" for col in _identity_columns(table))
        collisions = int((await db.execute(text(f"""
            SELECT count(*) FROM {table} current
            JOIN disease_migration_audit audit
              ON audit.entity_table=:table_name AND audit.migration_run_id=:run_id
             AND audit.migration_key=:migration_key AND audit.operation='geography_identity_repair'
             AND audit.restored_at IS NULL AND (audit.identity->>'id')::bigint=current.id
            JOIN {table} other ON {match} AND other.geography_key=:old_key
            WHERE current.geography_key=:new_key
        """), {"run_id": run_id, "migration_key": MIGRATION_KEY,
               "table_name": table, "old_key": OLD_KEY, "new_key": NEW_KEY})).scalar_one())
        if collisions:
            raise RuntimeError(f"{table}: {collisions} restoration identity collisions")
        result = await db.execute(text(f"""
            UPDATE {table} current SET geography_key=:old_key
            FROM disease_migration_audit audit
            WHERE audit.entity_table=:table_name AND audit.migration_run_id=:run_id
              AND audit.migration_key=:migration_key AND audit.operation='geography_identity_repair'
              AND audit.restored_at IS NULL AND (audit.identity->>'id')::bigint=current.id
              AND current.geography_key=:new_key
              AND current.raw_data->>'Geography'='CHFL'
        """), {"run_id": run_id, "migration_key": MIGRATION_KEY,
               "table_name": table, "old_key": OLD_KEY, "new_key": NEW_KEY})
        changed[table] = int(result.rowcount)
        if changed[table] != len(rows):
            raise RuntimeError(f"{table}: {len(rows)} audit rows but {changed[table]} restored")
        await db.execute(text("""
            UPDATE disease_migration_audit SET restored_at=CURRENT_TIMESTAMP
            WHERE migration_run_id=:run_id AND migration_key=:migration_key
              AND entity_table=:table_name AND operation='geography_identity_repair'
              AND restored_at IS NULL
        """), {"run_id": run_id, "migration_key": MIGRATION_KEY, "table_name": table})
    return {"mode": "restored", "run_id": run_id, "changed": changed,
            "after": await audit(db)}


async def run(*, apply_changes: bool, restore_run_id: str | None) -> dict:
    async with get_database() as db:
        if restore_run_id:
            result = await restore(db, restore_run_id)
            await db.commit()
        elif apply_changes:
            result = await apply(db)
            await db.commit()
        else:
            result = {"mode": "audit_only", **(await audit(db))}
            await db.rollback()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="audit and migrate in one transaction")
    mode.add_argument("--restore", metavar="RUN_ID", help="reverse one audited migration run")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply_changes=args.apply,
                                     restore_run_id=args.restore)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
