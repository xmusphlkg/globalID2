#!/usr/bin/env python3
"""Quarantine five W17 facts derived from an unverified death-row schema.

Raw PDFs, immutable revisions and original task counts remain audit evidence.
Only the exact URL/SHA/label from the real regression fixture is eligible.
"""
from __future__ import annotations
import asyncio
import csv
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
from sqlalchemy import select, delete, func

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.core.database import get_db, dispose_database
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.domain import DiseaseSeriesObservation as Observation, DiseaseSurveillanceSeries as Series, SourceDiseaseCategory as Category
from scripts.imports.pe_cdc_history import atomic_json


async def main():
    evidence = json.loads((ROOT / "tests/fixtures/ingestion/PE/2026w17_provenance.json").read_text())
    output = ROOT / "data/current/pe"
    receipt_path = output / "w17_death_contract_correction_receipt.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {
        "correction": "pe_2026w17_unverified_neonatal_classification_columns",
        "source_url": evidence["source_url"], "source_sha256": evidence["source_sha256"],
        "reason": "Source death row uses unverified confirmed/probable columns; whole row quarantined without inferred values",
        "raw_artifacts_preserved": True, "immutable_revisions_preserved": True,
        "original_task_counts_preserved": True, "removed_current_observations": []}
    def affected(raw):
        return raw.get("SourceURL") == evidence["source_url"] and raw.get("RawSHA256") == evidence["source_sha256"] and raw.get("RawDiseaseLabel") == "Muerte perinatal - neonatal"
    try:
        async with get_db() as db:
            await acquire_country_data_mutation_lock(db, "PE")
            rows = (await db.execute(select(Observation).join(Series, Observation.series_code == Series.series_code)
                .where(Series.country_code == "PE", Series.source_system == "SRC_PE_CDC"))).scalars().all()
            targets = [r for r in rows if affected(r.raw_data or {})]
            if targets and (len(targets) != 5 or sorted(r.value for r in targets) != [0, 21, 21, 701, 701]):
                raise ValueError("W17 correction precondition changed; manual evidence review required")
            if targets:
                receipt["removed_current_observations"] = [{"id": r.id, "series_code": r.series_code,
                    "time": r.time.isoformat(), "dimension_key": r.dimension_key, "value": r.value,
                    "dimensions": r.dimensions, "raw_data": r.raw_data, "metadata": r.metadata_} for r in targets]
                receipt["status"] = "planned"; atomic_json(receipt_path, receipt)
                await db.execute(delete(Observation).where(Observation.id.in_([r.id for r in targets])))
                # Four source categories/holding series represented the invalid
                # case interpretation. Deactivate only if no current facts use
                # them; the shared cumulative-death category stays active.
                for code in {r.series_code for r in targets}:
                    count = (await db.execute(select(func.count()).select_from(Observation).where(Observation.series_code == code))).scalar()
                    if count:
                        continue
                    series = (await db.execute(select(Series).where(Series.series_code == code))).scalar_one()
                    series.is_active = False
                    series.metadata_ = {**(series.metadata_ or {}), "parser_correction": receipt["correction"]}
                    category_id = (series.metadata_ or {}).get("source_category_id")
                    if category_id:
                        category = (await db.execute(select(Category).where(Category.id == category_id))).scalar_one()
                        category.is_active = False
                        category.metadata_ = {**(category.metadata_ or {}), "parser_correction": receipt["correction"]}
        # The stopped controller cannot race the CSV/state repair. Restarts
        # read these corrected rows; immutable original task output is unchanged.
        csv_path = output / "peru_cdc_weekly.csv"
        with csv_path.open() as stream:
            reader = csv.DictReader(stream); fields = reader.fieldnames; csv_rows = list(reader)
        kept = [r for r in csv_rows if not affected(r)]
        if len(csv_rows) != len(kept):
            temp = csv_path.with_suffix(".csv.tmp")
            with temp.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(kept)
            temp.replace(csv_path)
        state_path = output / "history-state.json"; state = json.loads(state_path.read_text())
        for key, batch in state["batches"].items():
            if evidence["source_url"] not in batch["urls"]:
                continue
            if batch.get("correction") != receipt["correction"]:
                batch["original_import_rows"] = batch["rows"]
                batch["rows"] -= 5; batch["correction"] = receipt["correction"]
            audit_path = (ROOT / key).parent / "source_audit.json"
            if audit_path.exists():
                audit = json.loads(audit_path.read_text())
                audit["original_import_rows"] = batch["original_import_rows"]
                audit["rows"] = batch["rows"]; audit["observed_values"] = batch["rows"]
                audit["correction_receipt"] = str(receipt_path.relative_to(ROOT)); atomic_json(audit_path, audit)
        state["current_valid_native_metrics"] = sum(b.get("rows", 0) for b in state["batches"].values())
        atomic_json(state_path, state)
        audit_path = output / "source_audit.json"; audit = json.loads(audit_path.read_text())
        audit["organized_native_metrics"] = state["current_valid_native_metrics"]
        audit["batches"] = state["batches"]; audit["correction_receipt"] = str(receipt_path.relative_to(ROOT)); atomic_json(audit_path, audit)
        receipt.update({"status": "applied", "current_csv_rows": len(kept),
            "current_valid_native_metrics": state["current_valid_native_metrics"],
            "completed_at": datetime.now(timezone.utc).isoformat()}); atomic_json(receipt_path, receipt)
        print(json.dumps({"removed_current_facts": len(targets), "csv_rows": len(kept), "valid_metrics": state["current_valid_native_metrics"]}))
    finally:
        await dispose_database()


if __name__ == "__main__":
    asyncio.run(main())
