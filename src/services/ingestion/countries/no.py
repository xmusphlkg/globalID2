"""FHI MSIS raw transport and offline closed-month source organization."""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
from datetime import date
from pathlib import Path

from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer
from src.services.ingestion.replay import offline_crawler_type


async def collect(writer, *, source, months=None, force=False, updater, **_):
    if str(source).strip().casefold() not in {"all", "fhi_msis", "msis"}:
        raise ValueError(f"Unsupported NO source: {source!r}")
    as_of = updater.today_provider()
    requested = updater._resolve_requested_months(months, force=force, as_of=as_of)
    if not requested:
        raise ValueError("NO refresh contains no eligible closed/current months")
    await writer.write(
        json.dumps({"as_of": as_of.isoformat(), "months": requested}).encode(),
        source_url=None,
        kind="collection_plan",
    )
    had_cache = updater.output_csv.exists()
    if had_cache:
        await writer.write(
            await asyncio.to_thread(updater.output_csv.read_bytes),
            source_url=updater.output_csv.resolve().as_uri(),
            kind="prior_csv",
        )

    def download():
        crawler = updater.crawler_type(save_raw=False)
        try:
            if hasattr(crawler, "collect_monthly_responses"):
                crawler.collect_monthly_responses(requested)
                return
            # Diagnosis names/codes are request metadata, not observations.
            diagnoses = crawler.fetch_diagnoses()
            for year in sorted({year for year, _ in requested}):
                crawler.get(
                    crawler.monthly_url,
                    params=crawler._monthly_params(year, [d.code for d in diagnoses]),
                )
        finally:
            crawler.session.close()

    with capture_writer(writer):
        try:
            await asyncio.to_thread(download)
        except Exception as exc:
            # Preserve the existing validated-cache recovery policy. No source
            # business rows have been parsed at this point.
            if not had_cache:
                raise
            from src.services._lifecycle import safe_exception_summary
            await writer.write(
                json.dumps({"error": safe_exception_summary(exc)}).encode(),
                source_url=None, kind="collection_failure",
            )
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, force=False, **_):
    plan = next(a for a in artifacts if a.kind == "collection_plan")
    config = json.loads(read_artifact(plan))
    clone = copy.copy(updater)
    clone.today_provider = lambda: date.fromisoformat(config["as_of"])
    clone.crawler_type = offline_crawler_type(updater.crawler_type, artifacts)
    original_csv = updater.output_csv
    with tempfile.TemporaryDirectory(prefix="globalid-no-organize-") as directory:
        clone.output_csv = Path(directory) / original_csv.name
        previous = next((a for a in artifacts if a.kind == "prior_csv"), None)
        if previous:
            clone.output_csv.write_bytes(read_artifact(previous))
        try:
            result = clone.refresh_source(
                source=source,
                force=force,
                months=[tuple(m) for m in config["months"]],
                save_raw=False,
            )
        except RuntimeError as exc:
            failure = next((a for a in artifacts if a.kind == "collection_failure"), None)
            if failure is not None and "No captured source artifact" in str(exc):
                detail = json.loads(read_artifact(failure))["error"]
                raise RuntimeError(f"NO source collection failed: {detail}") from exc
            raise
    result.source_csv = original_csv
    if result.source_audit.get("contract", "").startswith("fhi-msis-statistikk-"):
        from src.data.storage.series_observation_store import SeriesObservationStore
        checked = SeriesObservationStore().build_observations(
            result.rows, "NO", source_id=updater.ontology_source_id,
        )
        result.source_audit["registry_review"] = {
            "matched_observations": len(checked.observations),
            "unmatched_rows": checked.skipped_unmatched,
            "ambiguous_rows": checked.skipped_ambiguous,
            "invalid_rows": checked.skipped_invalid,
            "unmatched_labels": sorted({str(row.get("RawDiseaseLabel") or "")
                                        for row in checked.unhandled_rows or []}),
            "policy": "Unmapped native categories require review; do not project split categories into historical aggregates",
        }
        if checked.skipped_unmatched or checked.skipped_ambiguous or checked.skipped_invalid:
            result.source_audit["status"] = "needs_review"
    return result
