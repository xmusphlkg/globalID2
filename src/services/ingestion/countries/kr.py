"""KDCA collection retains raw envelopes and files; organization is offline."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from src.data.crawlers.kr import (
    KoreaKDCAOpenAPICrawler, _extract_items, aggregate_period_region_rows,
    normalize_kdca_download_rows, validate_kdca_national_rows,
    DEFAULT_PORTAL_SOURCE_NAME,
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


async def collect(writer, *, source, months, history_csv, source_file=None, source_dir=None):
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


def organize(artifacts, *, updater, source, months, history_csv, source_file=None, source_dir=None):
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
