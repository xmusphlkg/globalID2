"""KDCA collection retains raw envelopes and files; organization is offline."""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import date
from pathlib import Path

from src.data.crawlers.kr import (
    KoreaKDCAOpenAPICrawler, _extract_items, aggregate_period_region_rows,
    normalize_kdca_download_rows, validate_kdca_national_rows,
    DEFAULT_PORTAL_SOURCE_NAME,
    normalize_kdca_death_rows, validate_kdca_death_rows,
    DEFAULT_DEATH_SOURCE_NAME,
    aggregate_region_annual_rows,
)
from src.data.processors.kr import KRUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.snapshot_merge import merge_snapshot_rows
from src.services.ingestion.capture import capture_writer


async def _update_artifact_metadata(artifact, **context):
    from sqlalchemy import update

    from src.core.database import get_db
    from src.domain.collection import SourceArtifact

    metadata = {**artifact.metadata_, **context}
    async with get_db() as db:
        await db.execute(update(SourceArtifact).where(SourceArtifact.id == artifact.id).values(metadata_=metadata))
    artifact.metadata_ = metadata


async def _reject_partial_live_fetch(writer, start):
    # Persist the decision as well as the bytes, so loading all artifacts from
    # the database for offline replay cannot revive an incomplete live batch.
    for artifact in writer.artifacts[start:]:
        await _update_artifact_metadata(artifact, live_fetch_failed=True)
    return writer.artifacts


async def collect(writer, *, source, months=None, history_csv=None, start_year=None, end_year=None, source_file=None, source_dir=None):
    if source == "kdca_death_annual":
        return await collect_death(
            writer, source=source,
            start_year=start_year or 2001,
            end_year=end_year or date.today().year,
        )
    if source == "kdca_region_annual":
        return await collect_region(
            writer, source=source,
            start_year=start_year or 2001,
            end_year=end_year or date.today().year,
        )
    if months is None or history_csv is None:
        raise ValueError("KDCA monthly collection requires months and history_csv")
    crawler = KoreaKDCAOpenAPICrawler(save_raw=False)
    target = set(map(tuple, months))
    try:
        cache = Path(history_csv)
        if cache.exists():
            await writer.write(await asyncio.to_thread(cache.read_bytes), source_url=cache.as_posix(), kind="history_csv")
        paths = crawler._manual_source_paths(source_file=Path(source_file) if source_file else None, source_dir=Path(source_dir) if source_dir else None)
        if paths and (source_file or source_dir or not crawler.service_key):
            for path in paths:
                await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.as_posix(), kind="download_file", metadata={"filename": path.name})
            return writer.artifacts
        live_start = len(writer.artifacts)
        with capture_writer(writer) as capture:
            if crawler.service_key:
                try:
                    await asyncio.to_thread(crawler.fetch_period_region_monthly, start_year=min(year for year, _ in target), end_year=max(year for year, _ in target))
                except Exception:
                    capture.verify()
                    if not any(a.kind == "history_csv" for a in writer.artifacts):
                        raise
                    # Retain partial responses for inspection, but an unfinished
                    # paginated fetch is not a live candidate in the old policy.
                    return await _reject_partial_live_fetch(writer, live_start)
            else:
                # This helper extracts only the transport envelope; disease
                # normalization and national aggregation happen in organize.
                for year in sorted({year for year, _ in target}):
                    before = len(writer.artifacts)
                    fetch_error = None
                    try:
                        await asyncio.to_thread(crawler._fetch_bass_stats_list, year)
                    except Exception as exc:
                        capture.verify()
                        fetch_error = exc
                    for artifact in writer.artifacts[before:]:
                        # Source year is transport context, not a parsed fact.
                        await _update_artifact_metadata(artifact, portal_year=year)
                    if fetch_error is not None:
                        if not any(a.kind == "history_csv" for a in writer.artifacts):
                            raise fetch_error
                        return await _reject_partial_live_fetch(writer, live_start)
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source, months=None, history_csv=None, start_year=None, end_year=None, source_file=None, source_dir=None):
    if source == "kdca_death_annual":
        return organize_death(
            artifacts, updater=updater, source=source,
            start_year=start_year, end_year=end_year,
        )
    if source == "kdca_region_annual":
        return organize_region(
            artifacts, updater=updater, source=source,
            start_year=start_year, end_year=end_year,
        )
    if months is None or history_csv is None:
        raise ValueError("KDCA monthly organization requires months and history_csv")
    crawler = KoreaKDCAOpenAPICrawler(save_raw=False)
    target = set(map(tuple, months))
    raw_api, live, prior, downloads = [], [], [], []
    logs = []
    try:
        with tempfile.TemporaryDirectory(prefix="globalid-kdca-") as folder:
            root = Path(folder)
            envelopes = []
            for index, artifact in enumerate(artifacts):
                payload = read_artifact(artifact)
                if artifact.kind == "history_csv":
                    path = root / "history.csv"
                    path.write_bytes(payload)
                    try:
                        prior = updater._load_rows(path)
                    except Exception as exc:
                        logs.append(f"[cache] unable to read existing CSV: {type(exc).__name__}: {exc}")
                elif artifact.kind == "download_file":
                    path = root / f"{index}-{Path(artifact.metadata_['filename']).name}"
                    path.write_bytes(payload)
                    downloads.append(path)
                else:
                    if not artifact.metadata_.get("live_fetch_failed"):
                        envelopes.append((artifact, payload))
            live_error = None
            try:
                for artifact, payload in envelopes:
                    if "selectBassDissStatsList" in (artifact.source_url or ""):
                        envelope = json.loads(payload)
                        year = artifact.metadata_.get("portal_year") or min(y for y, _ in target)
                        live.extend(normalize_kdca_download_rows(crawler._extract_json_rows(envelope), fallback_year=year, source_name=DEFAULT_PORTAL_SOURCE_NAME, source_url=crawler.portal_stats_url))
                    elif "PeriodRegion" in (artifact.source_url or ""):
                        raw_api.extend(_extract_items(json.loads(payload)))
                if downloads:
                    live = crawler.load_download_rows(downloads, months=target)
                elif raw_api:
                    live = aggregate_period_region_rows(raw_api, months=target, source_url=crawler.doc_url)
                live = updater._filter_rows_for_months(live, sorted(target))
                if live:
                    validate_kdca_national_rows(live, target_months=target)
                    path = root / "normalized.csv"
                    crawler._write_national_csv(path, live)
                    live = updater._load_rows(path)
            except Exception as exc:
                live = []
                live_error = exc
                logs.append(f"[crawler] live parsing failed: {type(exc).__name__}: {exc}")
            prior = updater._filter_rows_for_months(prior, sorted(target)) if updater._rows_cover_months(prior, sorted(target)) else []
            if prior:
                try:
                    validate_kdca_national_rows(prior, target_months=target)
                except ValueError as exc:
                    logs.append(f"[cache] rejected previous CSV snapshot: {exc}")
                    prior = []
            rows, source_audit = merge_snapshot_rows(live, prior)
            if not rows:
                if live_error is not None:
                    raise live_error
                raise RuntimeError("KR crawler produced no usable rows")
            if rows is prior:
                logs.append(f"[recovery] using previous CSV snapshot with {len(rows)} rows")
            return KRUpdateFetchResult(rows, updater._latest_row_date(rows), Path(history_csv), logs, [source_audit])
    finally:
        crawler.session.close()


async def collect_death(writer, *, source="kdca_death_annual", start_year, end_year):
    """Capture the official annual death API response for offline replay."""
    crawler = KoreaKDCAOpenAPICrawler(save_raw=False)
    try:
        if not crawler.service_key:
            raise RuntimeError(
                "DATA_GO_KR_SERVICE_KEY is required for KDCA annual death capture"
            )
        with capture_writer(writer) as capture:
            await asyncio.to_thread(
                crawler.fetch_death_annual,
                start_year=int(start_year),
                end_year=int(end_year),
            )
        return writer.artifacts
    finally:
        crawler.session.close()


def organize_death(artifacts, *, updater, source="kdca_death_annual", start_year=None, end_year=None):
    """Normalize captured ``death01`` envelopes without network access."""
    crawler = KoreaKDCAOpenAPICrawler(save_raw=False)
    rows = []
    try:
        for artifact in artifacts:
            payload = read_artifact(artifact)
            envelope = json.loads(payload)
            rows.extend(normalize_kdca_death_rows(
                _extract_items(envelope),
                source_url=artifact.source_url or crawler.doc_url,
                source_name=DEFAULT_DEATH_SOURCE_NAME,
            ))
        if start_year is not None or end_year is not None:
            lower = int(start_year) if start_year is not None else 1900
            upper = int(end_year) if end_year is not None else 9999
            rows = [row for row in rows if lower <= int(row["Year"]) <= upper]
        validate_kdca_death_rows(rows)
        latest = max((row["Date"] for row in rows), default=None)
        latest_date = date.fromisoformat(latest) if latest else None
        return KRUpdateFetchResult(rows, latest_date, getattr(updater, "output_csv", Path("korea_deaths_annual.csv")), [], [])
    finally:
        crawler.session.close()


async def collect_region(writer, *, source="kdca_region_annual", start_year, end_year):
    """Capture the official annual Region01 response for offline replay."""
    crawler = KoreaKDCAOpenAPICrawler(save_raw=False)
    try:
        if not crawler.service_key:
            raise RuntimeError(
                "DATA_GO_KR_SERVICE_KEY is required for KDCA annual regional capture"
            )
        with capture_writer(writer):
            await asyncio.to_thread(
                crawler.fetch_region_annual,
                start_year=int(start_year),
                end_year=int(end_year),
            )
        return writer.artifacts
    finally:
        crawler.session.close()


def organize_region(artifacts, *, updater, source="kdca_region_annual", start_year=None, end_year=None):
    """Normalize captured Region01 envelopes without network access."""
    crawler = KoreaKDCAOpenAPICrawler(save_raw=False)
    try:
        raw_rows = []
        for artifact in artifacts:
            raw_rows.extend(_extract_items(json.loads(read_artifact(artifact))))
        lower = int(start_year) if start_year is not None else 1900
        upper = int(end_year) if end_year is not None else 9999
        rows = aggregate_region_annual_rows(
            raw_rows,
            years=set(range(lower, upper + 1)),
            source_url=crawler.doc_url,
        )
        if not rows:
            raise ValueError("KDCA Region01 returned no annual observations")
        latest = max(date.fromisoformat(row["Date"]) for row in rows)
        return KRUpdateFetchResult(rows, latest, getattr(updater, "output_csv", Path("korea_region_annual.csv")), [], [])
    finally:
        crawler.session.close()
