"""PHO IDTO byte acquisition and independent current-year normalization."""
import asyncio
import csv
import io
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from src.data.crawlers.ca import CanadaOntarioPHOCrawler, normalize_export_file, normalize_powerbi_monthly_rows, discover_monthly_visual, MAX_EXPORT_BYTES, _content_hash, OUTPUT_FIELDS, extract_embed_context, powerbi_headers, build_query_payload
from src.data.processors.ca import CAOntarioUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer


async def collect(writer, *, source, months=None, output_csv, force=False, input_file=None, reporting_year=None, use_configured_file=False):
    if source not in {"all", "pho_idto_monthly", "pho_idto", "idto", "ontario", "ca-on"}:
        raise ValueError(f"Unsupported Ontario source: {source}")
    crawler = CanadaOntarioPHOCrawler(save_raw=False)
    try:
        path = Path(input_file) if input_file else None
        if path is None and use_configured_file:
            configured = os.getenv(crawler.file_env, "").strip()
            if not configured:
                raise ValueError(f"Configured-file replay requested but {crawler.file_env} is unset")
            path = Path(configured)
        if path is not None:
            if not path.is_file():
                raise FileNotFoundError(path)
            if path.stat().st_size > MAX_EXPORT_BYTES:
                raise ValueError("Configured PHO IDTO export exceeds the file-size limit")
            await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="pho_export_file", metadata={"filename": path.name, "source_file_mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "retrieved_at": datetime.now(timezone.utc).isoformat(), "landing_url": crawler.landing_url})
        else:
            # This method only discovers the visual/query and decodes the
            # source envelope; disease/month normalization happens below.
            # The authentication wrapper contains a short-lived bearer token;
            # retain the source metadata/query entities, never the auth wrapper.
            wrapper = await asyncio.to_thread(crawler.get, crawler.embed_url)
            context = extract_embed_context(wrapper.text, expected_report_id=crawler.report_id)
            metadata_url = f"{context.cluster_url}/explore/reports/{context.report_id}/modelsAndExploration?preferReadOnlySession=true&skipQueryData=true"
            with capture_writer(writer):
                response = await asyncio.to_thread(crawler.get, metadata_url, headers=powerbi_headers(context), allow_redirects=False)
                if response.is_redirect:
                    raise RuntimeError("Power BI metadata endpoint returned an unexpected redirect")
                visual = discover_monthly_visual(response.json(), page_display_name=crawler.page_display_name, preferred_visual_name=crawler.visual_name)
                query = await asyncio.to_thread(crawler.post, f"{context.cluster_url}/explore/querydata?synchronous=true", headers=powerbi_headers(context, post=True), json=build_query_payload(visual, report_id=context.report_id), allow_redirects=False)
                if query.is_redirect:
                    raise RuntimeError("Power BI query endpoint returned an unexpected redirect")
    finally:
        crawler.session.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv, force=False, input_file=None, reporting_year=None, use_configured_file=False):
    crawler = CanadaOntarioPHOCrawler(save_raw=False)
    try:
        source_file = next((item for item in artifacts if item.kind == "pho_export_file"), None)
        source_sha, mtime, refresh = "", "", ""
        if source_file:
            meta = source_file.metadata_
            retrieved = meta["retrieved_at"]
            rows = normalize_export_file(Path(meta["filename"]), reporting_year=reporting_year, retrieved_at=retrieved, source_url=meta["landing_url"], allow_authoritative_revision=force, content=read_artifact(source_file))
            mode, source_sha, mtime = "official_export_file", source_file.sha256, meta["source_file_mtime"]
        else:
            metadata = next((item for item in artifacts if "/modelsAndExploration" in str(item.source_url)), None)
            query = next((item for item in artifacts if "/querydata" in str(item.source_url)), None)
            if metadata is None or query is None:
                raise RuntimeError("PHO collection is missing metadata or query-response bytes")
            visual = discover_monthly_visual(json.loads(read_artifact(metadata)), page_display_name=crawler.page_display_name, preferred_visual_name=crawler.visual_name)
            retrieved = query.created_at.isoformat()
            _, rows = normalize_powerbi_monthly_rows(json.loads(read_artifact(query)), visual=visual, retrieved_at=retrieved, source_url=crawler.landing_url)
            mode, refresh = "powerbi_read_only", visual.model_refresh_time
        diseases = len({row["RawDiseaseLabel"] for row in rows})
        populated = len({(int(row["Year"]), int(row["Month"])) for row in rows})
        unpublished = max(0, diseases * 12 - len(rows))
        if months is not None:
            requested = set(map(tuple, months))
            rows = [row for row in rows if (int(row["Year"]), int(row["Month"])) in requested]
        if not rows:
            raise RuntimeError("PHO IDTO returned no rows for the requested months")
        if len({int(row["Year"]) for row in rows}) != 1:
            raise ValueError("PHO IDTO preliminary snapshot unexpectedly spans multiple years")
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        rows = list(csv.DictReader(io.StringIO(stream.getvalue())))
        updater._validate_rows(rows)
        return CAOntarioUpdateFetchResult(rows, max(date.fromisoformat(row["Date"]) for row in rows), Path(output_csv), [], diseases, _content_hash(rows), populated, unpublished, mode, refresh, source_sha, mtime)
    finally:
        crawler.session.close()
