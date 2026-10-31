"""Audit existing publication metadata without fetching or regenerating data.

Default: one PostgreSQL REPEATABLE READ, READ ONLY transaction, then rollback.
--apply records only detected changes as pending surveillance scopes, and can
restore missing country_publish_state from consistent existing public rows.
It never changes observations or public curves, queues collection or release,
or fabricates historical collect/organize stages. An actual completed
PROCESS_DATA migration task and its single manual store stage record writes.

Watermarks and stored hashes do not prove source content equivalence. Missing
population, mapping, and policy fingerprints are always reported as uncertain;
uncertainty alone never marks all existing countries dirty.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.country_library import get_country_bootstrap_config
from src.domain import (
    CountryPublishState,
    PublishDirty,
    RunStage,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.generation.public_series_store import (
    PROJECTION_VERSION,
    country_publish_hash,
)


def utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def load_snapshot(connection) -> dict:
    """Read metadata only; source values, public arrays, and task history stay out."""
    queries = {
        "countries": "SELECT code AS country_code, is_active FROM countries",
        "observations": "SELECT split_part(geography_key, ':', 2) AS country_code, count(*) AS row_count, max(updated_at) AS source_high_water FROM disease_series_observations WHERE geography_key LIKE :pattern GROUP BY country_code",
        "legacy": "SELECT c.code AS country_code, count(*) AS row_count FROM disease_records r JOIN countries c ON c.id=r.country_id GROUP BY c.code",
        "public": "SELECT country_code, disease_id, content_hash, projection_version, source_high_water, point_count, updated_at FROM public_disease_series ORDER BY country_code, disease_id",
        "states": "SELECT country_code, source_high_water, series_count, point_count, content_hash, projection_version, published_at FROM country_publish_state",
        "pending": "SELECT DISTINCT scope_key AS country_code FROM publish_dirty WHERE line='surveillance' AND published_at IS NULL",
    }
    return {
        name: [
            dict(row)
            for row in connection.execute(
                text(query), {"pattern": "country:%:national"}
            ).mappings()
        ]
        for name, query in queries.items()
    }


def analyze(snapshot: dict) -> dict:
    countries = {row["country_code"]: row for row in snapshot["countries"]}
    observations = {row["country_code"]: row for row in snapshot["observations"]}
    legacy = {row["country_code"]: row for row in snapshot["legacy"]}
    states = {row["country_code"]: row for row in snapshot["states"]}
    public = defaultdict(list)
    for row in snapshot["public"]:
        public[row["country_code"]].append(row)
    pending = {row["country_code"] for row in snapshot["pending"]}
    scopes = sorted(set(observations) | set(legacy) | set(states) | set(public))
    results = []
    for code in scopes:
        source = observations.get(code, {})
        rows, state = public.get(code, []), states.get(code)
        high_water = utc(source.get("source_high_water"))
        row_waters = {utc(row["source_high_water"]) for row in rows}
        row_versions = {row["projection_version"] for row in rows}
        digest = country_publish_hash(row["content_hash"] for row in rows)
        points = sum(row["point_count"] for row in rows)
        public_consistent = (
            len(row_waters) <= 1
            and row_versions <= {PROJECTION_VERSION}
            and all(
                re.fullmatch(r"[0-9a-fA-F]{64}", row["content_hash"]) for row in rows
            )
        )
        eligible = (
            bool(countries.get(code, {}).get("is_active"))
            and get_country_bootstrap_config(code).get("public_release_enabled", True)
            is not False
        )
        reasons, seed = [], None
        published_water = (
            utc(state["source_high_water"])
            if state
            else (next(iter(row_waters)) if len(row_waters) == 1 else None)
        )
        state_matches = bool(state) and (
            state["projection_version"] == PROJECTION_VERSION
            and state["content_hash"] == digest
            and state["series_count"] == len(rows)
            and state["point_count"] == points
            and public_consistent
            and (not rows or row_waters == {published_water})
        )
        if state:
            if state["projection_version"] != PROJECTION_VERSION:
                reasons.append("projection_version_changed")
            if not state_matches:
                reasons.append("public_state_metadata_mismatch")
        elif rows:
            if public_consistent:
                published_at = max(utc(row["updated_at"]) for row in rows)
                seed = {
                    "country_code": code,
                    "source_high_water": published_water,
                    "series_count": len(rows),
                    "point_count": points,
                    "content_hash": digest,
                    "projection_version": PROJECTION_VERSION,
                    "published_at": published_at,
                }
            else:
                reasons.append("inconsistent_public_snapshot_without_state")
        elif source.get("row_count") or legacy.get(code, {}).get("row_count"):
            reasons.append("source_has_no_publication_snapshot")
        if (state or rows) and high_water != published_water:
            reasons.append("observation_watermark_changed")
        uncertainty = [
            "population_mapping_policy_fingerprints_unavailable",
            "watermark_cannot_detect_all_deletions_or_backdated_edits",
            "public_row_hashes_not_recomputed_from_arrays",
        ]
        if legacy.get(code, {}).get("row_count"):
            uncertainty.append("legacy_records_have_no_change_watermark")
        results.append(
            {
                "scope_key": code,
                "public_release_enabled": eligible,
                "source": {
                    "observation_rows": source.get("row_count", 0),
                    "legacy_rows": legacy.get(code, {}).get("row_count", 0),
                    "high_water": high_water,
                },
                "public": {
                    "series_count": len(rows),
                    "point_count": points,
                    "content_hash": digest,
                    "versions": sorted(row_versions),
                    "high_waters": sorted(
                        (value.isoformat() if value else None for value in row_waters),
                        key=lambda value: value or "",
                    ),
                },
                "state": state,
                "state_matches_public_metadata": state_matches,
                "watermark_matches": (state is not None or bool(rows))
                and high_water == published_water,
                "dirty_reasons": reasons,
                "already_pending": code in pending,
                "mark_dirty": eligible and bool(reasons) and code not in pending,
                "seed_state": seed if eligible else None,
                "uncertain": uncertainty,
            }
        )
    return {
        "projection_version": PROJECTION_VERSION,
        "summary": {
            "scopes": len(results),
            "observation_scopes": len(observations),
            "public_scopes": len(public),
            "state_scopes": len(states),
            "metadata_and_watermark_match": sum(
                row["state_matches_public_metadata"] and row["watermark_matches"]
                for row in results
            ),
            "new_dirty_scopes": sum(row["mark_dirty"] for row in results),
            "missing_states_to_seed": sum(
                row["seed_state"] is not None for row in results
            ),
            "already_pending": sum(row["already_pending"] for row in results),
            "uncertain_scopes": len(results),
        },
        "scopes": results,
    }


def apply_actions(session: Session, report: dict) -> dict:
    """Participate in one migration transaction; never publish or collect."""
    pending = set(
        session.scalars(
            select(PublishDirty.scope_key).where(
                PublishDirty.line == "surveillance", PublishDirty.published_at.is_(None)
            )
        )
    )
    to_mark = [
        row
        for row in report["scopes"]
        if row["mark_dirty"] and row["scope_key"] not in pending
    ]
    to_seed = [
        row["seed_state"]
        for row in report["scopes"]
        if row["seed_state"]
        and session.get(CountryPublishState, row["scope_key"]) is None
    ]
    if not to_mark and not to_seed:
        return {"task_uuid": None, "dirty_inserted": 0, "states_seeded": 0}
    now = datetime.now(timezone.utc)
    task = Task(
        task_type=TaskType.PROCESS_DATA,
        task_name="Migrate existing publication metadata",
        status=TaskStatus.COMPLETED,
        priority=TaskPriority.NORMAL,
        started_at=now,
        completed_at=now,
        progress=100,
        input_data={
            "operation": "existing_publication_migration",
            "projection_version": PROJECTION_VERSION,
        },
        metadata_={
            "migration": True,
            "manual_store": True,
            "historical_collection_not_reconstructed": True,
        },
    )
    session.add(task)
    session.flush()
    for seed in to_seed:
        session.add(
            CountryPublishState(
                **{
                    **seed,
                    "source_high_water": utc(seed["source_high_water"]),
                    "published_at": utc(seed["published_at"]),
                }
            )
        )
    for row in to_mark:
        session.add(
            PublishDirty(
                line="surveillance",
                scope_key=row["scope_key"],
                source_task_uuid=task.task_uuid,
                marked_at=now,
            )
        )
    details = {
        "dirty_scopes": [row["scope_key"] for row in to_mark],
        "seeded_states": [row["country_code"] for row in to_seed],
    }
    session.add(
        RunStage(
            task_uuid=task.task_uuid,
            stage="store",
            attempt=1,
            status="completed",
            started_at=now,
            finished_at=now,
            metrics={"manual_store": True, **details},
        )
    )
    task.output_data = details
    session.flush()
    return {
        "task_uuid": task.task_uuid,
        "dirty_inserted": len(to_mark),
        "states_seeded": len(to_seed),
    }


def run(engine, *, apply: bool = False) -> dict:
    with engine.connect() as connection:
        if connection.dialect.name != "postgresql":
            raise ValueError("The existing-data audit requires PostgreSQL")
        try:
            if apply:
                # Serialize explicit bootstrap writers. READ COMMITTED gives
                # a waiting writer a fresh view after acquiring this lock.
                connection.execute(text("SELECT pg_advisory_xact_lock(1983103025)"))
            else:
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
            connection.execute(text("SET LOCAL statement_timeout = '90s'"))
            report = analyze(load_snapshot(connection))
            report["mode"] = "apply" if apply else "read_only"
            if apply:
                with Session(bind=connection) as session:
                    report["applied"] = apply_actions(session, report)
                    session.commit()
                connection.commit()
            else:
                connection.rollback()
            return report
        except BaseException:
            connection.rollback()
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit per-scope metadata and uncertainty as JSON",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply only detected dirty/state metadata changes; never collect or publish",
    )
    args = parser.parse_args(argv)
    from src.core.config import get_config

    engine = create_engine(get_config().database.url_sync)
    try:
        report = run(engine, apply=args.apply)
    except Exception as error:  # noqa: BLE001 -- CLI reports error types without leaking database credentials.
        print(
            json.dumps(
                {
                    "error_type": type(error).__name__,
                    "message": "Existing-data audit failed; no publication was performed",
                }
            )
        )
        return 1
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            json.dumps(
                {
                    "mode": report["mode"],
                    **report["summary"],
                    **({"applied": report["applied"]} if args.apply else {}),
                },
                ensure_ascii=False,
            )
        )
        for row in report["scopes"]:
            print(
                f"{row['scope_key']} dirty={row['mark_dirty']} seed_state={bool(row['seed_state'])} reasons={','.join(row['dirty_reasons']) or 'none'} uncertain={','.join(row['uncertain'])}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
