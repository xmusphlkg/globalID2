"""Conservative discovery contract for INS weekly epidemiological bulletins.

No observation parser is enabled until complete official PDFs establish the
table, reporting period and revision semantics across current report editions.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse

from lxml import html

ARCHIVE_URL = (
    "https://www.ins.gov.co/buscador-eventos/Paginas/Vista-Boletin-Epidemilogico.aspx"
)
OFFICIAL_HOST = "www.ins.gov.co"
ARCHIVE_SITE_PATH = "/buscador-eventos/"
DEFAULT_SCOPE = "co_ins_bes_weekly"
DEFAULT_SOURCE_NAME = "Colombia INS Weekly Epidemiological Bulletin (BES)"
ONTOLOGY_SOURCE_ID = "SRC_CO_INS_BES"
HISTORY_START_YEAR = 2025


class BESContractError(ValueError):
    """An official report or source listing fails a required identity check."""


class NotYetVerifiedContract(BESContractError):
    """The bulletin's numerical table and period semantics remain unverified."""


@dataclass(frozen=True)
class BESReport:
    pdf_url: str
    listing_url: str
    title: str
    listed_year: int | None = None


@dataclass(frozen=True)
class BESCover:
    year: int
    week: int
    period_end: date | None = None


PARSER_VERSION = "co-ins-bes-dashboard-parser-v4"
SOURCE_DEFINITION_VERSION = "co-ins-bes-dashboard-ytd"
_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
    "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _official_pdf(value: str, base_url: str = ARCHIVE_URL) -> str | None:
    url = urljoin(base_url, value)
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != OFFICIAL_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    path = unquote(parsed.path)
    if "\\" in path or any(segment in {".", ".."} for segment in path.split("/")):
        return None
    if "%" in path and unquote(path) != path:
        return None
    if not path.casefold().startswith(
        ARCHIVE_SITE_PATH.casefold()
    ) or not path.casefold().endswith(".pdf"):
        return None
    return url


def validate_listing_url(value: str) -> str:
    """Require a credential-free INS archive page or SharePoint listing."""
    try:
        parsed = urlparse(value)
        path = unquote(parsed.path)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == OFFICIAL_HOST
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and path.casefold().startswith(ARCHIVE_SITE_PATH.casefold())
            and "\\" not in path
            and all(part not in {".", ".."} for part in path.split("/"))
            and not ("%" in path and unquote(path) != path)
        )
    except ValueError:
        valid = False
    if not valid:
        raise BESContractError("INS archive listing URL is not official")
    return value


def discover_archive_links(
    payload: bytes, *, listing_url: str = ARCHIVE_URL
) -> list[BESReport]:
    """Read actual anchors from a complete official archive page."""
    if not payload or b"</html" not in payload.lower():
        raise BESContractError("Complete INS archive HTML required")
    validate_listing_url(listing_url)
    tree = html.fromstring(payload)
    reports = {}
    for anchor in tree.xpath("//a[@href]"):
        pdf_url = _official_pdf(anchor.get("href", ""), listing_url)
        if pdf_url:
            title = " ".join(anchor.text_content().split()) or unquote(
                urlparse(pdf_url).path.rsplit("/", 1)[-1]
            )
            reports.setdefault(pdf_url, BESReport(pdf_url, listing_url, title))
    return list(reports.values())


def discover_sharepoint_files(
    document: dict, *, listing_url: str = ARCHIVE_URL
) -> list[BESReport]:
    """Read SharePoint Files entries, if the official REST endpoint is reachable."""
    if not isinstance(document, dict):
        raise BESContractError("SharePoint listing must be an object")
    validate_listing_url(listing_url)
    entries = document.get("value")
    if entries is None and isinstance(document.get("d"), dict):
        entries = document["d"].get("results")
    if not isinstance(entries, list):
        raise BESContractError("SharePoint Files collection missing")
    reports = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise BESContractError("SharePoint Files entry is not an object")
        path = entry.get("ServerRelativeUrl")
        if not isinstance(path, str):
            raise BESContractError("SharePoint file lacks ServerRelativeUrl")
        pdf_url = _official_pdf(path)
        if pdf_url:
            reports.setdefault(
                pdf_url,
                BESReport(
                    pdf_url,
                    listing_url,
                    str(
                        entry.get("Name")
                        or unquote(urlparse(pdf_url).path.rsplit("/", 1)[-1])
                    ),
                ),
            )
    return list(reports.values())


def sharepoint_library_context(payload: bytes) -> tuple[str, str]:
    """Read the actual SharePoint library identity embedded in the official view."""
    if not payload or b"</html" not in payload.lower():
        raise BESContractError("Complete INS archive HTML required")
    text = payload.decode("utf-8", errors="replace")
    names = set(re.findall(r'ctx\.listName\s*=\s*"\{([0-9a-fA-F-]{36})\}"', text))
    folders = set(re.findall(r'ctx\.listUrlDir\s*=\s*"([^"]+)"', text))
    if len(names) != 1 or len(folders) != 1:
        raise BESContractError("INS archive SharePoint library identity missing or ambiguous")
    guid = names.pop().lower()
    folder = folders.pop()
    if (not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", guid)
            or folder != "/buscador-eventos/BoletinEpidemiologico"):
        raise BESContractError("INS archive SharePoint library path changed")
    return guid, folder


def sharepoint_list_url(guid: str, start_year: int, end_year: int) -> str:
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", guid):
        raise BESContractError("Invalid INS SharePoint library identifier")
    if not 2025 <= start_year <= end_year <= 2100:
        raise BESContractError("Invalid INS SharePoint year window")
    filters = " or ".join(f"A_x00f1_o eq '{year}'" for year in range(start_year, end_year + 1))
    query = urlencode({"$select": "Id,File/Name,File/ServerRelativeUrl,A_x00f1_o,Consecutivo,Tema_x0020_Central",
                       "$expand": "File", "$filter": filters, "$top": "5000"})
    return ("https://www.ins.gov.co/buscador-eventos/_api/web/lists"
            f"(guid'{guid}')/items?{query}")


def discover_sharepoint_list_items(
    document: dict, *, listing_url: str, folder: str,
    start_year: int, end_year: int,
) -> list[BESReport]:
    validate_listing_url(listing_url)
    if folder != "/buscador-eventos/BoletinEpidemiologico":
        raise BESContractError("INS SharePoint folder changed")
    if not isinstance(document, dict):
        raise BESContractError("INS SharePoint page is not an object")
    container = document.get("d", document)
    if not isinstance(container, dict):
        raise BESContractError("INS SharePoint page body missing")
    entries = container.get("results", container.get("value"))
    if not isinstance(entries, list) or len(entries) > 5000:
        raise BESContractError("INS SharePoint items collection invalid")
    reports = []
    seen_ids = set()
    seen_urls = set()
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("File"), dict):
            raise BESContractError("INS SharePoint item lacks File metadata")
        item_id = item.get("Id")
        year_text = item.get("A_x00f1_o")
        file = item["File"]
        path = file.get("ServerRelativeUrl")
        name = file.get("Name")
        if (not isinstance(item_id, int) or item_id < 1 or item_id in seen_ids
                or not isinstance(year_text, str) or not year_text.isdecimal()
                or not start_year <= int(year_text) <= end_year
                or not isinstance(path, str) or not path.startswith(folder + "/")
                or not isinstance(name, str) or path.rsplit("/", 1)[-1] != name):
            raise BESContractError("INS SharePoint item identity or year invalid")
        url = _official_pdf(quote(path, safe="/"))
        if url is None or url in seen_urls:
            raise BESContractError("INS SharePoint item PDF URL invalid or duplicated")
        seen_ids.add(item_id)
        seen_urls.add(url)
        reports.append(BESReport(url, listing_url, name, int(year_text)))
    return reports


def parse_cover_identity(text: str) -> BESCover:
    """Require the publisher, bulletin title and source epidemiological week."""
    normalized = " ".join(text.split())
    normalized = re.sub(r"\b(de|del)(?=20\d{2}\b)", r"\1 ", normalized, flags=re.IGNORECASE)
    if not re.search(r"Instituto Nacional(?:\s+\S+){0,8}\s+de Salud", normalized, re.IGNORECASE):
        raise BESContractError("BES cover does not establish INS publisher")
    if not re.search(
        r"Bolet[ií]n Epidemiol[oó]gico Semanal", normalized, re.IGNORECASE
    ):
        raise BESContractError("BES cover does not establish weekly bulletin title")
    period_end = None
    period_year = None
    span = re.search(
        r"\b(\d{1,2}) de ([a-záéíóú]+)(?: (?:de(?:l)? )?(20\d{2}))? al "
        r"(\d{1,2}) de ([a-záéíóú]+) de(?:l)? (20\d{2})\b",
        normalized, re.IGNORECASE,
    )
    same_month = re.search(
        r"\b(\d{1,2}) al (\d{1,2}) de ([a-záéíóú]+) de(?:l)? (20\d{2})\b",
        normalized, re.IGNORECASE,
    )
    if span:
        first, first_month, first_year, last, last_month, last_year = span.groups()
        try:
            start_month = _MONTHS[first_month.casefold()]
            end_month = _MONTHS[last_month.casefold()]
            end_year = int(last_year)
            inferred_start_year = end_year - int(start_month > end_month)
            if first_year is not None and int(first_year) != inferred_start_year:
                raise ValueError("BES start year conflicts with end year/month")
            date(inferred_start_year, start_month, int(first))
            period_end = date(end_year, end_month, int(last))
        except (KeyError, ValueError) as exc:
            raise BESContractError("BES cover date range invalid") from exc
        period_year = inferred_start_year
    elif same_month:
        first, last, month, year = same_month.groups()
        try:
            date(int(year), _MONTHS[month.casefold()], int(first))
            period_end = date(int(year), _MONTHS[month.casefold()], int(last))
        except (KeyError, ValueError) as exc:
            raise BESContractError("BES cover date range invalid") from exc
        period_year = int(year)
    leading_weeks = {int(value) for value in re.findall(
        r"\b(\d{1,2})\s+semana\s+epidemiol[oó]gica\b", normalized, re.IGNORECASE)}
    middle_weeks = {int(value) for value in re.findall(
        r"\bsemana\s+(\d{1,2})\s+epidemiol[oó]gica\b", normalized, re.IGNORECASE)}
    trailing_weeks = {int(value) for value in re.findall(
        r"\bsemana epidemiol[oó]gica\s*(\d{1,2})\b", normalized, re.IGNORECASE)}
    week_matches = leading_weeks or middle_weeks or trailing_weeks
    matches = set()
    for match in re.finditer(
        r"(?:semana epidemiol[oó]gica|semana)\s*(?:n[uú]mero\s*)?(\d{1,2})\b.{0,100}?\b(20\d{2})\b",
        normalized,
        re.IGNORECASE,
    ):
        matches.add((int(match[2]), int(match[1])))
    for match in re.finditer(
        r"\b(20\d{2})\b.{0,100}?(?:semana epidemiol[oó]gica|semana)\s*(?:n[uú]mero\s*)?(\d{1,2})\b",
        normalized,
        re.IGNORECASE,
    ):
        matches.add((int(match[1]), int(match[2])))
    matches = {
        (year, week)
        for year, week in matches
        if 2023 <= year <= 2100 and 1 <= week <= 53
    }
    if period_year is not None and len(week_matches) == 1:
        week = next(iter(week_matches))
        # A year-end week can end in January of the following calendar year.
        year = period_end.year if week <= 2 and period_end and period_end.year > period_year else period_year
        matches = {(year, week)}
    if len(matches) != 1 or (week_matches and next(iter(matches))[1] not in week_matches):
        raise BESContractError("BES cover has no unique epidemiological year/week")
    year, week = matches.pop()
    return BESCover(year, week, period_end)


def _cover_identity_text(pages: list[str]) -> str:
    """Use the cover for the period; supplement only publisher/title from page two."""
    cover = pages[0]
    if len(pages) < 2:
        return cover
    second_page = " ".join(pages[1].split())
    evidence = []
    for pattern in (
        r"Instituto Nacional(?:\s+\S+){0,8}\s+de Salud",
        r"Bolet[ií]n Epidemiol[oó]gico Semanal",
    ):
        match = re.search(pattern, second_page, re.IGNORECASE)
        if match:
            evidence.append(match.group(0))
    return "\n".join([cover, *evidence])


def inspect_pdf(payload: bytes) -> BESCover:
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise BESContractError("Complete INS bulletin PDF required")
    import pypdfium2 as pdfium

    from src.services.document_images import PDFIUM_LOCK

    try:
        with PDFIUM_LOCK, pdfium.PdfDocument(payload) as pdf:
            excerpts = []
            for index in range(min(2, len(pdf))):
                page = pdf[index]
                textpage = page.get_textpage()
                try:
                    excerpts.append(textpage.get_text_range())
                finally:
                    textpage.close()
                    page.close()
        return parse_cover_identity(_cover_identity_text(excerpts))
    except Exception:  # noqa: BLE001 - pdfplumber handles PDFium extraction failures
        import pdfplumber
        from pdfplumber.utils.exceptions import PdfminerException

        try:
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                if not pdf.pages:
                    raise BESContractError("INS bulletin PDF has no pages")
                pages = [page.extract_text() or "" for page in pdf.pages[:2]]
            return parse_cover_identity(_cover_identity_text(pages))
        except (PdfminerException, BESContractError) as exc:
            raise BESContractError("INS bulletin cover identity unreadable") from exc


def _source_slug(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def _source_count(value: str | None) -> int | None:
    if value is not None and not isinstance(value, str):
        return None
    text = (value or "").strip()
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:\.\d{3})+|\d{1,3}(?:,\d{3})+)", text):
        return None
    return int(text.replace(".", "").replace(",", ""))


def normalize_annex_cells(cells: list[dict], report: BESReport,
                          cover: BESCover) -> tuple[list[dict], dict]:
    """Normalize verified source-year cumulative annex cells from either extractor."""
    if cover.period_end is None or report.listed_year not in {None, cover.year}:
        raise BESContractError("BES report has no verified source year and period end")
    if _official_pdf(report.pdf_url) != report.pdf_url or not cells:
        raise BESContractError("BES annex cells require an official PDF and nonempty input")
    group_positions = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise BESContractError("BES annex cell is not an object")
        required = {"pdf_sha256", "event_label_raw", "geography_label_raw",
                    "accumulated_header_raw", "accumulated_value_raw",
                    "page_1_based", "event_group_position_1_based"}
        if not required.issubset(cell):
            raise BESContractError("BES annex cell lacks required source fields")
        if not isinstance(cell["event_label_raw"], str) or not isinstance(
            cell["geography_label_raw"], str
        ):
            raise BESContractError("BES annex source labels must be text")
        key = (cell["page_1_based"], " ".join(cell["event_label_raw"].split()))
        group_positions.setdefault(key, set()).add(cell["event_group_position_1_based"])
    rows = []
    gaps = []
    identities = set()
    digests = set()
    event_labels = set()
    pages = set()
    for cell in cells:
        year = cell.get("source_epi_year", cover.year)
        week = cell.get("source_epi_week", cover.week)
        period_end = cell.get("source_period_end", cover.period_end.isoformat())
        if (year != cover.year or week != cover.week
                or period_end != cover.period_end.isoformat()):
            raise BESContractError("BES annex cell period differs from PDF cover")
        page = cell["page_1_based"]
        group = cell["event_group_position_1_based"]
        if not isinstance(page, int) or page < 1 or not isinstance(group, int) or group < 1:
            raise BESContractError("BES annex cell has invalid page or group position")
        header = " ".join(str(cell["accumulated_header_raw"]).split())
        if not re.fullmatch(rf"Acumulado\s*{cover.year}", header, re.IGNORECASE):
            raise BESContractError("BES annex cell is not source-year cumulative")
        digest = cell["pdf_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BESContractError("BES annex cell lacks PDF SHA-256")
        digests.add(digest)
        event_name = " ".join(str(cell["event_label_raw"]).split())
        geography = " ".join(str(cell["geography_label_raw"]).split())
        if not event_name or not geography:
            raise BESContractError("BES annex cell lacks source event or geography")
        event_labels.add(event_name)
        pages.add(page)
        ambiguous = len(group_positions[(page, event_name)]) > 1
        event_code = "bes_" + _source_slug(event_name)
        if ambiguous:
            event_code += f"__annex_group_{group}"
        case_definition = " ".join(str(cell.get("case_definition_raw") or "").split())
        definition = (f"{SOURCE_DEFINITION_VERSION}:"
                      f"{hashlib.sha256(case_definition.encode()).hexdigest()[:12]}")
        national = geography.casefold() in {"colombia", "total nacional"}
        geo_slug = _source_slug(geography)
        geography_key = ("country:CO:national" if national else
                         f"source:{ONTOLOGY_SOURCE_ID}:territory:{geo_slug}")
        identity = (event_code, geography_key)
        if identity in identities:
            raise BESContractError("Duplicate BES event/territory/week cumulative value")
        identities.add(identity)
        raw_count = cell.get("accumulated_value_raw")
        count = _source_count(raw_count)
        source_missing = raw_count is None or not str(raw_count).strip()
        source_unreadable = count is None and not source_missing
        if source_unreadable:
            gaps.append({"page": page, "event": event_name, "geography": geography,
                         "raw_value": raw_count, "reason": "cumulative_count_unreadable",
                         "source_cell_retained": True})
        dimensions = {"source_epi_year": cover.year, "source_epi_week": cover.week,
                      "source_period_type": "year_to_date_at_epi_week"}
        rows.append({
            "Date": cover.period_end.isoformat(), "Year": str(cover.year),
            "Month": str(cover.period_end.month), "DiseaseCode": "__source_native__",
            "SourceDiseaseCode": event_code, "RawDiseaseLabel": event_name,
            "Cases": "" if source_missing or source_unreadable else str(count),
            "MissingValueStatus": ("source_unreadable" if source_unreadable else
                                   "source_missing" if source_missing else ""),
            "Measure": "source_reported_ytd_event_count", "Unit": "count",
            "Frequency": "weekly", "PeriodType": "source_epi_year_to_date_at_week",
            "ReportingBasis": "bes_department_dashboard", "DefinitionVersion": definition,
            "Geography": geography, "GeographyKey": geography_key,
            "Geocode": "CO" if national else geo_slug,
            "ReportingArea": geography, "ParentCountryCode": "CO",
            "LocationType": "national" if national else "source_subdivision",
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": report.pdf_url,
            "SourceLandingPageURL": ARCHIVE_URL, "RawSHA256": digest,
            "ParserVersion": PARSER_VERSION,
            "ExtractionMethod": cell.get("extraction_method", "deterministic_pdf_table"),
            "SourceTablePage": page,
            "SourceTableColumn": cell.get("column_1_based", ""),
            "SourceCaseDefinition": case_definition,
            "SourceAccumulatedValueRaw": raw_count if raw_count is not None else "",
            "SourceGeographyLabel": geography,
            "SourceLabelAmbiguous": ambiguous,
            "SourceAnnexGroupPosition": group,
            "SourcePeriodEnd": cover.period_end.isoformat(),
            "SourceDateRole": "report_period_end_not_weekly_incidence",
            "SourceRecordID": f"{cover.year}-{cover.week:02d}:"
                              f"{hashlib.sha256((event_code + geography_key).encode()).hexdigest()[:20]}",
            "DatasetStatus": "published_revisable", "IsProvisional": "true",
            "AuthoritativeRevision": "true", "PublicReleaseEnabled": "false",
            "License": "reuse_terms_unverified",
            "Dimensions": json.dumps(dimensions, ensure_ascii=False, sort_keys=True),
        })
    if len(digests) != 1:
        raise BESContractError("BES annex cells mix PDF originals")
    return rows, {"year": cover.year, "week": cover.week,
                  "period_end": cover.period_end.isoformat(), "source_url": report.pdf_url,
                  "sha256": next(iter(digests)), "table_pages": sorted(pages),
                  "source_events": sorted(event_labels), "parsed_rows": len(rows),
                  "gaps": gaps, "unparsed_groups": 0,
                  "unparsed_cells": len(gaps),
                  "explicit_missing_cells": sum(row["MissingValueStatus"] == "source_missing" for row in rows),
                  "source_unreadable_cells": sum(row["MissingValueStatus"] == "source_unreadable"
                                                 for row in rows),
                  "period_type": "source_epi_year_to_date_at_week"}


def parse_observations(payload: bytes, report: BESReport) -> tuple[list[dict], dict]:
    """Read only the source-year cumulative column of the territorial dashboards."""
    cover = inspect_pdf(payload)
    if cover.period_end is None:
        raise NotYetVerifiedContract("BES cover lacks a dated reporting period")
    if report.listed_year is not None and report.listed_year != cover.year:
        raise BESContractError("BES cover year differs from official archive year")
    import pdfplumber

    digest = hashlib.sha256(payload).hexdigest()
    source_cells = []
    gaps = []
    table_pages = []
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        for page_index in range(max(0, len(pdf.pages) - 10), len(pdf.pages)):
            page = pdf.pages[page_index]
            for table in page.find_tables():
                if len(table.rows) < 35:
                    continue
                cells = table.extract()
                if len(cells) < 4 or not cells[2] or "Departamento" not in str(cells[2][0]):
                    continue
                table_pages.append(page_index + 1)
                headings = [(i, " ".join(value.split())) for i, value in enumerate(cells[0])
                            if i and value and value.strip()]
                if not headings:
                    raise BESContractError("BES territorial dashboard has no event headings")
                for group_index, (start, event_name) in enumerate(headings):
                    stop = headings[group_index + 1][0] if group_index + 1 < len(headings) else len(cells[0])
                    columns = []
                    for i in range(start, stop):
                        if i >= len(cells[2]):
                            continue
                        heading = " ".join((cells[2][i] or "").split())
                        if re.fullmatch(rf"Acumulado\s*{cover.year}", heading, re.IGNORECASE):
                            columns.append(i)
                        elif i + 1 < stop and i + 1 < len(cells[2]):
                            following = " ".join((cells[2][i + 1] or "").split())
                            if re.fullmatch(rf"Acumulado{cover.year}Esperado",
                                            re.sub(r"\s+", "", heading + following), re.IGNORECASE):
                                columns.append(i)
                    if len(columns) != 1:
                        gaps.append({"page": page_index + 1, "event": event_name,
                                     "reason": "source_year_cumulative_column_missing_or_ambiguous"})
                        continue
                    column = columns[0]
                    case_definition = " ".join((cells[1][start] or "").split())
                    for source_row in cells[3:]:
                        geography = " ".join((source_row[0] or "").split())
                        if not geography:
                            continue
                        raw_count = source_row[column] if column < len(source_row) else None
                        source_cells.append({
                            "pdf_sha256": digest,
                            "source_epi_year": cover.year,
                            "source_epi_week": cover.week,
                            "source_period_end": cover.period_end.isoformat(),
                            "page_1_based": page_index + 1,
                            "event_group_position_1_based": group_index + 1,
                            "event_label_raw": event_name,
                            "case_definition_raw": case_definition,
                            "geography_label_raw": geography,
                            "accumulated_header_raw": f"Acumulado {cover.year}",
                            "accumulated_value_raw": raw_count,
                            "column_1_based": column + 1,
                            "extraction_method": "deterministic_pdf_table",
                        })
    if not table_pages or not source_cells:
        raise NotYetVerifiedContract("BES territorial dashboard cumulative tables not extracted")
    rows, audit = normalize_annex_cells(source_cells, report, cover)
    audit["gaps"] = gaps + audit["gaps"]
    audit["unparsed_groups"] = len(gaps)
    audit["table_pages"] = sorted(set(table_pages))
    return rows, audit
