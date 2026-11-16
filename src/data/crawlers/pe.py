"""Peru's full official weekly bulletin directory and conservative case facts.

The source includes historical scans, modern numerical summaries and unrelated
international news. Only explicitly national, typed observations are admitted.
Every linked report remains in the archival plan even when its OCR is unusable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

import requests
from lxml import html

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "pe_cdc_weekly"
DEFAULT_SOURCE_NAME = "Peru CDC Epidemiological Bulletin"
ONTOLOGY_SOURCE_ID = "SRC_PE_CDC"
INDEX_URL = "https://www.dge.gob.pe/portalnuevo/informacion-publica/boletines-epidemiologicos/"
LISTING_URL = "https://epipublic.dge.gob.pe/publico/listado_ajax"
LISTING_FORM = {"bq_anio": "", "bq_categoria": "1"}
HISTORY_START_YEAR = 1993
ROOT = Path(__file__).resolve().parents[3]
# Bind cache identity to the parser code actually loaded by this process.
# Reading __file__ later can observe edits while functions still run old code.
PARSER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DEFAULT_CACHE = ROOT / "data/current/pe/archive"
_PDFIUM_LOCK = Lock()


class PeruContractError(ValueError):
    """A source count, scope or period cannot be independently verified."""


def _plain(text):
    return " ".join(str(text or "").split())


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def discover_reports(payload, *, start_year=HISTORY_START_YEAR, end_year=None):
    """Read actual directory metadata, never guess weeks from PDF filenames."""
    tree = html.fromstring(payload.decode("utf-8"))
    targets = {}; directory_rows = 0
    for row in tree.xpath("//tbody/tr"):
        urls = row.xpath(".//a[@href]/@href")
        periods = row.xpath(".//span[contains(concat(' ',normalize-space(@class),' '),' periodoVal ')]/text()")
        titles = row.xpath(".//h4//text()")
        if not urls or not periods:
            raise PeruContractError("Official bulletin directory row lacks a PDF or source week")
        directory_rows += 1
        url = urls[0]
        if urlparse(url).hostname not in {"www.dge.gob.pe", "dge.gob.pe", "epipublic.dge.gob.pe"} or not url.lower().endswith(".pdf"):
            raise PeruContractError("Official directory changed PDF host/format")
        year_match = re.search(r"(?:boletin_|/)((?:19|20)\d{2})", url)
        if not year_match:
            raise PeruContractError("Directory report has no source year")
        year = int(year_match[1]); week = int(periods[0].strip())
        if not 1 <= week <= 53:
            raise PeruContractError("Directory source week outside 1–53")
        title = _plain(" ".join(titles))
        title_year = re.search(r"\b((?:19|20)\d{2})\b", title)
        catalog_issues = []
        if title_year and int(title_year[1]) != year:
            catalog_issues.append("directory_title_year_conflicts_with_url")
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        publication = re.search(r"fecha\s+publicacion:\s*(\d{2}-\d{2}-\d{4})", _fold(_plain(row.text_content())))
        try:
            published_date = datetime.strptime(publication[1], "%d-%m-%Y").date().isoformat() if publication else None
        except ValueError:
            published_date = None
            catalog_issues.append("invalid_or_unknown_directory_publication_date")
        target = {"url": url, "year": year, "week": week, "title": title,
                  "published_date": published_date, "published_date_raw": publication[1] if publication else None,
                  "directory_copies": 1, "catalog_issues": catalog_issues}
        if url in targets:
            previous = targets[url]
            if (previous["year"], previous["week"]) != (year, week):
                previous["catalog_issues"] = sorted(set(previous["catalog_issues"] + ["directory_conflicting_week_aliases"]))
                previous.setdefault("directory_week_aliases", [previous["week"]])
                if week not in previous["directory_week_aliases"]:
                    previous["directory_week_aliases"].append(week)
            previous["directory_copies"] += 1
        else:
            targets[url] = target
    if not directory_rows or not targets:
        raise PeruContractError("No official bulletins in requested history range")
    return sorted(targets.values(), key=lambda target: (target["year"], target["week"], target["url"]))


def pdf_text(payload):
    import pypdfium2
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise PeruContractError("Source artifact is not a complete PDF")
    with _PDFIUM_LOCK, pypdfium2.PdfDocument(payload) as document:
        texts = []
        for page in document:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range().replace("\r\n", "\n"))
            textpage.close(); page.close()
    return texts


_MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
           "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
           "noviembre": 11, "diciembre": 12}


def report_period(first_page, *, reference, calendar_definition_verified=False):
    """Source Sunday–Saturday intervals, read explicitly, never ISO-assigned."""
    text = _fold(_plain(first_page))
    if not re.search(r"\b" + str(reference["year"]) + r"\b", text):
        raise PeruContractError("PDF cover does not independently confirm directory reporting year")
    explicit_week = re.search(r"semana\s+ep\w+\s*(?::|n[°ºo.]*\s*)\s*(\d{1,2})\b", text)
    if explicit_week and int(explicit_week[1]) != int(reference["week"]):
        raise PeruContractError("PDF header week conflicts with official directory")
    # Historical spellings and modern 'Del 24 al 30 de diciembre del 2000'.
    pattern = (r"\bdel\s+(\d{1,2})\s*(?:de\s+)?([a-z]+)?\s*(?:(?:de|del)\s+)?(\d{4})?"
               r"\s*(?:al|[-–])\s*(\d{1,2})\s*(?:de\s+)?([a-z]+)\s*(?:(?:de|del)\s+)?(\d{4})?")
    matches = list(re.finditer(pattern, text))
    periods = []
    for match in matches:
        day1, month1, year1, day2, month2, year2 = match.groups()
        if not year2:
            if not re.search(r"\b" + str(reference["year"]) + r"\b", text):
                continue
            year2 = str(reference["year"])
        if month2 not in _MONTHS or (month1 and month1 not in _MONTHS):
            continue
        try:
            end = date(int(year2), _MONTHS[month2], int(day2))
            start = date(int(year1 or year2), _MONTHS[month1 or month2], int(day1))
            if start > end and not year1:
                start = start.replace(year=start.year - 1)
        except ValueError:
            continue
        if (end - start).days == 6 and start.year in {int(reference["year"]) - 1, int(reference["year"])} and end.year in {int(reference["year"]), int(reference["year"]) + 1}:
            periods.append((start, end))
    periods = list(dict.fromkeys(periods))
    if not periods and calendar_definition_verified:
        ending = re.search(r"\bal\s+(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})\b", text)
        if ending and ending[2] in _MONTHS:
            end = date(int(ending[3]), _MONTHS[ending[2]], int(ending[1]))
            if end.weekday() == 5 and end.year in {int(reference["year"]), int(reference["year"]) + 1}:
                periods = [(end - timedelta(days=6), end)]
    if len(periods) != 1:
        raise PeruContractError("No unique explicit seven-day bulletin reporting interval")
    return periods[0]


def _integer(text):
    value = text.strip()
    if re.fullmatch(r"\d+", value):
        return str(int(value))
    if re.fullmatch(r"\d{1,3}(?:[ ,.\u00a0]\d{3})+", value):
        return str(int(re.sub(r"[ ,.\u00a0]", "", value)))
    raise PeruContractError(f"Ambiguous source case count: {value!r}")


def _native_row(label, value, *, kind, reference, start, end, source_url, digest, page, evidence):
    slug = re.sub(r"[^a-z0-9]+", "_", _fold(label)).strip("_")
    definition = f"pe_cdc_national_narrative_{kind}_v1"
    return {"Date": end.isoformat(), "Year": str(end.year), "Month": str(end.month),
        "Week": str(reference["week"]), "PeriodStart": "" if kind.startswith("cumulative") else start.isoformat(),
        "PeriodEnd": end.isoformat(), "SourceReportPeriodStart": start.isoformat(),
        "SourceReportYear": str(reference["year"]), "SourceWeek": str(reference["week"]),
        "DiseaseCode": "__source_native__", "SourceDiseaseCode": slug + "__" + kind,
        "RawDiseaseLabel": label, "Cases": value, "MissingValueStatus": "",
        "Measure": ("cumulative_case_notifications" if kind.startswith("cumulative_cases") else
                    "cumulative_deaths" if kind.startswith("cumulative_deaths") else
                    "deaths" if kind.startswith("weekly_deaths") else "cases"),
        "Frequency": "weekly", "PeriodType": "cumulative" if kind.startswith("cumulative") else "weekly",
        "ReportingBasis": "notification", "DefinitionVersion": definition,
        "Geography": "Peru", "GeographyKey": "country:PE:national",
        "Source": DEFAULT_SOURCE_NAME, "SourceURL": source_url, "SourcePage": str(page),
        "SourcePublishedAt": reference.get("published_date") or "", "RawSHA256": digest,
        "SourceEvidence": evidence, "DatasetStatus": "provisional_revisable", "IsProvisional": "true",
        "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
        "PublicReleaseEnabled": "false", "License": "source_terms_review_required",
        "Dimensions": json.dumps({"source_case_label": label, "source_calendar": "explicit_bulletin_dates",
            "source_report_url": source_url, "source_report_sha256": digest,
            "count_kind": kind, "source_reporting_year": reference["year"], "source_reporting_week": reference["week"],
            "case_classification": kind.split("__", 1)[1] if "__" in kind else "reported_unspecified", "extraction": "explicit_national_narrative",
            "evidence": evidence, "source_page": page}, ensure_ascii=False, sort_keys=True)}


def parse_national_summary_tables(payload, *, texts, reference, source_url, digest, start, end):
    """All rows in the national comparison summary, with typed current columns.

    Classified 2019-style tables and unclassified 2013-style tables have
    different contracts. Source-defined merged rows (malaria, maternal deaths)
    remain unsplit; they are not forced into confirmed/probable columns.
    """
    import io
    import pdfplumber
    rows = []; audits = []
    candidate_pages = [i for i, text in enumerate(texts) if
        "enfermedades/eventos sujetos a vigilancia epidemiologica" in _fold(_plain(text))]
    if not candidate_pages:
        return rows, audits
    with pdfplumber.open(io.BytesIO(payload)) as document:
        for index in candidate_pages:
            text = _fold(_plain(texts[index]))
            if "peru" not in text or str(reference["year"]) not in text:
                continue
            for table in document.pages[index].extract_tables():
                if not table:
                    continue
                first = _plain(" ".join(str(value or "") for value in table[0]))
                if "enfermedad" not in _fold(first) or str(reference["year"]) not in first or str(int(reference["year"]) - 1) not in first:
                    continue
                classified = "confirmados" in _fold(first) and "probables" in _fold(first)
                schema = "classified_comparison_v1" if classified else "unclassified_comparison_v1"
                audit = {"page": index + 1, "schema": schema, "categories": [], "rejected_rows": [], "rows": 0}
                header = _fold(first if classified else _plain(" ".join(str(v or "") for r in table[:2] for v in r)))
                valid_header = (re.search(r"\bsemana\s+" + str(reference["week"]) + r"\b", header)
                    and "acumulado" in header and "defuncion" in header
                    and re.search(r"i\s*\.\s*a\s*\.", header)
                    and first.index(str(int(reference["year"]) - 1)) < first.index(str(reference["year"])))
                if classified:
                    valid_header = valid_header and header.count("confirmados") == 4 and header.count("probables") == 4
                else:
                    valid_header = valid_header and len(table[0]) == 3 and (
                        _plain(table[0][1]) == str(int(reference["year"]) - 1)
                        and _plain(table[0][2]) == str(reference["year"]))
                if not valid_header:
                    audit["rejected_rows"].append({"raw": first, "reason": "unverified_summary_column_header"})
                    audits.append(audit)
                    continue
                current_index = None
                if not classified:
                    current_index = next((i for i, value in enumerate(table[0]) if _plain(value) == str(reference["year"])), None)
                    if current_index is None:
                        continue
                source_rows = table[1:]
                if classified:
                    # Some modern PDFs have only three geometric table rows:
                    # header, many disease lines in one cell, final disease.
                    # Keep the physical source line boundaries before _plain
                    # can erase them. Multi-column/wrapped rows need a separate
                    # verified schema and are deliberately not reconstructed.
                    source_rows = [cells if len(cells) != 1 else [line]
                        for cells in source_rows
                        for line in (str(cells[0] or "").splitlines() if len(cells) == 1 else [None])]
                for cells in source_rows:
                    if classified:
                        raw = _plain(" ".join(str(value or "") for value in cells))
                        match = re.fullmatch(r"(.+?)\s+((?:\d+(?:[.,]\d+)?\s+)*\d+(?:[.,]\d+)?)", raw)
                        if not match:
                            if raw:
                                audit["rejected_rows"].append({"raw": raw, "reason": "nonrectangular_or_unreadable_numeric_row"})
                            continue
                        label, numbers = match[1], match[2].split()
                        if re.search(r"(?<!\S)\d+(?:[.,]\d+)?(?!\S)", label) or len(label) > 180:
                            audit["rejected_rows"].append({"raw": raw,
                                "reason": "category_label_contains_numeric_table_cells_or_multiple_rows"})
                            continue
                        if len(numbers) not in {4, 6, 8, 10, 12} or (
                            len(numbers) == 6 and not _fold(label).replace(" ", "").startswith("muerte")):
                            audit["rejected_rows"].append({"raw": raw, "reason": "unsupported_classified_row_column_count"})
                            continue
                        count = len(numbers) // 2
                        current, comparison = numbers[count:], numbers[:count]
                    else:
                        if current_index >= len(cells):
                            audit["rejected_rows"].append({"raw": str(cells), "reason": "truncated_year_group"})
                            continue
                        label = _plain(cells[0]); current = _plain(cells[current_index]).split()
                        comparison = _plain(cells[current_index - 1]).split()
                        if not label or _fold(label).startswith("semana") or not current:
                            continue
                        if len(current) not in {2, 3, 4}:
                            audit["rejected_rows"].append({"raw": str(cells), "reason": "unsupported_unclassified_row_column_count"})
                            continue
                    is_death_event = _fold(label).replace(" ", "").startswith("muerte")
                    if len(current) in {2, 3} and is_death_event:
                        metrics = [(0, "weekly_deaths"), (1, "cumulative_deaths")]
                    elif is_death_event:
                        audit["rejected_rows"].append({"raw": str(cells),
                            "reason": "death_event_has_unverified_classification_columns"})
                        continue
                    elif len(current) == 2:
                        audit["rejected_rows"].append({"raw": str(cells), "reason": "unverified_merged_event_row"})
                        continue
                    elif classified and len(current) in {5, 6}:
                        metrics = [(0, "weekly_cases__confirmed"), (1, "weekly_cases__probable"),
                                   (2, "cumulative_cases__confirmed"), (3, "cumulative_cases__probable"),
                                   (4, "cumulative_deaths")]
                    else:
                        metrics = [(0, "weekly_cases"), (1, "cumulative_cases"), (2, "cumulative_deaths")]
                    try:
                        parsed = [(kind, _integer(current[position])) for position, kind in metrics]
                    except PeruContractError as exc:
                        audit["rejected_rows"].append({"raw": str(cells), "reason": str(exc)})
                        continue
                    for kind, value in parsed:
                        row = _native_row(label, value, kind=kind, reference=reference, start=start, end=end,
                            source_url=source_url, digest=digest, page=index + 1, evidence=_plain(str(cells)))
                        row["DefinitionVersion"] = f"pe_cdc_national_summary_{schema}_{kind}_v1"
                        dimensions = json.loads(row["Dimensions"])
                        dimensions.update({"extraction": "national_summary_table", "table_schema": schema,
                            "comparison_year": int(reference["year"]) - 1, "comparison_values": comparison,
                            "source_current_year_values": current,
                            "incidence_rate": current[-1] if len(current) in {4, 6} else None,
                            "incidence_denominator": "source_footnote; syphilis may use live births"})
                        row["Dimensions"] = json.dumps(dimensions, ensure_ascii=False, sort_keys=True)
                        rows.append(row); audit["rows"] += 1
                    audit["categories"].append(label)
                if audit["categories"] or audit["rejected_rows"]:
                    audits.append(audit)
    return rows, audits


def parse_bulletin_pdf(payload, *, source_url, reference):
    """Keep every independently verified national narrative category.

    OCR substitutions, international news, local counts and rates never become
    national cases. Unsupported summary matrices remain explicitly audited.
    """
    texts = pdf_text(payload)
    all_text = _fold(_plain(" ".join(texts)))
    calendar_verified = bool(re.search(r"semana epidemiologica inicia el dia domingo.{0,100}sabado", all_text))
    start, end = report_period(texts[0], reference=reference, calendar_definition_verified=calendar_verified)
    digest = hashlib.sha256(payload).hexdigest(); rows = []; issues = []; seen = {}
    numeric = r"(?:\d{1,3}(?:[ ,.\u00a0]\d{3})+|\d+)"
    cumulative = re.compile(r"(?:el\s+)?acumulado\s+nacional\s*(?:es\s*)?[:=]?\s*(" + numeric + r")\s+casos\b", re.I)
    # These headings admit normal source typography but exclude OCR-corrupt
    # symbols or lower-case glyph substitutions in otherwise uppercase labels.
    heading = re.compile(r"(?<![A-Za-zÁÉÍÓÚÑáéíóúñ])([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]{2,}(?:[ .()/-]+[A-Za-zÁÉÍÓÚÑáéíóúñ.]{1,}){0,7})\s*:\s*")
    standalone = re.compile(r"(?:^|\n)\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ /.-]{2,65})\s*(?:\n|$)")
    national_year = re.compile(r"(?:Durante|En)\s+el\s+año\s+(?P<year>\d{4})\s+se\s+"
        r"(?:reportaron|reportan|notificaron|registraron)\s+a\s+nivel\s+nacional\s+"
        r"(?P<count>" + numeric + r")\s+casos\b", re.I)
    for page_number, text in enumerate(texts, 1):
        # News citations in these sections are not Peru national surveillance.
        if any(marker in _fold(text) for marker in ["brotes internacionales", "promed-mail", "fuente: diario"]):
            continue
        def category_heading(match):
            label = _plain(match[1])
            words = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", label)
            return bool(words) and all(word[0].isupper() or word.lower() in {
                "de", "del", "con", "por", "la", "el", "y", "sin"} for word in words)
        headings = sorted([match for match in [*heading.finditer(text), *standalone.finditer(text)]
                           if category_heading(match)], key=lambda match: match.start())
        for index, match in enumerate(headings):
            label = _plain(match[1]); section = _plain(text[match.end():headings[index + 1].start() if index + 1 < len(headings) else len(text)])
            if label in {"TIA", "TIS", "FUENTE", "NOTA", "COMENTARIOS", "RECOMENDACIONES"}:
                continue
            values = [(value, _integer(value[1])) for value in cumulative.finditer(section)]
            values.extend((value, _integer(value["count"])) for value in national_year.finditer(section)
                          if int(value["year"]) == int(reference["year"]))
            if not values:
                continue
            if len(values) != 1:
                issues.append({"page": page_number, "category": label, "reason": "ambiguous_multiple_national_cumulative_values"})
                continue
            value = values[0][1]; evidence = values[0][0][0]
            row = _native_row(label, value, kind="cumulative_cases", reference=reference, start=start,
                end=end, source_url=source_url, digest=digest, page=page_number, evidence=evidence)
            key = (row["SourceDiseaseCode"], row["DefinitionVersion"])
            if key in seen and seen[key]["Cases"] != value:
                raise PeruContractError("Contradictory national narrative values in one report")
            seen[key] = row
    rows = list(seen.values())
    matrix_rows, matrix_audit = parse_national_summary_tables(payload, texts=texts, reference=reference,
        source_url=source_url, digest=digest, start=start, end=end)
    rows.extend(matrix_rows)
    unsupported = [i + 1 for i, text in enumerate(texts) if any(marker in _fold(text) for marker in [
        "notificacion semanal oportuna", "resumen de las enfermedades", "notificacion de enfermedades",
        "indicadores de la vigilancia", "enfermedades o eventos sujetos"])]
    return rows, {"reference": reference, "pages": len(texts), "period_start": start.isoformat(),
        "period_end": end.isoformat(), "parsed_rows": len(rows), "categories": sorted({row["SourceDiseaseCode"] for row in rows}),
        "issues": issues, "national_summary_tables": matrix_audit,
        "unsupported_matrix_pages": [page for page in unsupported if page not in {a["page"] for a in matrix_audit}], "source_url": source_url,
        "raw_sha256": digest, "status": "partial" if issues or unsupported or not rows else "validated_national_narratives"}


class PeruCDCCrawler(BaseCrawler):
    def __init__(self, *, cache_directory=None, **kwargs):
        super().__init__(timeout=45, delay=0.2, **kwargs)
        self.cache_directory = Path(cache_directory or DEFAULT_CACHE)
        self.cache_directory.mkdir(parents=True, exist_ok=True)

    def discover(self, *, start_year=HISTORY_START_YEAR, end_year=None):
        response = self.post(LISTING_URL, data=LISTING_FORM)
        return discover_reports(response.content, start_year=start_year, end_year=end_year), response.content

    def download(self, reference, *, force=False):
        """Complete cached snapshots or resumable public GET, with exact receipts."""
        url = reference["url"]; key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache_directory / (key + ".pdf"); receipt_path = path.with_suffix(".json")
        now = time.time()
        if path.exists() and receipt_path.exists() and not force:
            receipt = json.loads(receipt_path.read_text()); payload = path.read_bytes()
            validated = float(receipt.get("validated_at", receipt.get("retrieved_at", 0)))
            ttl = 3600 if int(reference["year"]) >= datetime.now(timezone.utc).year - 1 else 30 * 86400
            if hashlib.sha256(payload).hexdigest() == receipt.get("sha256") and now - validated < ttl:
                return payload, {"reference": reference, "retrieval_mode": "cached_snapshot", **receipt}
        from src.services.ingestion.capture import assert_source_access, capture_sync
        assert_source_access()
        partial = path.with_suffix(".pdf.part"); partial_receipt = path.with_suffix(".part.json")
        headers = {}; offset = 0
        if partial.exists() and partial_receipt.exists() and not force:
            meta = json.loads(partial_receipt.read_text())
            if meta.get("url") == url:
                offset = partial.stat().st_size
                headers["Range"] = f"bytes={offset}-"
                if meta.get("etag") or meta.get("last_modified"):
                    headers["If-Range"] = meta.get("etag") or meta["last_modified"]
        with self.session.get(url, stream=True, timeout=(15, self.timeout), headers=headers) as response:
            response.raise_for_status()
            if response.status_code == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                if not match or int(match[1]) != offset:
                    raise PeruContractError("Resumed response Content-Range does not match archived partial bytes")
                expected_size = int(match[3]); mode = "ab"
            elif response.status_code == 200:
                offset = 0; expected_size = int(response.headers.get("Content-Length", 0)); mode = "wb"
            else:
                raise PeruContractError(f"Unexpected PDF HTTP status: {response.status_code}")
            transfer = {"url": url, "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"), "expected_bytes": expected_size,
                        "started_at": datetime.now(timezone.utc).isoformat()}
            partial_receipt.write_text(json.dumps(transfer))
            with partial.open(mode) as stream:
                for chunk in response.iter_content(64 * 1024):
                    if chunk:
                        stream.write(chunk); stream.flush()
                os.fsync(stream.fileno())
            if expected_size and partial.stat().st_size != expected_size:
                raise PeruContractError("Source PDF transfer was incomplete; partial bytes retained for resume")
            payload = partial.read_bytes()
            if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                raise PeruContractError("Downloaded bulletin is not a complete PDF; partial bytes retained")
            receipt = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                       "download_url": response.url, "retrieved_at": now, "validated_at": time.time(), **transfer}
            temporary = receipt_path.with_suffix(".json.tmp"); temporary.write_text(json.dumps(receipt))
            partial.replace(path); temporary.replace(receipt_path)
            partial_receipt.unlink(missing_ok=True)
            payload = capture_sync(payload, response.url, response.headers.get("Content-Type", "application/pdf"))
            return payload, {"reference": reference, "retrieval_mode": "live", **receipt}

    async def crawl(self, **kwargs):
        references, _ = await asyncio.to_thread(self.discover, **kwargs)
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=INDEX_URL, raw_data={"reports": references})]

    def parse(self, response):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LISTING_URL,
                              raw_data={"reports": discover_reports(response.content)})]
