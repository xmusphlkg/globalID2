#!/usr/bin/env python3
"""Reconcile every frozen Colombia SIVIGILA source row with the database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.imports.import_surveillance_archive import load_archive_manifest
from src.core.database import dispose_database, get_db
from src.data.processors.co import ColombiaINSSivigilaUpdater
from src.data.storage.series.identity import _dimension_key
from src.services.ingestion.countries import co

SOURCE_ID = "SRC_CO_INS_SIVIGILA"
SAMPLE_LIMIT = 20


def _fingerprint(row: dict) -> str:
    payload = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _expected_record(row: dict) -> tuple:
    return (
        _fingerprint(row),
        row["Date"],
        row["GeographyKey"],
        row["SourceDiseaseCode"],
        row["DefinitionVersion"],
        float(row["Cases"]),
        _dimension_key(json.loads(row["Dimensions"])),
    )


def _different_fields(expected: tuple, actual: tuple) -> list[str]:
    labels = (
        "raw_data",
        "time",
        "geography_key",
        "source_series_code",
        "definition_version",
        "value",
        "dimension_key",
    )
    return [
        label for label, left, right in zip(labels, expected, actual) if left != right
    ]


def _source_rows(
    state: dict, year: int, updater, spool_path: Path
) -> tuple[sqlite3.Connection, int, int, bool]:
    total = int(state["identity"]["year_counts"][str(year)])
    page_limit = int(state["identity"]["page_limit"])
    version = state["identity"]["dataset_version"]
    spool = sqlite3.connect(spool_path, check_same_thread=False)
    spool.execute("PRAGMA journal_mode=OFF")
    spool.execute("PRAGMA synchronous=OFF")
    spool.execute("PRAGMA cache_size=-16384")
    spool.execute(
        "CREATE TABLE expected (record_id TEXT PRIMARY KEY, digest TEXT, "
        "report_date TEXT, geography TEXT, source_code TEXT, "
        "definition_version TEXT, value REAL, dimension_key TEXT) WITHOUT ROWID"
    )
    spool.execute("CREATE TABLE seen_db (record_id TEXT PRIMARY KEY) WITHOUT ROWID")
    applied_offsets = set()
    applied_rows = 0
    for key, batch in state["batches"].items():
        if int(batch["year"]) != year or not batch.get("applied"):
            continue
        offset = int(batch["offset"])
        if (
            key != f"{year}:{offset}"
            or offset in applied_offsets
            or offset % page_limit
        ):
            raise ValueError(f"Invalid or duplicate CO page identity: {key}")
        manifest = ROOT / batch["manifest"]
        artifacts = load_archive_manifest(manifest, "CO")
        fetched = co.organize(
            artifacts,
            updater=updater,
            start_year=year,
            end_year=year,
            page_offset=offset,
        )
        if fetched.source_audit["dataset_version"] != version:
            raise ValueError(f"CO {year}:{offset} belongs to another source version")
        if fetched.source_audit["year_source_rows"] != total or len(
            fetched.rows
        ) != int(batch["rows"]):
            raise ValueError(f"CO {year}:{offset} source count changed")
        try:
            spool.executemany(
                "INSERT INTO expected VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (row["SourceRecordID"], *_expected_record(row))
                    for row in fetched.rows
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Duplicate CO source record ID across pages in {year}:{offset}"
            ) from exc
        applied_offsets.add(offset)
        applied_rows += len(fetched.rows)
    complete_pages = applied_offsets == set(range(0, total, page_limit))
    return spool, applied_rows, len(applied_offsets), complete_pages


async def verify(state: dict, *, year_filter: int | None = None) -> dict:
    counts = {
        int(year): int(total)
        for year, total in state["identity"]["year_counts"].items()
    }
    if not counts or any(total <= 0 for total in counts.values()):
        raise ValueError("CO source year counts must be positive")
    if year_filter is not None:
        if year_filter not in counts:
            raise ValueError(f"CO {year_filter} is outside frozen source years")
        counts = {year_filter: counts[year_filter]}
    updater = ColombiaINSSivigilaUpdater()
    years = {}
    try:
        async with get_db() as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            for year, official_rows in sorted(counts.items()):
                with TemporaryDirectory(prefix=f"co-verify-{year}-") as temporary:
                    (
                        spool,
                        source_rows,
                        page_count,
                        complete_pages,
                    ) = await asyncio.to_thread(
                        _source_rows,
                        state,
                        year,
                        updater,
                        Path(temporary) / "source.sqlite",
                    )
                    source_unique_rows = spool.execute(
                        "SELECT count(*) FROM expected"
                    ).fetchone()[0]
                    database_rows = municipality = unknown = exterior = 0
                    mismatched = unexpected = duplicate = 0
                    mismatch_examples = []
                    unexpected_examples = []
                    duplicate_examples = []
                    query = text("""
                    SELECT obs.raw_data, obs.value, obs.time, obs.geography_key,
                           obs.dimension_key, series.source_series_code,
                           series.definition_version
                    FROM disease_series_observations obs
                    JOIN disease_surveillance_series series USING (series_code)
                    WHERE series.country_code='CO' AND series.source_system=:source_id
                      AND obs.time >= :start_date AND obs.time < :end_date
                """)
                    stream = await db.stream(
                        query,
                        {
                            "source_id": SOURCE_ID,
                            "start_date": date(year, 1, 1),
                            "end_date": date(year + 1, 1, 1),
                        },
                    )
                    async for (
                        raw,
                        value,
                        when,
                        geography,
                        dimension_key,
                        code,
                        version,
                    ) in stream:
                        database_rows += 1
                        municipality += geography.startswith(
                            "country:CO:source-municipality:"
                        )
                        unknown += geography.startswith(
                            "source:SRC_CO_INS_SIVIGILA:unknown-location:"
                        )
                        exterior += geography.startswith(
                            "source:SRC_CO_INS_SIVIGILA:exterior:"
                        )
                        record_id = (
                            raw.get("SourceRecordID") if isinstance(raw, dict) else None
                        )
                        if record_id is not None:
                            inserted = spool.execute(
                                "INSERT OR IGNORE INTO seen_db VALUES (?)", (record_id,)
                            ).rowcount
                            if not inserted:
                                duplicate += 1
                                if len(duplicate_examples) < SAMPLE_LIMIT:
                                    duplicate_examples.append(record_id)
                        source = (
                            spool.execute(
                                "SELECT digest, report_date, geography, source_code, "
                                "definition_version, value, dimension_key FROM expected "
                                "WHERE record_id=?",
                                (record_id,),
                            ).fetchone()
                            if record_id is not None
                            else None
                        )
                        if source is None:
                            unexpected += 1
                            if len(unexpected_examples) < SAMPLE_LIMIT:
                                unexpected_examples.append(record_id)
                            continue
                        spool.execute(
                            "DELETE FROM expected WHERE record_id=?", (record_id,)
                        )
                        actual = (
                            _fingerprint(raw),
                            when.date().isoformat(),
                            geography,
                            code,
                            version,
                            value,
                            dimension_key,
                        )
                        fields = _different_fields(source, actual)
                        if fields:
                            mismatched += 1
                            if len(mismatch_examples) < SAMPLE_LIMIT:
                                mismatch_examples.append(
                                    {"source_record_id": record_id, "fields": fields}
                                )
                    missing = spool.execute("SELECT count(*) FROM expected").fetchone()[
                        0
                    ]
                    year_complete = (
                        complete_pages
                        and source_rows
                        == source_unique_rows
                        == database_rows
                        == official_rows
                        and not (missing or unexpected or mismatched or duplicate)
                    )
                    years[str(year)] = {
                        "official_rows": official_rows,
                        "source_rows": source_rows,
                        "source_unique_rows": source_unique_rows,
                        "database_rows": database_rows,
                        "municipality_rows": municipality,
                        "unknown_location_rows": unknown,
                        "exterior_rows": exterior,
                        "applied_pages": page_count,
                        "applied_source_rows": source_rows,
                        "complete_pages": complete_pages,
                        "missing_database_rows": missing,
                        "unexpected_database_rows": unexpected,
                        "duplicate_database_record_ids": duplicate,
                        "mismatched_rows": mismatched,
                        "missing_source_record_id_examples": [
                            row[0]
                            for row in spool.execute(
                                "SELECT record_id FROM expected LIMIT ?",
                                (SAMPLE_LIMIT,),
                            )
                        ],
                        "unexpected_database_record_id_examples": unexpected_examples,
                        "duplicate_database_record_id_examples": duplicate_examples,
                        "mismatched_row_examples": mismatch_examples,
                        "complete": year_complete,
                    }
                    print(
                        json.dumps(
                            {
                                "year": year,
                                "complete": year_complete,
                                "source_rows": source_rows,
                                "database_rows": database_rows,
                                "mismatched_rows": mismatched,
                            }
                        ),
                        flush=True,
                    )
                    spool.close()
    finally:
        await dispose_database()
    return {
        "source": "co_ins_sivigila_historical",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": state["identity"]["dataset_version"],
        "verification": "frozen_artifact_full_row_and_physical_identity",
        "scope": "year_only" if year_filter is not None else "all_source_years",
        "years": years,
        "complete": year_filter is None
        and state.get("status") == "complete"
        and all(row["complete"] for row in years.values()),
    }


def _year_state(state: dict, year: int) -> dict:
    return {
        "dataset_version": state["identity"]["dataset_version"],
        "year_count": state["identity"]["year_counts"].get(str(year)),
        "page_limit": state["identity"]["page_limit"],
        "batches": {
            key: batch
            for key, batch in state["batches"].items()
            if int(batch["year"]) == year
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state", type=Path, default=ROOT / "data/current/co/history-state.json"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--year", type=int, help="Verify one year without replacing full acceptance"
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    state_bytes = args.state.read_bytes()
    state = json.loads(state_bytes)
    report = asyncio.run(verify(state, year_filter=args.year))
    current_bytes = args.state.read_bytes()
    if (args.year is None and current_bytes != state_bytes) or (
        args.year is not None
        and _year_state(json.loads(current_bytes), args.year)
        != _year_state(state, args.year)
    ):
        raise SystemExit(
            "CO history state changed during verification; rerun against a stable snapshot"
        )
    output = args.output or (
        ROOT / "data/current/co/years" / f"{args.year}-acceptance.json"
        if args.year is not None
        else ROOT / "data/current/co/database_acceptance.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "years": report["years"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    if args.require_complete and not (
        report["complete"]
        or (args.year is not None and report["years"][str(args.year)]["complete"])
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
