"""Capture and organize INS BES source-native cumulative dashboard cells."""

from __future__ import annotations

import asyncio
import csv
import gzip
import hashlib
import json
import multiprocessing
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from src.data.crawlers.co_bes import (
    ARCHIVE_URL,
    DEFAULT_SCOPE,
    HISTORY_START_YEAR,
    PARSER_VERSION,
    BESContractError,
    BESCover,
    BESReport,
    discover_archive_links,
    discover_sharepoint_list_items,
    inspect_pdf,
    normalize_annex_cells,
    parse_observations,
    sharepoint_library_context,
    sharepoint_list_url,
    validate_listing_url,
)
from src.data.crawlers.co_bes_cache import BESPDFCache
from src.data.crawlers.co_ins_transport import ColombiaINSOfficialTransport
from src.data.processors.co import ColombiaFetchResult
from src.services.ingestion.artifacts import read_artifact

MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_LIST_BYTES = 8 * 1024 * 1024
MAX_LIST_PAGES = 100
AI_CACHE_VERSION = "co-ins-bes-ai-v1"


def _load_validated_ai_rows(report: BESReport, pdf: bytes, raw_hash: str,
                            ai_cache_directory: str):
    path = Path(ai_cache_directory) / f"{raw_hash}-{AI_CACHE_VERSION}.json"
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BESContractError("BES AI cache is unreadable") from exc
    if document.get("validated") is not True:
        return None
    cached_report = document.get("report") or {}
    cached_cover = document.get("cover") or {}
    if (document.get("pdf_sha256") != raw_hash
            or document.get("prompt_version") != AI_CACHE_VERSION
            or document.get("coverage_scope") != "source_native_cumulative_dashboard_cells"
            or cached_report.get("pdf_url") != report.pdf_url
            or cached_report.get("listed_year") != report.listed_year):
        raise BESContractError("BES AI cache source identity changed")
    try:
        actual_cover = inspect_pdf(pdf)
    except BESContractError:
        evidence = document.get("cover_provenance")
        if (document.get("cover_validation") != "independent_native_cover_agreement"
                or not isinstance(evidence, list) or len(evidence) < 2):
            raise BESContractError("BES AI cover lacks independent validation") from None
        try:
            actual_cover = BESCover(
                int(cached_cover["year"]), int(cached_cover["week"]),
                date.fromisoformat(cached_cover["period_end"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BESContractError("BES AI cover has invalid period") from exc
        if (not 1 <= actual_cover.week <= 53
                or report.listed_year != actual_cover.year
                or actual_cover.period_end.year not in {actual_cover.year,
                                                        actual_cover.year + 1}):
            raise BESContractError("BES AI cover conflicts with official archive year")
    if (cached_cover.get("year") != actual_cover.year
            or cached_cover.get("week") != actual_cover.week
            or cached_cover.get("period_end") != (
                actual_cover.period_end.isoformat() if actual_cover.period_end else None)):
        raise BESContractError("BES AI cache period differs from PDF cover")
    cells = document.get("cells")
    pages = document.get("pages")
    partial = document.get("partial_report", False)
    if (not isinstance(cells, list) or not isinstance(pages, list)
            or not pages or any(not isinstance(page, int) or page < 1 for page in pages)
            or not isinstance(partial, bool)
            or {cell.get("page_1_based") for cell in cells if isinstance(cell, dict)} != set(pages)):
        raise BESContractError("BES AI cache lacks validated cells")
    rows, audit = normalize_annex_cells(cells, report, actual_cover)
    if len(rows) != len(cells):
        raise BESContractError("BES AI cache omitted source cells")
    audit["extraction_method"] = "model_center_pdf_vision"
    audit["ai_cache_path"] = str(path)
    audit["ai_prompt_version"] = AI_CACHE_VERSION
    audit["ai_partial_report"] = partial
    return rows, audit, partial, set(pages)


def _parse_cached_document(report: BESReport, pdf: bytes, cache_directory: str,
                           ai_cache_directory: str):
    raw_hash = hashlib.sha256(pdf).hexdigest()
    ai_rows = _load_validated_ai_rows(report, pdf, raw_hash, ai_cache_directory)
    if ai_rows is not None and not ai_rows[2]:
        return ai_rows[:2]
    identity = json.dumps([PARSER_VERSION, raw_hash, report.pdf_url, report.listed_year],
                          separators=(",", ":"))
    cache_key = hashlib.sha256(identity.encode()).hexdigest()
    directory = Path(cache_directory)
    cache = directory / f"{cache_key}.json.gz"
    if cache.exists():
        try:
            document = json.loads(gzip.decompress(cache.read_bytes()))
            rows = document["rows"]
            detail = document["audit"]
            row_hash = hashlib.sha256(
                json.dumps(rows, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()).hexdigest()
            if (document["cache_key"] == cache_key
                    and document["raw_sha256"] == raw_hash
                    and document["parser_version"] == PARSER_VERSION
                    and document["row_count"] == len(rows)
                    and document["rows_sha256"] == row_hash
                    and detail["sha256"] == raw_hash
                    and detail["source_url"] == report.pdf_url):
                if ai_rows is None:
                    return rows, detail
                return _merge_ai_pages(rows, detail, ai_rows)
        except (OSError, EOFError, ValueError, KeyError, TypeError):
            pass
    rows, detail = parse_observations(pdf, report)
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode()
    document = {"cache_key": cache_key, "raw_sha256": raw_hash,
                "parser_version": PARSER_VERSION, "row_count": len(rows),
                "rows_sha256": hashlib.sha256(serialized).hexdigest(),
                "rows": rows, "audit": detail}
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{cache_key}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(gzip.compress(json.dumps(document, ensure_ascii=False,
                                                  sort_keys=True).encode()))
        temporary.replace(cache)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if ai_rows is None:
        return rows, detail
    return _merge_ai_pages(rows, detail, ai_rows)


def _merge_ai_pages(deterministic_rows, deterministic_audit, ai_result):
    ai_rows, ai_audit, partial, pages = ai_result
    if not partial:
        raise BESContractError("BES AI page merge requires partial-report cache")
    actionable_pages = {gap["page"] for gap in deterministic_audit["gaps"]
                        if not gap.get("source_cell_retained")}
    pages = pages & actionable_pages
    if not pages:
        return deterministic_rows, deterministic_audit
    rows = [row for row in deterministic_rows if row["SourceTablePage"] not in pages]
    rows.extend(row for row in ai_rows if row["SourceTablePage"] in pages)
    identities = [row["SourceRecordID"] for row in rows]
    if len(identities) != len(set(identities)):
        raise BESContractError("BES AI page replacement duplicates a source cell")
    remaining_gaps = [gap for gap in deterministic_audit["gaps"]
                      if gap.get("page") not in pages] + ai_audit["gaps"]
    detail = dict(deterministic_audit)
    detail.update({
        "gaps": remaining_gaps,
        "unparsed_groups": sum(gap["reason"] == "source_year_cumulative_column_missing_or_ambiguous"
                               for gap in remaining_gaps),
        "unparsed_cells": sum(gap["reason"] == "cumulative_count_unreadable"
                              for gap in remaining_gaps),
        "explicit_missing_cells": sum(row["MissingValueStatus"] == "source_missing" for row in rows),
        "source_unreadable_cells": sum(row["MissingValueStatus"] == "source_unreadable"
                                       for row in rows),
        "parsed_rows": len(rows),
        "source_events": sorted({row["RawDiseaseLabel"] for row in rows}),
        "table_pages": sorted(set(deterministic_audit["table_pages"]) | pages),
        "extraction_method": "deterministic_with_model_center_page_replacement",
        "ai_replaced_pages": sorted(pages),
        "ai_cache_path": ai_audit["ai_cache_path"],
        "ai_prompt_version": AI_CACHE_VERSION,
    })
    return rows, detail


def _validated_next_page(url: str, first_url: str) -> str:
    validate_listing_url(url)
    current = urlparse(url)
    first = urlparse(first_url)
    if (current.path.casefold() != first.path.casefold()
            or current.fragment or current.username or current.password):
        raise BESContractError("INS SharePoint pagination URL changed endpoint")
    query = parse_qs(current.query, keep_blank_values=True)
    original = parse_qs(first.query, keep_blank_values=True)
    if any(query.get(key) != values for key, values in original.items()):
        raise BESContractError("INS SharePoint pagination changed year filter")
    if set(query) - set(original) != {"$skiptoken"} or not query["$skiptoken"][0]:
        raise BESContractError("INS SharePoint pagination token missing or invalid")
    return url


def _read_sharepoint_response(response):
    validate_listing_url(response.url)
    declared = response.headers.get("Content-Length")
    try:
        length = int(declared) if declared is not None else None
    except ValueError:
        raise BESContractError("INS SharePoint Content-Length invalid") from None
    if length is not None and (length < 1 or length > MAX_LIST_BYTES):
        raise BESContractError("INS SharePoint response length outside limits")
    chunks = []
    count = 0
    try:
        for chunk in response.iter_content(chunk_size=256 * 1024):
            count += len(chunk)
            if count > MAX_LIST_BYTES:
                raise BESContractError("INS SharePoint response exceeds byte limit")
            chunks.append(chunk)
    except requests.RequestException as exc:
        raise BESContractError(f"INS SharePoint stream failed: {type(exc).__name__}") from None
    payload = b"".join(chunks)
    if length is not None and not response.headers.get("Content-Encoding") and count != length:
        raise BESContractError("INS SharePoint Content-Length mismatch")
    try:
        document = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError):
        raise BESContractError("INS SharePoint response is not complete JSON") from None
    return payload, document, length


def _download_pdf(report, cache_directory, force, transport_type):
    transport = transport_type()
    try:
        return BESPDFCache(cache_directory).get(transport, report.pdf_url, force=force)
    finally:
        transport.close()


def _link_year_hint(report: BESReport) -> int | None:
    years = set(map(int, re.findall(r"(?<!\d)20\d{2}(?!\d)", report.pdf_url)))
    return next(iter(years)) if len(years) == 1 else None


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, download_workers=8, parse_workers=4, force=False,
                  cache_directory="data/cache/co/ins_bes_reports",
                  transport_type=ColombiaINSOfficialTransport, **options):
    if source != DEFAULT_SCOPE:
        raise BESContractError("Unexpected INS BES source scope")
    start = int(start_year)
    end = int(end_year) if end_year is not None else datetime.now(timezone.utc).year
    if start < HISTORY_START_YEAR or end < start or not 1 <= int(download_workers) <= 8:
        raise BESContractError("Invalid INS BES collection window or download limit")
    if not 1 <= int(parse_workers) <= 32:
        raise BESContractError("Invalid INS BES parse limit")
    transport = transport_type()
    try:
        response = await asyncio.to_thread(transport.get, ARCHIVE_URL, stream=True)
        try:
            validate_listing_url(response.url)
            declared = response.headers.get("Content-Length")
            try:
                length = int(declared) if declared is not None else None
            except ValueError:
                raise BESContractError("INS BES archive Content-Length invalid") from None
            if length is not None and (length < 1 or length > MAX_HTML_BYTES):
                raise BESContractError("INS BES archive length outside limits")
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise BESContractError("INS BES archive is not HTML")
            chunks = []
            size = 0
            try:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    size += len(chunk)
                    if size > MAX_HTML_BYTES:
                        raise BESContractError("INS BES archive exceeds byte limit")
                    chunks.append(chunk)
            except requests.RequestException as exc:
                raise BESContractError(f"INS BES archive stream failed: {type(exc).__name__}") from None
            payload = b"".join(chunks)
            if length is not None and not response.headers.get("Content-Encoding") and size != length:
                raise BESContractError("INS BES archive Content-Length mismatch")
            reports = discover_archive_links(payload, listing_url=response.url)
            archive_url = response.url
        finally:
            response.close()
        await writer.write(payload, source_url=archive_url, kind="html_report",
                           metadata={"endpoint": "archive", "sha256": hashlib.sha256(payload).hexdigest(),
                                     "byte_size": size, "content_length": length})
    finally:
        transport.close()

    discovery = "archive_anchors"
    listing_pages = []
    if not reports:
        guid, folder = sharepoint_library_context(payload)
        first_url = sharepoint_list_url(guid, start, end)
        page_url = first_url
        visited = set()
        listed = []
        item_ids = set()
        while page_url:
            if page_url in visited or len(visited) >= MAX_LIST_PAGES:
                raise BESContractError("INS SharePoint pagination loop or limit exceeded")
            visited.add(page_url)
            page_transport = transport_type()
            try:
                response = await asyncio.to_thread(
                    page_transport.get, page_url, stream=True,
                    headers={"Accept": "application/json;odata=verbose"})
                try:
                    raw, document, declared = _read_sharepoint_response(response)
                    page_reports = discover_sharepoint_list_items(
                        document, listing_url=response.url, folder=folder,
                        start_year=start, end_year=end)
                    next_url = document.get("d", document).get("__next") or document.get("@odata.nextLink")
                    if next_url:
                        _validated_next_page(next_url, first_url)
                        if not page_reports:
                            raise BESContractError("INS SharePoint empty intermediate page")
                    elif len(page_reports) == 5000:
                        raise BESContractError("INS SharePoint full final page has ambiguous pagination")
                    if any(report.pdf_url in item_ids for report in page_reports):
                        raise BESContractError("INS SharePoint duplicate PDF across pages")
                    item_ids.update(report.pdf_url for report in page_reports)
                    listed.extend(page_reports)
                    artifact = await writer.write(
                        raw, source_url=response.url, kind="archive_json",
                        metadata={"endpoint": "sharepoint_items", "page": len(visited),
                                  "sha256": hashlib.sha256(raw).hexdigest(),
                                  "byte_size": len(raw), "content_length": declared,
                                  "reported_items": len(page_reports), "next_url": next_url})
                    listing_pages.append({"url": response.url, "sha256": artifact.sha256,
                                          "items": len(page_reports), "next_url": next_url})
                    page_url = next_url
                finally:
                    response.close()
            finally:
                page_transport.close()
        if not listed:
            raise BESContractError("INS SharePoint year-filtered listing is empty")
        reports = listed
        discovery = "sharepoint_year_filtered_items"

    selected = []
    deferred = []
    for report in reports:
        hint = _link_year_hint(report)
        if report.listed_year is not None:
            if not start <= report.listed_year <= end:
                raise BESContractError("INS SharePoint listed year outside request")
            selected.append(report)
        elif hint is not None and hint < start:
            deferred.append({**asdict(report), "status": "deferred_outside_link_hint", "hint_year": hint})
        else:
            selected.append(report)
    failures = []
    captured = []
    semaphore = asyncio.Semaphore(int(download_workers))

    async def fetch(report):
        async with semaphore:
            try:
                payload, receipt = await asyncio.to_thread(
                    _download_pdf, report, Path(cache_directory), bool(force), transport_type)
                artifact = await writer.write(
                    payload, source_url=receipt["final_url"], kind="cda_pdf",
                    metadata={"endpoint": "pdf", "report": asdict(report), **receipt})
                captured.append({"url": report.pdf_url, "status": "captured",
                                 "artifact_sha256": artifact.sha256, **receipt})
            except Exception as exc:  # noqa: BLE001 - retain every failed URL in the plan
                failures.append({"url": report.pdf_url, "status": "download_failed",
                                 "error_type": type(exc).__name__,
                                 "error": str(exc) if isinstance(exc, BESContractError)
                                 else "INS bulletin capture failed"})

    await asyncio.gather(*(fetch(report) for report in selected))
    plan = {"archive_url": archive_url, "archive_sha256": hashlib.sha256(payload).hexdigest(),
            "discovery_method": discovery, "listing_pages": listing_pages,
            "requested_start_year": start, "requested_end_year": end,
            "reports": [asdict(report) for report in selected], "deferred": deferred,
            "captured": sorted(captured, key=lambda item: item["url"]),
            "failed_downloads": sorted(failures, key=lambda item: item["url"]),
            "download_workers": int(download_workers), "parse_workers": int(parse_workers),
            "numeric_contract_status": "not_yet_verified"}
    await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=archive_url,
                       kind="archive_json", metadata={"endpoint": "collection_plan"})
    return writer.artifacts


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
             end_year=None, parse_workers=4,
             parse_cache_directory="data/cache/co/ins_bes_parsed",
             ai_cache_directory="data/cache/co/ins_bes_ai", allow_empty=False, **options):
    updater.normalize_source(source)
    plans = [item for item in artifacts if item.metadata_.get("endpoint") == "collection_plan"]
    if len(plans) != 1:
        raise BESContractError("Exactly one INS BES collection plan required")
    plan = json.loads(read_artifact(plans[0]))
    if int(start_year) != plan["requested_start_year"] or (
        end_year is not None and int(end_year) != plan["requested_end_year"]
    ):
        raise BESContractError("INS BES requested years differ from captured plan")
    if not 1 <= int(parse_workers) <= 32:
        raise BESContractError("Invalid INS BES parse limit")
    expected = {report["pdf_url"]: BESReport(**report) for report in plan["reports"]}
    captured_hashes = {item["url"]: item["sha256"] for item in plan["captured"]}
    documents = []
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "pdf":
            continue
        report = artifact.metadata_["report"]
        if (report["pdf_url"] not in expected
                or artifact.sha256 != artifact.metadata_["sha256"]
                or artifact.sha256 != captured_hashes.get(report["pdf_url"])):
            raise BESContractError("Unlinked or modified INS BES PDF artifact")
        documents.append((expected[report["pdf_url"]], read_artifact(artifact)))
    if plan["failed_downloads"]:
        raise BESContractError(f"INS BES downloads failed: {len(plan['failed_downloads'])}; see collection plan")
    if len(documents) != len(expected) or not documents:
        raise BESContractError("INS BES capture incomplete or archive has no candidate reports")
    rows = []
    parsed = []
    failures = []
    with ProcessPoolExecutor(max_workers=int(parse_workers),
                             mp_context=multiprocessing.get_context("spawn")) as executor:
        futures = [(report, executor.submit(_parse_cached_document, report, pdf,
                                            str(parse_cache_directory), str(ai_cache_directory)))
                   for report, pdf in documents]
        for report, future in futures:
            try:
                report_rows, detail = future.result()
                if not plan["requested_start_year"] <= detail["year"] <= plan["requested_end_year"]:
                    continue
                rows.extend(report_rows)
                parsed.append(detail)
            except (BESContractError, ValueError) as exc:
                failures.append({"url": report.pdf_url, "error_type": type(exc).__name__,
                                 "error": str(exc)})
    if not rows and not allow_empty:
        raise BESContractError("No INS BES dashboard cumulative observations parsed; see source originals")
    identities = set()
    for row in rows:
        identity = row["SourceRecordID"]
        if identity in identities:
            raise BESContractError("Duplicate INS BES source record across reports")
        identities.add(identity)
    rows.sort(key=lambda row: (row["Date"], row["SourceRecordID"]))
    output = Path(updater.output_csv)
    if rows:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=output.parent,
                prefix=f".{output.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                writer = csv.DictWriter(stream, fieldnames=sorted({key for row in rows for key in row}))
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    gaps = [gap | {"report_url": detail["source_url"]}
            for detail in parsed for gap in detail["gaps"]]
    ambiguous_rows = sum(bool(row["SourceLabelAmbiguous"]) for row in rows)
    fixed_annex_complete = not failures and not gaps and len(parsed) == len(expected)
    audit = {
        "status": "parsed_with_gaps" if failures or gaps else "parsed_source_native",
        "requested_start_year": plan["requested_start_year"],
        "requested_end_year": plan["requested_end_year"],
        "discovered_reports": len(expected), "parsed_reports": len(parsed),
        "failed_reports": failures,
        "unparsed_groups": sum(detail["unparsed_groups"] for detail in parsed),
        "unparsed_cells": sum(detail["unparsed_cells"] for detail in parsed),
        "explicit_missing_cells": sum(detail["explicit_missing_cells"] for detail in parsed),
        "source_unreadable_cells": sum(detail.get("source_unreadable_cells", 0)
                                       for detail in parsed),
        "parser_gaps": gaps, "source_rows": len(rows),
        "source_events": len({row["SourceDiseaseCode"] for row in rows}),
        "ambiguous_source_label_rows": ambiguous_rows,
        "coverage_scope": "source_native_cumulative_dashboard_cells",
        "fixed_annex_cells_complete": fixed_annex_complete,
        "coverage_complete": fixed_annex_complete,
        "source_definitions_fully_resolved": ambiguous_rows == 0,
        "all_narrative_tables_imported": False,
        "report_audits": [{key: value for key, value in detail.items() if key != "gaps"}
                          for detail in parsed],
        "period_type": "source_epi_year_to_date_at_week",
        "weekly_incidence_derived": False,
        "national_aggregate_derived": False,
        "public_release_enabled": False,
        "source_csv": str(output),
    }
    return ColombiaFetchResult(
        rows, max((date.fromisoformat(row["Date"]) for row in rows), default=None), output,
        [f"[CO BES] {len(rows)} source-native cumulative observations from " +
         f"{len(parsed)}/{len(expected)} PDFs"], audit,
    )


def _candidate_annex_pages(pdf_path: Path) -> list[int]:
    import pdfplumber

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for index in range(max(0, len(pdf.pages) - 10), len(pdf.pages)):
            text = pdf.pages[index].extract_text() or ""
            if re.search(
                r"Comportamiento de la notificaci[oó]n por "
                r"(?:departamento|entidad territorial)", text, re.IGNORECASE,
            ) and "Departamento" in text and "Amazonas" in text:
                pages.append(index + 1)
    return pages


async def organize_async(artifacts, *, updater, source=DEFAULT_SCOPE,
                         start_year=HISTORY_START_YEAR, end_year=None,
                         parse_workers=4, **options):
    """Ask the model center only for reports/pages with deterministic gaps."""
    from src.services.ingestion.co_bes_ai import extract_report

    arguments = dict(updater=updater, source=source, start_year=start_year,
                     end_year=end_year, parse_workers=parse_workers, **options)
    first = await asyncio.to_thread(organize, artifacts, allow_empty=True, **arguments)
    reports = {artifact.metadata_["report"]["pdf_url"]:
               (BESReport(**artifact.metadata_["report"]), Path(artifact.relative_path))
               for artifact in artifacts if artifact.metadata_.get("endpoint") == "pdf"}
    targets = {}
    for failure in first.source_audit["failed_reports"]:
        targets[failure["url"]] = None
    for gap in first.source_audit["parser_gaps"]:
        if gap.get("source_cell_retained"):
            continue
        if gap["report_url"] not in targets:
            targets.setdefault(gap["report_url"], set()).add(int(gap["page"]))
    if not targets:
        return first

    semaphore = asyncio.Semaphore(2)

    async def repair(url, pages):
        async with semaphore:
            report, pdf_path = reports[url]
            selected = pages if pages is not None else await asyncio.to_thread(
                _candidate_annex_pages, pdf_path)
            if not selected:
                raise BESContractError("No fixed BES annex pages identified for AI fallback")
            await extract_report(pdf_path, report, sorted(selected), partial_report=pages is not None,
                                 cache_directory=Path(options.get("ai_cache_directory",
                                                                  "data/cache/co/ins_bes_ai")))

    results = await asyncio.gather(*(repair(url, pages) for url, pages in targets.items()),
                                   return_exceptions=True)
    ai_failures = [{"url": url, "error_type": type(result).__name__}
                   for url, result in zip(targets, results, strict=True)
                   if isinstance(result, BaseException)]
    if len(ai_failures) == len(targets):
        if not first.rows:
            raise BESContractError("BES model-center fallback did not yield verified annex cells")
        first.source_audit["ai_failures"] = ai_failures
        return first
    updated = await asyncio.to_thread(organize, artifacts, **arguments)
    updated.source_audit["ai_failures"] = ai_failures
    return updated
