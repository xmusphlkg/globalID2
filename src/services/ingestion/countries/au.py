"""NINDSS transport, offline DSR parsing, and source recovery selection."""
from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date
from pathlib import Path

from src.data.crawlers.au import AustraliaNINDSSCrawler, _parse_dm0_to_state_counts, _quarter_for_month, _month_name, _SKIP_STATES, normalize_au_state_code, _parse_int
from src.data.processors.au import AUUpdateFetchResult, _norm_text, _ARCHIVE_SKIP_STATES
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source, months=None, country_code="AU", output_csv=None, include_current_month=False, refresh_recent_months=3):
    from src.data.processors.au import AUMonthlyUpdater
    updater = AUMonthlyUpdater(country_code=country_code, output_csv=output_csv, include_current_month=include_current_month, refresh_recent_months=refresh_recent_months)
    requested = updater._resolve_requested_months(months)
    if not requested:
        raise ValueError("AU crawl has no eligible closed months")
    if updater.output_csv.exists():
        await writer.write(await asyncio.to_thread(updater.output_csv.read_bytes), source_url=updater.output_csv.resolve().as_uri(), kind="prior_csv", metadata={"source_file": updater.output_csv.name, "months": requested})
    archive_root = updater._default_raw_archive_root(country_code)
    for year, month in requested:
        for path in sorted((archive_root / str(year) / f"{month:02d}").glob("*.json")):
            await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="recovery_json", metadata={"year": year, "month": month, "source_file": path.name})
    crawler = AustraliaNINDSSCrawler(save_raw=False)
    try:
        from src.services.ingestion.capture import capture_writer
        with capture_writer(writer):
            configured = await asyncio.to_thread(crawler._load_config)
        if not configured:
            if not writer.artifacts:
                raise RuntimeError("AU NINDSS: failed to capture Power BI auth token")
            return writer.artifacts
        from src.services.ingestion.capture import capture_writer
        with capture_writer(writer):
            diseases = await asyncio.to_thread(crawler.get_all_diseases)
        if not diseases:
            if not writer.artifacts:
                raise RuntimeError("AU NINDSS: could not retrieve disease list from Power BI")
            return writer.artifacts
        semaphore = asyncio.Semaphore(6)
        async def fetch(year, month, disease):
            async with semaphore:
                payload = crawler._build_location_payload(str(year), _quarter_for_month(month), _month_name(month), disease)
                raw = await asyncio.to_thread(crawler.request_payload_bytes, payload)
                if raw is not None:
                    # Serial registration avoids filename races without retaining
                    # every response until the complete month batch finishes.
                    async with write_lock:
                        await writer.write(raw, source_url=crawler._config["apiUrl"], kind="powerbi_json", metadata={"year": year, "month": month, "disease": disease, "months": requested})
        write_lock = asyncio.Lock()
        await asyncio.gather(*(fetch(year, month, disease) for year, month in requested for disease in diseases))
    finally:
        crawler.session.close()
        if crawler._http:
            crawler._http.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, country_code="AU", output_csv=None, include_current_month=False, refresh_recent_months=3):
    requested = updater._resolve_requested_months(months)
    live = {}
    archive_rows = []
    archive_months = set()
    prior_rows = []
    for artifact in artifacts:
        raw = read_artifact(artifact)
        meta = artifact.metadata_
        if artifact.kind == "prior_csv":
            prior_rows = updater.parse_source_csv(raw, source_file=meta["source_file"])
        elif artifact.kind == "powerbi_json":
            dm0, payload = AustraliaNINDSSCrawler.parse_payload_bytes(raw)
            if dm0 is None:
                continue
            counts = _parse_dm0_to_state_counts(dm0, payload, str(meta["year"]))
            for state, count in counts.items():
                if _norm_text(state).upper() in _SKIP_STATES:
                    continue
                jurisdiction = normalize_au_state_code(_norm_text(state))
                value = _parse_int(count)
                if jurisdiction is None:
                    if value:
                        raise ValueError(f"AU NINDSS returned an unknown non-empty state/territory label: {state!r}")
                    continue
                if value is not None:
                    live[(meta["year"], meta["month"], meta["disease"], jurisdiction)] = value
        elif artifact.kind == "recovery_json":
            payload = json.loads(raw)
            disease = _norm_text(payload.get("disease") or payload.get("Disease"))
            counts = payload.get("parsed_counts")
            if not disease or not isinstance(counts, dict):
                continue
            year, month = meta["year"], meta["month"]
            archive_months.add((year, month))
            if updater.is_subdivision:
                value = next((value for state, value in counts.items() if normalize_au_state_code(state) == updater.country_code), None)
                value = _parse_int(value)
                if value is None:
                    continue
            else:
                value = sum(_parse_int(value) or 0 for state, value in counts.items() if _norm_text(state).upper() not in _ARCHIVE_SKIP_STATES)
            archive_rows.append(updater._normalized_output_row(year=year, month=month, disease=disease, cases=max(0, value), source_file=meta["source_file"]))
    totals = {}
    for (year, month, disease, jurisdiction), value in live.items():
        if updater.is_subdivision and jurisdiction != updater.country_code:
            continue
        key = (year, month, disease)
        totals[key] = totals.get(key, 0) + value
    # Feed the same CSV parser the exact rows the old crawler emitted.
    output = io.StringIO(newline="")
    fields = ["Disease", "DiseaseFull", "Group", "Year", "Month", "Date", "Cases", "JurisdictionCode", "ParentCountryCode", "LocationType", "ReportingArea", "Geocode", "GeographyKey"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for (year, month, disease), cases in sorted(totals.items()):
        row = updater._normalized_output_row(year=year, month=month, disease=disease, cases=cases, source_file=updater.output_csv.name)
        writer.writerow({key: {"Disease": disease, "DiseaseFull": disease, "Year": year, "Month": month}.get(key, row.get(key, "")) for key in fields})
    live_rows = updater.parse_source_csv(output.getvalue().encode(), source_file=updater.output_csv.name)
    archive_rows = archive_rows if set(requested).issubset(archive_months) else []
    prior_rows = updater._filter_rows_for_months(prior_rows, requested) if updater._rows_cover_months(prior_rows, requested) else []
    label, rows = updater._select_candidate_rows(live_rows, archive_rows, prior_rows)
    if not rows:
        raise RuntimeError("AU crawler produced no usable rows")
    return AUUpdateFetchResult(rows=rows, source_latest_date=updater._latest_row_date(rows), source_csv=updater.output_csv, script_logs=[f"[organize] selected {label}"])
