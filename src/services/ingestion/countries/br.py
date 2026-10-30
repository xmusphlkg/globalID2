"""SINAN connector downloads DBC bytes before any decompression or aggregation."""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.data.crawlers.br import BrazilSINANCrawler, SINANFile, _deduplicate_sinan_file_aliases
from src.data.processors.br import BRMonthlyUpdater, BRUpdateFetchResult
from src.data.processors.br_subdivisions import (
    EXCLUDED_STATE_PREFIXES, STATE_CODES, aggregate_residence_file, coalesce_source_rows,
)
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.snapshot_merge import merge_snapshot_rows


async def collect(writer, *, source, months, history_csv, start_year=None, country_code="BR"):
    subdivision = country_code != "BR"
    if subdivision and country_code not in {f"BR-{code}" for code in STATE_CODES.values()}:
        raise ValueError(f"Unsupported Brazil residence state: {country_code}")
    crawler = BrazilSINANCrawler(save_raw=False)
    target_years = {year for year, _ in months}
    if start_year:
        target_years = {year for year in target_years if year >= start_year}
    try:
        prior = Path(history_csv)
        if not subdivision and prior.exists():
            await writer.write(await asyncio.to_thread(prior.read_bytes), source_url=prior.as_posix(), kind="history_csv")
        from src.services.ingestion.capture import capture_writer
        with capture_writer(writer):
            try:
                index = await asyncio.to_thread(crawler.fetch_file_index)
            except Exception:
                if not any(a.kind == "history_csv" for a in writer.artifacts):
                    raise
                index = []
        prefixes = BRMonthlyUpdater._prefixes_from_source(source)
        requested = set(prefixes) if prefixes is not None else {item.prefix for item in index}
        if "LER" in requested:
            requested.add("LERD")
        selected = [item for item in _deduplicate_sinan_file_aliases(index) if item.year in target_years and item.prefix in requested]
        if subdivision:
            selected = [item for item in selected if item.prefix not in EXCLUDED_STATE_PREFIXES]
        with tempfile.TemporaryDirectory(prefix="globalid-sinan-collect-") as temporary:
            for item in selected:
                try:
                    path = await asyncio.to_thread(crawler._download_file, item, Path(temporary))
                except Exception:
                    if subdivision:
                        raise
                    continue
                metadata = asdict(item)
                metadata["modified_at"] = item.modified_at.isoformat() if item.modified_at else None
                await writer.write(await asyncio.to_thread(path.read_bytes), source_url=item.url, kind="sinan_dbc", metadata={"file": metadata})
        if not any(a.kind in {"sinan_dbc", "history_csv"} for a in writer.artifacts):
            raise RuntimeError("[BR-SINAN] No SINAN files matched requested months/prefixes")
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source, months, history_csv, start_year=None, country_code="BR"):
    if updater.country_code != "BR":
        return _organize_subdivision(
            artifacts, updater=updater, months=months, history_csv=history_csv,
            country_code=updater.country_code,
        )
    crawler = BrazilSINANCrawler(save_raw=False)
    files, paths, prior = [], {}, []
    logs = []
    try:
        with tempfile.TemporaryDirectory(prefix="globalid-sinan-organize-") as temporary:
            root = Path(temporary)
            for index, artifact in enumerate(artifacts):
                if artifact.kind == "history_csv":
                    path = root / "history.csv"
                    path.write_bytes(read_artifact(artifact))
                    try:
                        prior = updater._load_rows(path)
                    except Exception as exc:
                        logs.append(f"[cache] unable to read existing CSV: {type(exc).__name__}: {exc}")
                elif artifact.kind == "sinan_dbc":
                    metadata = dict(artifact.metadata_["file"])
                    metadata["modified_at"] = datetime.fromisoformat(metadata["modified_at"]) if metadata.get("modified_at") else None
                    item = SINANFile(**metadata)
                    path = root / f"{index}-{Path(item.filename).name}"
                    path.write_bytes(read_artifact(artifact))
                    files.append(item)
                    paths[item.url] = path
            live = []
            live_error = None
            if files:
                try:
                    summary = crawler.crawl_monthly_national(root / "output.csv", months=months, prefixes=updater._prefixes_from_source(source), write_csv=False, file_index=files, collected_files=paths)
                    live = updater._filter_rows_for_months(summary.rows, months)
                except Exception as exc:
                    live_error = exc
                    logs.append(f"[crawler] live parsing failed: {type(exc).__name__}: {exc}")
            prior, _, _ = updater._prior_rows_candidate(prior, months)
            rows, source_audit = merge_snapshot_rows(live, prior)
            if not rows:
                if live_error is not None:
                    raise live_error
                raise RuntimeError("BR crawler produced no usable rows")
            if rows is prior:
                logs.append(f"[recovery] using previous CSV snapshot with {len(rows)} rows")
            return BRUpdateFetchResult(rows, updater._latest_row_date(rows), Path(history_csv), logs, [source_audit])
    finally:
        crawler.session.close()


def _organize_subdivision(artifacts, *, updater, months, history_csv, country_code):
    """Read only captured bytes; never reuse a national snapshot for a state."""
    if country_code not in {f"BR-{code}" for code in STATE_CODES.values()}:
        raise ValueError(f"Unsupported Brazil residence state: {country_code}")
    rows, logs, audit = [], [], []
    with tempfile.TemporaryDirectory(prefix="globalid-sinan-state-organize-") as temporary:
        root = Path(temporary)
        for index, artifact in enumerate(artifacts):
            if artifact.kind != "sinan_dbc":
                continue
            metadata = dict(artifact.metadata_["file"])
            metadata["modified_at"] = datetime.fromisoformat(metadata["modified_at"]) if metadata.get("modified_at") else None
            item = SINANFile(**metadata)
            if item.prefix in EXCLUDED_STATE_PREFIXES:
                logs.append(f"[excluded] {item.filename}: not an individual-case residence dataset")
                audit.append({"file": item.filename, "excluded": "not an individual-case residence dataset"})
                continue
            dbc = root / f"{index}.dbc"
            dbf = root / f"{index}.dbf"
            dbc.write_bytes(read_artifact(artifact))
            BrazilSINANCrawler._decompress_to_dbf(dbc, dbf)
            try:
                result = aggregate_residence_file(item, dbf, months=set(months))
            finally:
                dbf.unlink(missing_ok=True)
            rows.extend(row for row in result["rows"] if row["CountryCode"] == country_code)
            audit.append({"file": item.filename, **{key: value for key, value in result.items() if key != "rows"}})
            logs.append(
                f"[residence] {item.filename}: assigned={result['assigned_count']} "
                f"unknown={result['unknown_residence_count']} undated={result['undated_count']}"
            )
    rows = coalesce_source_rows(rows)
    if not rows:
        raise RuntimeError(f"BR crawler produced no usable residence rows for {country_code}")
    return BRUpdateFetchResult(rows, updater._latest_row_date(rows), Path(history_csv), logs, audit)
