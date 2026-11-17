#!/usr/bin/env python3
"""Replay the reviewed MX weekly repair, preserving nine rejected old identities.

Default: execute the real store strategy twice in one transaction and roll back.
--apply: use the ordinary staged runner and a fenced foreground task lease.
The frozen baseline, correction evidence, replayed originals and candidate CSV
must agree before any observation changes. No publication is performed here.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text, update
from src.core.database import dispose_database, get_db, get_session_maker
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.data.processors.mx import MexicoDGEImportResult
from src.data.processors.mx_high_frequency import MexicoDGEHighFrequencyUpdater
from src.data.storage.series_observation_store import (
    SeriesObservationStore, _persist_observation_revisions,
    observation_publication_changes,
)
from src.domain import DiseaseSeriesObservation, DiseaseSeriesObservationRevision, DiseaseSurveillanceSeries
from src.domain.task import TaskStatus
from src.services.crawl_service import CrawlService
from src.services.ingestion.countries import mx_high_frequency
from src.services.ingestion.foreground import foreground_task_lease
from src.services.ingestion.runner import run_surveillance
from src.services.ingestion.stages import mark_dirty
from src.services.ingestion.strategies import UpdaterCommitStrategy
from scripts.import_high_frequency_sources import HighFrequencyReplay, create_source_import_task
from scripts.onboard_remaining_surveillance import fail_foreground_import

SOURCE = "SRC_MX_DGE_WEEKLY"
SCOPE = "mx_dge_weekly"
REPAIR = "mx_weekly_period_correction_20261117"
DEFAULT_CANDIDATE = ROOT / "data/processed/mx/high-frequency-weekly-repair-20261117"
DEFAULT_BACKUP = ROOT / "data/backups/br-mx-repair-20261117"
DEFAULT_MANIFEST = ROOT / "data/raw/mx/high-frequency-weekly-repair-20261117/artifacts_manifest.json"
KEYS = ("time", "series_code", "geography_key", "dimension_key")
STATE = ("value", "unit", "suppressed", "suppression_reason", "quality_status", "dimensions", "raw_data", "metadata")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(value):
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)).isoformat()


def identity(row):
    return (utc(row["time"]), *(row[k] for k in KEYS[1:]))


def normalize_snapshot(row):
    return {k: utc(v) if k in {"time", "created_at", "updated_at", "recorded_at"} and v is not None else v
            for k, v in row.items()}


def normalize_raw(row):
    result = {}
    for key, value in row.items():
        if key in {"Dimensions", "SourceProvenance"} and isinstance(value, str):
            value = json.loads(value)
        elif isinstance(value, bool):
            value = str(value).lower()
        result[key] = value
    return result


def raw_identity(row):
    return tuple(row[k] for k in ("Date", "SourceDiseaseCode", "DefinitionVersion", "GeographyKey"))


def compare_candidate(expected, actual):
    wanted = {raw_identity(r): normalize_raw(r) for r in expected}
    got = {raw_identity(r): normalize_raw(r) for r in actual}
    require(len(wanted) == len(expected) == len(got) == len(actual) == 1272,
            "Reviewed candidate must contain 1272 unique observations")
    require(wanted == got, "Frozen original replay differs from reviewed candidate CSV")


class RepairPlan:
    def __init__(self, candidate=DEFAULT_CANDIDATE, backup=DEFAULT_BACKUP, manifest=DEFAULT_MANIFEST):
        candidate, backup = Path(candidate), Path(backup)
        self.manifest = Path(manifest)
        self.paths = {"candidate": candidate / "observations.csv", "audit": candidate / "source_audit.json",
                      "corrections": candidate / "period_corrections.json", "baseline_receipt": backup / "baseline.json",
                      "baseline": backup / "mx_high_frequency_observations.jsonl.gz", "manifest": self.manifest}
        self.hashes = {k: sha(p) for k, p in self.paths.items()}
        baseline_receipt = json.loads(self.paths["baseline_receipt"].read_text())
        require(self.hashes["baseline"] == baseline_receipt["files"]["mx_high_frequency_observations"]["sha256"],
                "Frozen baseline SHA does not match its capture receipt")
        with gzip.open(self.paths["baseline"], "rt") as stream:
            self.baseline = [r for line in stream if (r := json.loads(line))["metadata"].get("source_id") == SOURCE]
        require(len(self.baseline) == 761, "Frozen baseline must contain 761 weekly observations")
        with self.paths["candidate"].open(encoding="utf-8-sig", newline="") as stream:
            self.rows = list(csv.DictReader(stream))
        compare_candidate(self.rows, self.rows)
        self.audit = json.loads(self.paths["audit"].read_text())
        require(self.audit["failed_count"] == 0 and len(self.audit["excluded_source_cells"]) == 16
                and self.audit["coverage_complete"] is False, "Reviewed source exclusions changed")
        document = json.loads(self.paths["corrections"].read_text())
        self.corrections = document["corrections"]
        require(document["count"] == len(self.corrections) == 9, "Exactly nine reviewed period corrections required")
        base_by_raw = {raw_identity(r["raw_data"]): r for r in self.baseline}
        new_by_raw = {raw_identity(r): r for r in self.rows}
        self.old = []
        for correction in self.corrections:
            before, after = correction["before"], correction["after"]
            require(correction["source_system"] == SOURCE and correction["old_date"] == before["Date"] == "2026-10-18"
                    and correction["new_date"] == after["Date"] == "2026-10-25", "Unexpected correction source/date")
            old = base_by_raw.get(raw_identity(before))
            require(old is not None and normalize_raw(old["raw_data"]) == normalize_raw(before),
                    "Correction before-row differs from immutable database baseline")
            require(normalize_raw(new_by_raw.get(raw_identity(after), {})) == normalize_raw(after),
                    "Correction after-row differs from reviewed candidate")
            require(correction["original_sha256"] == before["RawSHA256"] == after["RawSHA256"]
                    and correction["original_url"] == before["SourceURL"] == after["SourceURL"]
                    and str(correction["source_page"]) == before["SourcePage"],
                    "Correction original-page evidence differs")
            allowed = {"Date", "PeriodStart", "PeriodEnd", "SourceReportPeriodStart", "SourceReportPeriodEnd"}
            b, a = normalize_raw(before), normalize_raw(after)
            for field in allowed:
                b.pop(field); a.pop(field)
            b["Dimensions"] = {**b["Dimensions"], "source_epi_week": 43}
            if before["SourcePage"] != after["SourcePage"]:
                # The corrected amebic meningoencephalitis weekly TOTAL is
                # already printed in the same issue's national summary. Keep
                # its one verified identity and the equivalent-page proof.
                require(before["SourcePage"] == "46" and after["SourcePage"] == "9"
                        and before["RawDiseaseLabel"] == "Meningoencefalitis amebiana primaria§"
                        and after["RawDiseaseLabel"] == before["RawDiseaseLabel"].rstrip("§")
                        and after["Measure"] == "case_notifications"
                        and a["SourceProvenance"].get("equivalent_source_pages") == [9, 46],
                        "Changed source page is not the verified equivalent national summary")
                b["RawDiseaseLabel"] = a["RawDiseaseLabel"]
                b["Dimensions"].update(source_disease_label=a["RawDiseaseLabel"],
                                       table_family="national_summary", cumulative_start=None)
                b["SourcePage"], b["SourceProvenance"] = a["SourcePage"], a["SourceProvenance"]
            require(b == a and before["Cases"] == after["Cases"], "Correction changes more than the explicit week")
            require(raw_identity(before) not in new_by_raw, "Obsolete period remains in candidate")
            self.old.append(old)
        require(len({identity(r) for r in self.old}) == 9, "Duplicate correction identity")
        self.plan_id = hashlib.sha256(json.dumps(self.hashes, sort_keys=True).encode()).hexdigest()

    def verify_files(self):
        require(self.hashes == {k: sha(p) for k, p in self.paths.items()}, "Reviewed inputs changed while repair was running")


async def weekly_rows(db, *, lock=False):
    query = select(DiseaseSeriesObservation.__table__).join(
        DiseaseSurveillanceSeries, DiseaseSurveillanceSeries.series_code == DiseaseSeriesObservation.series_code
    ).where(DiseaseSurveillanceSeries.country_code == "MX", DiseaseSurveillanceSeries.source_system == SOURCE)
    if lock:
        query = query.with_for_update(of=DiseaseSeriesObservation)
    return [dict(row) for row in (await db.execute(query)).mappings()]


async def protected_state(db):
    return dict((await db.execute(text("""
        SELECT count(*) AS rows, md5(coalesce(string_agg(md5(to_jsonb(o)::text), '' ORDER BY o.id), '')) AS digest
        FROM disease_series_observations o JOIN disease_surveillance_series s USING(series_code)
        WHERE s.country_code='MX' AND s.source_system<>:source
    """), {"source": SOURCE})).mappings().one())


class MexicoWeeklyPeriodRepair(UpdaterCommitStrategy):
    def __init__(self, plan, lease=None):
        self.plan, self.lease, self.receipt = plan, lease, {}

    def rejection(self, old, correction, target):
        result = dict(old)
        result["quality_status"] = "rejected"
        result["metadata"] = {**old["metadata"], "period_correction": {
            "repair": REPAIR, "plan_id": self.plan.plan_id,
            "reason": "explicit_table_cutoff_previously_assigned_to_wrong_week",
            "source_caption": correction["source_caption"],
            "original_url": correction["original_url"], "original_sha256": correction["original_sha256"],
            "source_page": correction["source_page"],
            "superseded_by": dict(zip(KEYS, identity(target))),
            "baseline_sha256": self.plan.hashes["baseline"],
            "corrections_sha256": self.plan.hashes["corrections"],
        }}
        return result

    def verify_final(self, actual, incoming, rejected):
        expected = {identity(r): r for r in [*incoming, *rejected]}
        got = {identity(r): r for r in actual}
        require(len(expected) == len(actual) == len(got) == 1281 and expected.keys() == got.keys(),
                "Final source identities differ from 1272 active plus nine rejected")
        for key, wanted in expected.items():
            require(all(got[key].get(k) == wanted.get(k) for k in STATE), f"Stored observation differs: {key}")
        require(sum(r["quality_status"] == "rejected" for r in actual) == 9, "Rejected count differs")

    async def commit(self, db, *, updater, fetched, task_uuid, **options):
        require(updater.country_code == "MX" and updater.ontology_source_id == SOURCE, "Repair is MX weekly only")
        self.plan.verify_files()
        compare_candidate(self.plan.rows, fetched.rows)
        require(fetched.source_audit == self.plan.audit, "Replayed source audit differs from reviewed audit")
        await acquire_country_data_mutation_lock(db, "MX")
        if self.lease:
            await self.lease.owned_task(db)
        built = SeriesObservationStore().build_observations(fetched.rows, "MX", source_id=SOURCE)
        require(not (built.skipped_invalid or built.skipped_unmatched or built.skipped_ambiguous)
                and len(built.observations) == 1272, "Registry preflight must resolve every candidate uniquely")
        incoming = built.observations
        targets = {raw_identity(r["raw_data"]): r for r in incoming}
        rejected = [self.rejection(old, correction, targets[raw_identity(correction["after"])])
                    for old, correction in zip(self.plan.old, self.plan.corrections)]
        current = await weekly_rows(db, lock=True)
        if len(current) == 1281:
            self.verify_final(current, incoming, rejected)
            self.receipt = {"mode": "already_applied", "plan_id": self.plan.plan_id,
                            "active": 1272, "rejected": 9, "observations_changed": 0}
            return MexicoDGEImportResult(0, 0, None, fetched.source_latest_date, False)
        require({identity(r): normalize_snapshot(r) for r in current} ==
                {identity(r): normalize_snapshot(r) for r in self.plan.baseline},
                "Weekly database changed since frozen baseline; refusing concurrent/stale repair")
        protected_before = await protected_state(db)
        now = datetime.now(timezone.utc)
        current_by_key = {identity(r): r for r in current}
        current_old = [current_by_key[identity(r)] for r in self.plan.old]
        # Preserve an initial state if a legacy row predates the revision ledger.
        await _persist_observation_revisions(db, current_old, recorded_at=now, seed_current_state=True)
        original_revisions = [dict(r) for r in (await db.execute(select(
            DiseaseSeriesObservationRevision.__table__).where(
                DiseaseSeriesObservationRevision.series_code.in_({r["series_code"] for r in self.plan.old})
            ))).mappings()]
        table = DiseaseSeriesObservation.__table__
        for old, replacement in zip(self.plan.old, rejected):
            result = await db.execute(update(table).where(table.c.id == old["id"]).values(
                quality_status="rejected", metadata=replacement["metadata"],
                updated_at=now.replace(tzinfo=None)))
            require(result.rowcount == 1, "Old observation was not updated exactly once")
        added_revisions = await _persist_observation_revisions(
            db, current_old, recorded_at=now, seed_current_state=False)
        require(added_revisions == 9, "Exactly nine rejected revisions must be appended")
        imported = await super().commit(db, updater=updater, fetched=fetched, task_uuid=task_uuid, **options)
        require(not imported.skipped_unmapped, "Normal importer skipped candidate facts")
        await mark_dirty(db, line="surveillance", scope_key="MX", task_uuid=task_uuid,
                         change_set={"version": 1, "precise": True,
                                     "changes": list(observation_publication_changes(current_old))})
        await db.flush()
        final = await weekly_rows(db)
        self.verify_final(final, incoming, rejected)
        protected_after = await protected_state(db)
        require(protected_before == protected_after, "Unrelated MX observations changed")
        for revision in original_revisions:
            saved = (await db.execute(select(DiseaseSeriesObservationRevision.__table__).where(
                DiseaseSeriesObservationRevision.id == revision["id"]))).mappings().one()
            require(dict(saved) == revision, "An original revision was modified")
        self.plan.verify_files()
        if self.lease:
            await self.lease.owned_task(db)
        self.receipt = {"mode": "stored", "plan_id": self.plan.plan_id, "input_sha256": self.plan.hashes,
                        "before": 761, "after": len(final), "active": 1272, "rejected": 9,
                        "rejected_revisions_appended": added_revisions, "original_revisions_preserved": len(original_revisions),
                        "old_period_dirty": "2026-10-18", "protected_before": protected_before,
                        "protected_after": protected_after}
        return imported


async def run(args):
    plan = RepairPlan(args.candidate, args.backup, args.manifest)
    updater = MexicoDGEHighFrequencyUpdater(source=SCOPE)
    replay = HighFrequencyReplay(mx_high_frequency, plan.manifest)
    options = {"country_code": "MX", "start_year": 2026, "end_year": 2026}
    if not args.apply:
        fetched = await asyncio.to_thread(replay.organize, replay.originals, updater=updater, source=SCOPE, **options)
        strategy = MexicoWeeklyPeriodRepair(plan)
        async with get_session_maker()() as db:
            try:
                await db.execute(text("SET LOCAL lock_timeout='15s'"))
                await db.execute(text("SET LOCAL statement_timeout='90s'"))
                await strategy.commit(db, updater=updater, fetched=fetched, service=CrawlService(), force=True, task_uuid=str(uuid4()))
                receipt = dict(strategy.receipt)
                await strategy.commit(db, updater=updater, fetched=fetched, service=CrawlService(), force=True, task_uuid=str(uuid4()))
                require(strategy.receipt["mode"] == "already_applied", "Repeat execution was not idempotent")
                receipt.update(mode="rollback_rehearsal", repeated_execution="no_changes")
            finally:
                await db.rollback()
    else:
        task = await create_source_import_task("MX", SCOPE)
        lease = None
        try:
            async with foreground_task_lease(task.task_uuid) as lease:
                strategy = MexicoWeeklyPeriodRepair(plan, lease)
                result = await run_surveillance(CrawlService(), task=task, updater=updater, connector=replay,
                    source=SCOPE, force=True, process=True, strategy=strategy, connector_options=options)
                receipt = {**strategy.receipt, "task_uuid": task.task_uuid, "ingestion": asdict(result)}
                async with get_db() as db:
                    owned = await lease.owned_task(db)
                    owned.status = TaskStatus.COMPLETED
                    owned.progress = 100
                    owned.completed_at = datetime.now(timezone.utc)
                    owned.output_data = receipt
        except BaseException as error:
            await fail_foreground_import(task.task_uuid, lease, error)
            raise
    receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
    output = args.receipt or Path(args.candidate) / ("period-repair-apply-receipt.json" if args.apply else "period-repair-dry-run.json")
    Path(output).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit through staged ingestion; default rolls back")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    async def execute():
        try:
            await run(args)
        finally:
            await dispose_database()
    asyncio.run(execute())


if __name__ == "__main__":
    main()
