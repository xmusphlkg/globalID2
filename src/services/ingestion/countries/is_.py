"""Iceland's reviewed current dashboards and historical workbook data lines."""

from __future__ import annotations

import asyncio
import importlib
import json
import mimetypes
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.core.source_scopes import (
    canonicalize_task_source,
    validate_iceland_crawl_options,
)
from src.data.crawlers.is_history import (
    OFFICIAL_WORKBOOKS,
    IcelandHistoryCrawler,
    IcelandHistoryRawFile,
)
from src.data.crawlers.powerbi_public import (
    PowerBIQueryResult,
    PowerBIReportContext,
    decode_dsr_v2,
)
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.stages import mark_dirty
from src.services.ingestion.strategies import UpdaterCommitStrategy

crawler_module = importlib.import_module("src.data.crawlers.is")
processor_module = importlib.import_module("src.data.processors.is")
HISTORY_SOURCES = {"is_doh_history", "is_doh_legacy_icd"}


class RecordedSession:
    """Capture exact metadata responses while Power BI discovers its route."""

    def __init__(self, session):
        self.session = session
        self.responses = []

    def get(self, *args, **kwargs):
        response = self.session.get(*args, **kwargs)
        self.responses.append(response)
        return response

    def post(self, *args, **kwargs):
        response = self.session.post(*args, **kwargs)
        self.responses.append(response)
        return response


def context_data(context):
    return {
        key: value
        for key, value in asdict(context).items()
        if key not in {"models_payload", "schema_payload", "landing_html"}
    }


def scope_queries(scope):
    if scope == crawler_module.SOURCE_SCOPE_ANNUAL:
        return [
            (entity, ("AR", "SJUKDOMUR")) for entity in crawler_module._ANNUAL_ENTITIES
        ]
    if scope == crawler_module.SOURCE_SCOPE_STI:
        return [("Kynsjúkdómar", ("AR", "MAN", "SJUKDOMUR"))]
    return [("Sýkingar", ("ISO_AR", "VIKUNUMER_ISO", "SJUKDOMUR"))]


class SourceUnavailable(RuntimeError):
    """A source failure eligible for the existing cached-snapshot recovery."""


async def fetch(operation, *args, **kwargs):
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except Exception as exc:
        raise SourceUnavailable(str(exc)) from exc


async def collect(
    writer, *, source, history_csv=None, fill_missing=False, start_year=None, **options
):
    normalized = canonicalize_task_source(source, country_code="IS")
    validate_iceland_crawl_options(
        country_code="IS",
        source=normalized,
        fill_missing=fill_missing,
        start_year=start_year,
    )
    if normalized in HISTORY_SOURCES:
        return await collect_history(writer, source=normalized)
    scopes = processor_module._source_scopes(normalized)
    crawler = crawler_module.IcelandDOHCrawler()
    recorded = RecordedSession(crawler.session)
    crawler.powerbi.session = recorded
    error = None
    try:
        try:
            urls = await fetch(crawler.discover_view_urls)
            for scope in scopes:
                context = await fetch(crawler.powerbi.discover, urls[scope])
                for response in recorded.responses:
                    await writer.write(
                        response.content,
                        source_url=response.url,
                        kind="archive_json"
                        if response.headers.get("content-type", "").startswith(
                            "application/json"
                        )
                        else "html_report",
                        metadata={"scope": scope, "endpoint": "discovery"},
                    )
                recorded.responses.clear()
                for entity, columns in scope_queries(scope):
                    request = await fetch(
                        crawler.powerbi.entity_sum_request,
                        context,
                        entity=entity,
                        group_columns=columns,
                        value_column="FJOLDI",
                    )
                    url = f"{context.api_base_url}/public/reports/querydata?synchronous=true"
                    response = await fetch(
                        crawler.session.post,
                        url,
                        headers=crawler.powerbi._request_headers(
                            context.resource_key, json_body=True
                        ),
                        json=request,
                        timeout=crawler.powerbi.timeout,
                    )
                    await fetch(response.raise_for_status)
                    await writer.write(
                        response.content,
                        source_url=url,
                        kind="archive_json",
                        metadata={
                            "scope": scope,
                            "endpoint": "query",
                            "entity": entity,
                            "context": context_data(context),
                            "request": request,
                        },
                    )
        except SourceUnavailable as exc:
            error = exc
        if history_csv and Path(history_csv).exists():
            await writer.write(
                await asyncio.to_thread(Path(history_csv).read_bytes),
                source_url=Path(history_csv).as_posix(),
                kind="history_csv",
                metadata={"collection_error": str(error) if error else None},
            )
        elif error is not None:
            raise error
        return writer.artifacts
    finally:
        crawler.session.close()


async def collect_history(writer, *, source):
    kinds = (
        {"registry_annual", "registry_disease_monthly"}
        if source == "is_doh_history"
        else {"legacy_icd_monthly"}
    )
    specs = [
        spec
        for spec in OFFICIAL_WORKBOOKS
        if spec.source_kind in kinds and not spec.validation_only
    ]
    if not specs:
        raise ValueError(f"No reviewed Iceland workbooks for {source}")
    crawler = IcelandHistoryCrawler()
    try:
        # The reviewed catalogue is already authoritative; landing discovery is
        # retained as provenance and availability fallback, never a row parser.
        try:
            landing = await asyncio.to_thread(crawler.get, crawler.landing_url)
        except Exception:  # noqa: BLE001 - reviewed catalogue survives discovery outages
            landing = None
        if landing is not None:
            await writer.write(
                landing.content,
                source_url=crawler.landing_url,
                kind="html_report",
                metadata={"endpoint": "history_landing"},
            )
        for spec in specs:
            response = await asyncio.to_thread(crawler.get, spec.url)
            kind = (
                "workbook_xls"
                if spec.filename.lower().endswith(".xls")
                else "workbook_xlsx"
            )
            await writer.write(
                response.content,
                source_url=spec.url,
                kind=kind,
                metadata={"spec": asdict(spec)},
            )
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source, start_year=None, **options):
    normalized = canonicalize_task_source(source, country_code="IS")
    if normalized in HISTORY_SOURCES:
        return organize_history(artifacts, updater=updater)
    scopes = processor_module._source_scopes(normalized)
    cached = []
    grouped = {}
    contexts = {}
    failed = False
    captured = [(artifact, read_artifact(artifact)) for artifact in artifacts]
    for artifact, raw in captured:
        if artifact.kind == "history_csv":
            import csv
            import io

            try:
                cached = updater.normalize_current_rows(
                    list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
                )
            except Exception:  # noqa: BLE001 - unreadable cache must not block live data
                cached = []
            failed = bool(artifact.metadata_.get("collection_error"))
    try:
        for artifact, raw in captured:
            if artifact.metadata_.get("endpoint") == "query":
                payload = json.loads(raw)
                if not isinstance(payload, dict) or payload.get("error"):
                    raise RuntimeError(
                        "Power BI query failed or returned a non-object response"
                    )
                rows = decode_dsr_v2(payload)
                if len(rows) >= 30000:
                    raise RuntimeError(
                        "Power BI query reached its 30000-row safety window"
                    )
                meta = artifact.metadata_
                scope = meta["scope"]
                grouped.setdefault(scope, []).append(
                    (meta["entity"], PowerBIQueryResult(rows, meta["request"], payload))
                )
                contexts[scope] = PowerBIReportContext(
                    **meta["context"],
                    models_payload={},
                    schema_payload={},
                    landing_html="",
                )
    except (ValueError, RuntimeError, KeyError):
        if not cached:
            raise
        failed = True
    crawler = crawler_module.IcelandDOHCrawler()
    counts = {}
    refreshes = {}
    fingerprints = {}
    live = []
    try:
        if not failed:
            try:
                for scope in scopes:
                    context = contexts[scope]
                    rows = crawler.parse_scope_queries(
                        scope,
                        context,
                        grouped[scope],
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    )
                    crawler._validate_scope_rows(scope, rows)
                    counts[scope] = len(rows)
                    refreshes[scope] = context.last_refresh
                    fingerprints[scope] = context.schema_fingerprint
                    # Preserve the old normalized CSV field selection and
                    # empty-column values without materializing a current CSV.
                    projected = [
                        {
                            "Disease": row["RawDiseaseLabel"],
                            **{
                                field: row.get(field, "")
                                for field in crawler_module.CSV_FIELDNAMES
                                if field not in {"", "Disease"}
                            },
                        }
                        for row in rows
                    ]
                    live.extend(updater.normalize_current_rows(projected))
            except Exception:
                if not cached:
                    raise
                live = []
                counts = {}
                refreshes = {}
                fingerprints = {}
        live.sort(key=lambda row: (row["Date"], row["SourceScope"], row["DiseaseCode"]))
        selected = updater._scope_filter(live or cached, scopes)
        if not selected:
            raise RuntimeError("Iceland current crawler produced no usable rows")
        selected = updater._filter_start_year(selected, start_year)
        if not selected:
            raise RuntimeError(
                "No Iceland current rows remained after start-year filtering"
            )
        if not counts:
            counts = {
                scope: sum(row["SourceScope"] == scope for row in selected)
                for scope in scopes
            }
        return processor_module.ISUpdateFetchResult(
            selected,
            updater._latest_row_date(selected),
            updater.output_csv,
            ["[artifacts] offline Iceland source normalization"],
            counts,
            refreshes,
            fingerprints,
        )
    finally:
        crawler.session.close()


def organize_history(artifacts, *, updater):
    files = []
    for artifact in artifacts:
        read_artifact(artifact)
        spec = artifact.metadata_.get("spec")
        if not spec:
            continue
        files.append(
            IcelandHistoryRawFile(
                key=spec["key"],
                source_kind=spec["source_kind"],
                filename=spec["filename"],
                path=artifact.relative_path,
                source_url=artifact.source_url,
                sha256=artifact.sha256,
                size_bytes=artifact.byte_size,
                media_type=mimetypes.guess_type(spec["filename"])[0]
                or "application/octet-stream",
                disease_key=spec["disease_key"],
                validation_only=spec["validation_only"],
            )
        )
    if not files:
        raise ValueError("Iceland history collection has no workbook artifacts")
    prepared = updater.prepare_files(files)
    latest = max(
        (date.fromisoformat(row["Date"]) for row in prepared.series_rows), default=None
    )
    return SimpleNamespace(
        rows=prepared.rows,
        series_rows=prepared.series_rows,
        source_latest_date=latest,
        prepared=prepared,
    )


class IcelandHistoryCommitStrategy:
    async def commit(
        self, db, *, service, updater, fetched, force, task_uuid, **options
    ):
        # Preserve the source-specific lock/write order and current-source
        # precedence enforced by IcelandHistoryProcessor.import_rows.
        await ensure_iceland_country(db)
        series = await service._save_series_rows(db, updater, fetched.series_rows)
        await updater.import_rows(
            db,
            fetched.rows,
            db_latest_date=None,
            source_latest_date=fetched.source_latest_date,
            force=force,
        )
        await mark_dirty(db, line="surveillance", scope_key="IS", task_uuid=task_uuid)
        return SimpleNamespace(inserted_or_updated=series.upserted)


async def ensure_iceland_country(db) -> None:
    """Provision the canonical Iceland row before facts are written."""

    profile = get_country_profile("IS")
    bootstrap = get_country_bootstrap_config("IS")
    await db.execute(
        text(
            """
            INSERT INTO countries (
                code, name, name_en, name_local, language, timezone,
                data_source_url, data_source_type,
                crawler_config, parser_config, disease_mapping_rules, report_config,
                is_active, metadata, notes, created_at, updated_at
            ) VALUES (
                :code, :name, :name_en, :name_local, :language, :timezone,
                :data_source_url, :data_source_type,
                CAST(:crawler_config AS json), CAST(:parser_config AS json),
                CAST(:disease_mapping_rules AS json), CAST(:report_config AS json),
                true, CAST(:metadata AS json), :notes,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                name_en = EXCLUDED.name_en,
                name_local = EXCLUDED.name_local,
                language = EXCLUDED.language,
                timezone = EXCLUDED.timezone,
                data_source_url = EXCLUDED.data_source_url,
                data_source_type = EXCLUDED.data_source_type,
                crawler_config = EXCLUDED.crawler_config,
                parser_config = EXCLUDED.parser_config,
                disease_mapping_rules = EXCLUDED.disease_mapping_rules,
                report_config = EXCLUDED.report_config,
                is_active = true,
                metadata = (
                    COALESCE(countries.metadata, '{}'::json)::jsonb
                    || EXCLUDED.metadata::jsonb
                )::json,
                notes = EXCLUDED.notes,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "code": profile.code,
            "name": profile.name,
            "name_en": profile.name_en,
            "name_local": profile.name_local,
            "language": profile.language,
            "timezone": profile.timezone,
            "data_source_url": bootstrap.get("data_source_url"),
            "data_source_type": bootstrap.get("data_source_type"),
            "crawler_config": json.dumps(bootstrap.get("crawler_config", {})),
            "parser_config": json.dumps(bootstrap.get("parser_config", {})),
            "disease_mapping_rules": json.dumps(
                bootstrap.get("disease_mapping_rules", {})
            ),
            "report_config": json.dumps(bootstrap.get("report_config", {})),
            "metadata": json.dumps(
                {
                    "standard_source": profile.source,
                    "iso_alpha2": profile.code,
                    "iceland_surveillance_pipeline": True,
                }
            ),
            "notes": bootstrap.get("notes"),
        },
    )


class IcelandCurrentCommitStrategy(UpdaterCommitStrategy):
    async def commit(self, db, **kwargs):
        await ensure_iceland_country(db)
        return await super().commit(db, **kwargs)
