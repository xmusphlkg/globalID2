"""Costa Rica Ministry originals and reviewed national notification tables.

Annual counts and publication-date cumulative snapshots have distinct source
identities. The source epidemiological week is never converted to an ISO week.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo
import hashlib
import io
import json
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from lxml import html
import pdfplumber

from .base import BaseCrawler

DEFAULT_SCOPE = "cr_moh_notifications"
DEFAULT_SOURCE_NAME = "Costa Rica Ministry of Health Notification Reports"
ONTOLOGY_SOURCE_ID = "SRC_CR_MOH_NOTIFICATIONS"
HISTORY_START_YEAR = 2014
HOST = "https://www.ministeriodesalud.go.cr"
LIBRARY = HOST + "/index.php/biblioteca-de-archivos-left/documentos-ministerio-de-salud/material-informativo/material-publicado/"
NOTIFICATIONS_URL = LIBRARY + "estadisticas-y-bases-de-datos/estadisticas-y-bases-de-datos-vigilancia-de-la-salud/estadistica-de-eventos-de-notificacion-obligatoria"
INDIVIDUAL_URL = NOTIFICATIONS_URL + "/notificacion-individual-vigilancia-de-la-salud"
BULLETINS_URL = LIBRARY + "boletines/boletines-vigilancia-vs-enfermedades-de-transmision-vectorial"
MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
          "noviembre": 11, "diciembre": 12}


class CostaRicaContractError(ValueError):
    pass


def plain(value):
    return " ".join(unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().casefold().split())


def official_url(base, link):
    result = urljoin(base, link)
    parsed = urlsplit(result)
    if parsed.scheme != "https" or parsed.netloc != "www.ministeriodesalud.go.cr" or parsed.username or parsed.password:
        raise CostaRicaContractError("Notification archive link leaves official Ministry host")
    return result


def discover(payload, url, *, start_year=HISTORY_START_YEAR, end_year=9999):
    """Return linked year directories, pagination and eligible PDF originals."""
    root = html.fromstring(payload)
    directories, pages, reports = {}, set(), {}
    for anchor in root.xpath("//a[@href]"):
        href = anchor.get("href")
        title = " ".join(anchor.text_content().split())
        if not href:
            continue
        absolute = urljoin(url, href)
        if urlsplit(absolute).netloc != "www.ministeriodesalud.go.cr":
            continue
        path = urlsplit(absolute).path
        year_dir = re.search(r"/boletines-epidemiologicos-(20\d{2})$", path)
        if year_dir and int(start_year) <= int(year_dir[1]) <= int(end_year):
            directories[absolute] = int(year_dir[1])
        if path == urlsplit(url).path and "limitstart=" in urlsplit(absolute).query:
            pages.add(official_url(url, href))
        if not path.endswith("/file"):
            continue
        if "notificacion-individual" in path and "pdf" in plain(title):
            year_match = re.search(r"\b(20\d{2})\b", title)
            kind = "annual_individual"
        elif "/boletines-epidemiologicos-" in path and "pdf" in plain(title):
            year_match = re.search(r"/boletines-epidemiologicos-(20\d{2})/", path)
            kind = "bulletin"
        else:
            continue
        if year_match and int(start_year) <= int(year_match[1]) <= int(end_year):
            target = official_url(url, href)
            reports[target] = {"url": target, "title": title, "year": int(year_match[1]), "kind": kind, "listing_url": url}
    return directories, sorted(pages), list(reports.values())


def _row(label, count, *, when, year, period_type, report, page, source_code=None, measure="case_notifications", **extra):
    code = source_code or "annual_" + hashlib.sha256(label.encode()).hexdigest()[:20]
    return {
        "Date": when.isoformat(), "Year": str(when.year), "Month": str(when.month),
        "DiseaseCode": "__source_native__", "SourceDiseaseCode": code, "RawDiseaseLabel": label,
        "Cases": str(count), "MissingValueStatus": "reported", "Frequency": period_type,
        "PeriodType": "annual" if period_type == "annual" else "cumulative",
        **({"PeriodStart": f"{year}-01-01", "PeriodEnd": f"{year}-12-31"} if period_type == "annual" else {}),
        "Measure": measure, "Unit": "count", "ReportingBasis": "national_individual_notifications" if period_type == "annual" else "national_cumulative_publication_snapshot",
        "DefinitionVersion": "cr_moh_individual_annual_v1" if period_type == "annual" else "cr_moh_cumulative_publication_snapshot_v1",
        "Geography": "Costa Rica", "GeographyKey": "country:CR:national",
        "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["url"], "SourcePage": page,
        "SourceReportYear": year, "DatasetStatus": "closed_revisable" if period_type == "annual" else "provisional",
        "IsProvisional": "false" if period_type == "annual" else "true",
        "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
        "PublicReleaseEnabled": "false", "AggregationPolicy": "non_additive", **extra,
    }


def parse_annual_text(text, report, page):
    """Only the explicit national Table 1. Rates never substitute for counts."""
    norm = plain(text)
    if "casos registrados de enfermedades de declaracion obligatoria" not in norm or not re.search(r"cuadro\s+n[°ºo.]?\s*1\b", norm):
        return [], []
    year = report["year"]
    if not re.search(rf"costa rica\s+{year}\b", norm) or "causas" not in norm or "tasa" not in norm:
        raise CostaRicaContractError("Annual national table year or count/rate headings disagree")
    started = False
    rows, rejected, labels = [], [], set()
    for line in text.splitlines():
        if re.fullmatch(r"N[º°o]?\s+Tasa", line.strip(), flags=re.I):
            started = True
            continue
        if not started:
            continue
        if plain(line).startswith(("nota:", "fuente:")):
            break
        match = re.fullmatch(r"(.+?)\s+(\d+)\s+(\d+(?:[,.]\d+)?)", line.strip())
        if not match:
            if line.strip():
                rejected.append({"page": page, "text": line, "reason": "ambiguous count/label/rate layout"})
            continue
        label, count, rate = match.groups()
        if label in labels:
            rejected.append({"page": page, "text": line, "reason": "ambiguous or duplicate source label"})
            continue
        labels.add(label)
        provisional = "*" in label
        category_label = " ".join(label.replace("*", "").split())
        rows.append(_row(label, int(count), when=date(year, 1, 1), year=year,
                         period_type="annual", report=report, page=page,
                         source_code="annual_" + hashlib.sha256(category_label.encode()).hexdigest()[:20],
                         SourceReportedRate=rate, IsProvisional=str(provisional).lower(),
                         Dimensions=json.dumps({"source_label": category_label, "source_report_kind": "individual_annual"}, ensure_ascii=False, sort_keys=True)))
    if len(rows) < 25:
        raise CostaRicaContractError("National annual Table 1 is truncated or schema changed")
    return rows, rejected


def publication_date(text, expected_year):
    match = re.search(r"\b(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")\s+(?:de\s+)?(20\d{2})\b", plain(text))
    if not match or int(match[3]) != expected_year:
        raise CostaRicaContractError("Bulletin has no unambiguous publication date in expected year")
    return date(int(match[3]), MONTHS[match[2]], int(match[1]))


def parse_snapshot_text(text, report, page, published):
    norm = plain(text)
    rows = []
    # Match the national table caption, not a narrative or top-cantons table.
    dengue = re.search(r"costa rica:\s*casos y tasas de dengue por region a la semana epidemiologica\s*(\d{1,2}),\s*(20\d{2})", norm)
    chik = re.search(r"costa rica:\s*casos de chikungunya por region notificados y confirmados a la\s*semana epidemiologica\s*(\d{1,2}),\s*(20\d{2})", norm)
    for match, kind in ((dengue, "dengue"), (chik, "chikungunya")):
        if not match:
            continue
        week, year = int(match[1]), int(match[2])
        if year != report["year"] or not 1 <= week <= 53:
            raise CostaRicaContractError("Cumulative table report year/week disagrees")
        section = norm[match.end():].split("fuente:", 1)[0]
        total = re.search(r"\btotal\s+(\d+)\s+(\d+(?:[,.]\d+)?)\b", section)
        if not total:
            raise CostaRicaContractError("National cumulative table has no reviewed Total row")
        count = int(total[1])
        region_names = r"central norte|pacifico central|chorotega|huetar caribe|central sur|brunca|huetar norte|central occidente|central este"
        regions = re.findall(r"\b(" + region_names + r")\s+(\d+)\s+(\d+(?:[,.]\d+)?)\b", section[:total.start()])
        if len(regions) != 9 or len({r[0] for r in regions}) != 9 or sum(int(r[1]) for r in regions) != count:
            raise CostaRicaContractError("National cumulative table does not reconcile with nine health regions")
        extras = {"SourcePublishedAt": published.isoformat(), "SourceReportedEpidemiologicalWeek": week,
                  "Dimensions": json.dumps({"source_epi_week": week, "source_report_year": year, "snapshot_date_basis": "explicit_source_publication_date"}, sort_keys=True)}
        rows.append(_row(kind.title(), count, when=published, year=year, period_type="daily", report=report, page=page,
                         source_code=kind + "_notified_cumulative_snapshot", measure="cumulative_case_notifications", **extras))
        if kind == "chikungunya":
            if not total[2].isdigit() or any(not r[2].isdigit() for r in regions) or sum(int(r[2]) for r in regions) != int(total[2]):
                raise CostaRicaContractError("Chikungunya confirmed PCR count is not an integer")
            rows.append(_row("Chikungunya confirmed PCR", int(total[2]), when=published, year=year, period_type="daily", report=report, page=page,
                             source_code="chikungunya_confirmed_pcr_cumulative_snapshot", measure="cumulative_confirmed_case_notifications", **extras))
    return rows


def parse_report_pdf(payload, report, *, as_of=None):
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise CostaRicaContractError("Incomplete Ministry PDF")
    as_of = as_of or datetime.now(ZoneInfo("America/Costa_Rica")).date()
    if report["kind"] == "annual_individual" and report["year"] >= as_of.year:
        raise CostaRicaContractError("Annual notification report must cover a closed calendar year")
    rows, rejected = [], []
    if report["kind"] == "bulletin":
        import pypdfium2 as pdfium
        from src.services.situation_notices.documents import _PDFIUM_LOCK
        texts = []
        # PDFium's native text extraction avoids repeatedly decoding thousands
        # of chart objects across this archive. Share the application lock;
        # PDFium is not safe to call concurrently in separate worker threads.
        with _PDFIUM_LOCK:
            document = pdfium.PdfDocument(payload)
            try:
                total_pages = len(document)
                pages = min(10, total_pages)
                for index in range(pages):
                    page = document[index]
                    textpage = page.get_textpage()
                    try:
                        texts.append(textpage.get_text_range())
                    finally:
                        textpage.close()
                        page.close()
            finally:
                document.close()
        if not texts or "boletin epidemiologico" not in plain(texts[0]) or "ministerio de salud" not in plain(texts[0]):
            raise CostaRicaContractError("PDF is not a reviewed Ministry epidemiological bulletin")
        published = publication_date(texts[0], report["year"])
        if published > as_of:
            raise CostaRicaContractError("Bulletin publication date is after captured source date")
        for index, text in enumerate(texts):
            rows.extend(parse_snapshot_text(text, report, index + 1, published))
        return rows, {"url": report["url"], "status": "parsed_partial" if rows else "unsupported_layout",
                      "observations": len(rows), "total_pages": total_pages, "reviewed_opening_pages": pages,
                      "rejected_cells": [], "publication_date": published.isoformat(),
                      "uncovered": "other diseases, remaining tables, graphs, municipality/age/sex breakdowns"}
    with pdfplumber.open(io.BytesIO(payload)) as document:
        # Reviewed tables are in the opening pages. Other tables are explicitly
        # outside this parser, rather than guessed from graphs or prose.
        pages = min(15, len(document.pages))
        published = None
        for index in range(pages):
            text = document.pages[index].extract_text() or ""
            found, problems = parse_annual_text(text, report, index + 1)
            rows.extend(found)
            rejected.extend(problems)
            if found:
                break
        total_pages = len(document.pages)
    return rows, {"url": report["url"], "status": "parsed_partial" if rows else "unsupported_layout",
                  "observations": len(rows), "total_pages": total_pages, "reviewed_opening_pages": pages,
                  "rejected_cells": rejected, "publication_date": published.isoformat() if published else None,
                  "uncovered": "remaining disease tables, graphs, municipality/age/sex breakdowns and historical collective workbooks"}


class CostaRicaNotificationsCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=60, max_retries=2, delay=0.15)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged official archive collection")

    def parse(self, response):
        raise NotImplementedError("Offline organization requires captured archive plan")
