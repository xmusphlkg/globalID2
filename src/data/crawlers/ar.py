"""Argentina BEN archive and conservative source-native ENO table contract.

Reports contain foreign-country outbreaks, laboratory studies and seasonal
summaries as well as ENO tables. Archive every discovered report, but normalize
only explicitly identified national ENO cumulative columns. Other content stays
in the original report and the coverage audit, never becomes invented cases.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "ar_ben_weekly"
DEFAULT_SOURCE_NAME = "Argentina National Epidemiological Bulletin (BEN)"
ONTOLOGY_SOURCE_ID = "SRC_AR_BEN"
LANDING_URL = "https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional"
LEGACY_URL = "https://www.argentina.gob.ar/salud/epidemiologia/boletines"
HISTORY_START_YEAR = 2009
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARCHIVE = ROOT / "data/current/ar/archive"


class ArgentinaBENContractError(ValueError):
    pass



def _replace_cache(path, payload):
    """Detach cache revisions from hard-linked, immutable captured originals."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ar-ben-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def clean(value):
    return " ".join(unescape(str(value or "")).split())


def fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", clean(value).casefold()) if not unicodedata.combining(c))


def official_url(href, base=LANDING_URL):
    # Several published links contain the CMS's literal blank:# prefix.
    href = unescape(href).removeprefix("blank:#")
    url = urljoin(base, href)
    host = urlparse(url).hostname or ""
    if host not in {"www.argentina.gob.ar", "argentina.gob.ar", "bancos.salud.gob.ar", "www.msal.gob.ar"}:
        return None
    return url.split("#", 1)[0]


def links_from_html(payload, base):
    html = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    out = []
    for href, title in re.findall(r'<a\b[^>]*href=["\x27]([^"\x27]+)["\x27][^>]*>(.*?)</a>', html, flags=re.S | re.I):
        url = official_url(href, base)
        if url:
            out.append({"url": url, "label": clean(re.sub(r"<[^>]*>", " ", title)), "original_href": href, "index_url": base})
    return out


def epidemiological_week_end(year, week):
    if not 1 <= int(week) <= 53:
        raise ArgentinaBENContractError("Invalid source epidemiological week")
    jan4 = date(int(year), 1, 4)
    first_sunday = jan4 - timedelta(days=(jan4.weekday() + 1) % 7)
    return first_sunday + timedelta(weeks=int(week) - 1, days=6)


def source_report_identity(text, *, reference=None):
    # Header/citation with report number + SE + year takes precedence over
    # uploader directories, link labels and stale cover artwork.
    match = re.search(r"Bolet[ií]n\s+Epidemiol[oó]gico\s+Nacional\s+N[°ºo.]?\s*(\d+)\s*[,.;]?\s*SE\s*(\d+)\s*[,.;]?\s*A[ñn]o\s*(20\d{2})", text, re.I)
    if not match:
        compact = re.sub(r"\s+", "", text)
        match = re.search(r"Bolet[ií]nEpidemiol[oó]gicoNacionalN[°ºo.]?(\d+)[,.;]?SE(\d+)[,.;]?A[ñn]o(20\d{2})", compact, re.I)
    if match:
        number, week, year = map(int, match.groups())
        return {"report_number": number, "report_week": week, "report_year": year}
    # Older PDFs omit a machine-readable cover. The archive's explicit year
    # and week/issue in its label are retained as catalogue identity only.
    label = clean((reference or {}).get("label"))
    number = re.search(r"(?:Nro|N[°º]|BEN)\s*(\d{3,4})", label, re.I)
    week = re.search(r"(?:Semana|\bSE)\s*(\d{1,2})", label, re.I)
    year = re.search(r"boletines[-/]?(20\d{2})", (reference or {}).get("index_url", ""))
    if number and week and year:
        return {"report_number": int(number.group(1)), "report_week": int(week.group(1)), "report_year": int(year.group(1)), "identity_basis": "official_catalogue"}
    raise ArgentinaBENContractError("No unambiguous BEN report year/week/number")


def parse_count(value):
    value = clean(value)
    if value in {"", "-", "—", "*", "s/d", "SD"}:
        return "", "source_missing"
    if not re.fullmatch(r"\d+|\d{1,3}(?:\.\d{3})+", value):
        raise ArgentinaBENContractError(f"Invalid integer count {value!r}")
    return str(int(value.replace(".", ""))), ""


def _row(label, value, *, year, week, report, url, digest, page, classification, table, group=""):
    value, missing = parse_count(value)
    ending = epidemiological_week_end(year, week)
    # Identity embeds case classification and cumulative grain, preventing
    # totals, confirmed cases and ordinary weekly cases from sharing a series.
    code = "eno_" + hashlib.sha256(f"{fold(label)}|{classification}|cumulative".encode()).hexdigest()[:24]
    dims = {"source_category": label, "classification": classification, "count_basis": "cumulative_SE1_to_source_week", "source_epi_year": year, "source_epi_week": week, "period_start": (epidemiological_week_end(year, 1) - timedelta(days=6)).isoformat(), "period_end": ending.isoformat(), "source_calendar": "Sunday-Saturday; week 1 contains January 4", "source_group": group}
    return {"Date": ending.isoformat(), "Year": str(ending.year), "Month": str(ending.month), "DiseaseCode": "__source_native__", "SourceDiseaseCode": code, "RawDiseaseLabel": label, "Cases": value, "MissingValueStatus": missing, "Measure": "cumulative_laboratory_tests" if classification == "laboratory_studied" else "cumulative_laboratory_positives" if classification == "laboratory_positive" else "cumulative_clinical_notifications" if classification == "clinical_grouped" else "cumulative_case_notifications", "ReportingBasis": "laboratory_surveillance" if classification.startswith("laboratory_") else "clinical_surveillance" if classification == "clinical_grouped" else "notification", "Frequency": "weekly", "PeriodType": "cumulative", "DefinitionVersion": f"ar_ben_eno_{'historical_' if table.startswith('Historical') else ''}{classification}_cumulative_v1", "Geography": "Argentina", "GeographyKey": "country:AR:national", "Source": DEFAULT_SOURCE_NAME, "SourceURL": url, "RawSHA256": digest, "SourcePage": str(page), "SourceTable": table, "SourceReportNumber": str(report["report_number"]), "DatasetStatus": "provisional", "IsProvisional": "true", "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true", "PublicReleaseEnabled": "false", "Dimensions": json.dumps(dims, ensure_ascii=False, sort_keys=True)}


def parse_eno_table(table, *, report, url, digest="", page=0, context="", issues=None):
    """Parse only reviewed ENO table families with explicit column identities."""
    rows = []
    header = None
    current_group = ""
    family="laboratory" if "agrupados laboratoriales" in fold(context) else "clinical" if "agrupados clinicos" in fold(context) else "nominal"
    expanded=[]
    for original in table:
        # Missing grid borders sometimes merge several complete source rows.
        # Split only when the label and every numeric column have matching
        # explicit line counts; never infer values from chart positions.
        if len(original)>3 and original[1] and original[3]:
            numeric_lines=str(original[3]).strip().splitlines()
            labels=str(original[1]).strip().splitlines()
            if len(numeric_lines)>1 and len(labels)==len(numeric_lines) and all(re.fullmatch(r"[\d.]+",clean(x)) for x in numeric_lines):
                n=len(numeric_lines)
                for index in range(n):
                    split=[]
                    for cell in original:
                        parts=str(cell or "").strip().splitlines()
                        split.append(parts[index] if len(parts)==n else cell if index==0 else "")
                    expanded.append(split)
                continue
        expanded.append(original)
    for cells in expanded:
        cells = [clean(x) for x in cells]
        f = [fold(x) for x in cells]
        if "evento" in f:
            label_i = f.index("evento")
            candidates = []
            for i, cell in enumerate(f):
                if "acumulado" in cell and "mediana" not in cell:
                    m = re.search(r"(20\d{2})\s*(?:se\s*)?(\d{1,2})?", cell)
                    if m:
                        period_end = re.search(r"\bse\s*1\s*[-–a]\s*(\d{1,2})", cell)
                        year = int(m.group(1));week = int(period_end.group(1) if period_end else m.group(2) or report["report_week"])
                        candidates.append((i, year, week, "clinical_grouped" if family=="clinical" else "confirmed"))
            if candidates:
                header = (label_i, candidates)
            else:
                header = None
            continue
        if not header:
            continue
        label_i, candidates = header
        if not cells[label_i] and any(x in {"notif.","conf.","confi."} for x in f):
            expanded_candidates=[]
            for index,year,week,classification in candidates:
                for offset in [0,1]:
                    subtype=f[index+offset] if index+offset<len(f) else ""
                    if subtype in {"notif.","conf.","confi."}:
                        kind=("laboratory_studied" if subtype=="notif." else "laboratory_positive") if family=="laboratory" else "notified" if subtype=="notif." else "confirmed"
                        expanded_candidates.append((index+offset,year,week,kind))
            if expanded_candidates:
                header=(label_i,expanded_candidates)
            continue
        if len(cells) <= label_i or not cells[label_i]:
            continue
        if label_i and cells[0]:
            current_group = cells[0]
        label = cells[label_i]
        for index, year, week, classification in candidates:
            if index >= len(cells):
                raise ArgentinaBENContractError("Truncated ENO row")
            try:
                rows.append(_row(label, cells[index], year=year, week=week, report=report, url=url, digest=digest, page=page, classification=classification, table="ENO selected events cumulative", group=current_group))
            except ArgentinaBENContractError as exc:
                if issues is None:
                    raise
                issues.append({"label":label,"raw_cells":cells,"error":str(exc)})
    return rows



def parse_historical_eno_grid(page, textpage, context, *, report, url, digest, page_number):
    """Read the published 4-year Notif./Confi. grid by PDF geometry.

    Older Office PDFs contain almost-horizontal line transforms and rotated
    text flags, so default table detectors lose columns. The actual PDF text
    bounding boxes preserve empty confirmed cells, unlike splitting text lines.
    """
    positions = []
    for match in re.finditer(r"Notif\.", context):
        box = textpage.get_charbox(match.start())
        positions.append((box[0], box[1]))
    if len(positions) < 2:
        return []
    positions.sort()
    header_y = positions[0][1]
    positions = [p for p in positions if abs(p[1] - header_y) < 3]
    n_years = len(positions)
    if n_years not in {2, 3, 4, 5}:
        return []
    conf_matches = []
    for match in re.finditer(r"Confi\.", context):
        box = textpage.get_charbox(match.start())
        if abs(box[1] - header_y) < 3:
            conf_matches.append(box[0])
    if len(conf_matches) != n_years:
        return []
    verticals = sorted({round(line["x0"], 3) for line in page.lines if abs(line["x0"]-line["x1"]) < .01 and line["y0"] <= header_y <= line["y1"]+8})
    first = max((x for x in verticals if x < positions[0][0]), default=None)
    if first is None:
        return []
    rights = [x for x in verticals if x >= first and x <= max(conf_matches) + 35]
    if len(rights) < 2*n_years+1:
        return []
    bounds = rights[:2*n_years+1]
    label_left = max((x for x in verticals if x < first - 10), default=first-130)
    year_positions=[]
    for match in re.finditer(r"\b20\d{2}\b", context):
        box=textpage.get_charbox(match.start())
        if bounds[0] <= box[0] <= bounds[-1] and header_y+5 < box[1] < header_y+30:
            year_positions.append((box[0],int(match.group())))
    years=[y for _,y in sorted(year_positions)]
    if len(years)!=n_years or years!=sorted(set(years)) or max(years)>report["report_year"]:
        return []
    week_match=re.search(r"(?:\ba\s*SE|\bSE)\s*(\d{1,2})",context[:250],re.I)
    if not week_match:
        return []
    week=int(week_match.group(1))
    lines=sorted({round(line["y0"],3) for line in page.lines if abs(line["y0"]-line["y1"])<.01 and line["x1"]-line["x0"]>20 and line["x0"]<=bounds[0]+2 and line["x1"]>=label_left+2 and line["y0"]<header_y+1},reverse=True)
    if len(lines)<3:
        return []
    rows=[]
    for top,bottom in zip(lines,lines[1:]):
        if top-bottom<4:
            continue
        values=[clean(textpage.get_text_bounded(left=l+.3,right=r-.3,top=top-.1,bottom=bottom+.1)) for l,r in zip(bounds,bounds[1:])]
        if not any(values) or any(v and not re.fullmatch(r"\d+|\d{1,3}(?:\.\d{3})+",v) for v in values):
            continue
        label=clean(textpage.get_text_bounded(left=label_left+.3,right=bounds[0]-.3,top=top-.1,bottom=bottom+.1))
        if not label or fold(label) in {"evento","grupo"}:
            continue
        for index,value in enumerate(values):
            rows.append(_row(label,value,year=years[index//2],week=week,report=report,url=url,digest=digest,page=page_number,classification="notified" if index%2==0 else "confirmed",table="Historical national ENO Notif./Confi. cumulative grid"))
    return rows


def parse_ben_payload(payload, *, source_url, reference=None):
    import pypdfium2 as pdfium
    import pdfplumber
    if payload[:4] != b"%PDF":
        raise ArgentinaBENContractError("BEN artifact is not a PDF")
    digest = hashlib.sha256(payload).hexdigest()
    texts = []
    with pdfium.PdfDocument(payload) as document:
        for page in document:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close();page.close()
    report = source_report_identity("\n".join(texts[:4]), reference=reference)
    candidates = [i for i, text in enumerate(texts) if ("evento" in fold(text) and "grupo" in fold(text) and (("acumulado" in fold(text) and "mediana" in fold(text)) or ("umbral" in fold(text) and "Notif." in text and "Confi." in text))) or ("argentina" in fold(text) and re.search(r"tabla\s*\d+[.:]\s*tabla de (?:eventos nominales|agrupados clinicos|eventos agrupados laboratoriales)",fold(text)))]
    rows = []
    tables_audit = []
    if candidates:
        with pdfplumber.open(io.BytesIO(payload)) as document, pdfium.PdfDocument(payload) as geometry_document:
            for i in candidates:
                page_rows = []
                issues=[]
                pdfpage=document.pages[i]
                # Office exports introduce tiny matrix rotations that make
                # line orientation and upright flags disagree with rendering.
                for line in pdfpage.objects.get("line",[]):
                    for key in ("x0","x1","y0","y1","top","bottom","width","height"):
                        line[key]=round(line[key],3)
                for character in pdfpage.chars:
                    character["upright"]=True
                for table in pdfpage.extract_tables():
                    page_rows.extend(parse_eno_table(table, report=report, url=source_url, digest=digest, page=i + 1, context=texts[i], issues=issues))
                if not page_rows and "Notif." in texts[i] and "Confi." in texts[i]:
                    geometry_page = geometry_document[i]
                    textpage = geometry_page.get_textpage()
                    page_rows.extend(parse_historical_eno_grid(document.pages[i], textpage, texts[i], report=report, url=source_url, digest=digest, page_number=i+1))
                    textpage.close();geometry_page.close()
                rows.extend(page_rows)
                tables_audit.append({"page": i + 1, "rows": len(page_rows), "rejected_rows":issues,"status": "partial" if issues else "parsed" if page_rows else "unsupported_table_layout"})
    seen = {}
    for row in rows:
        key = (row["Date"], row["SourceDiseaseCode"], row["GeographyKey"])
        if key in seen and seen[key]["Cases"] != row["Cases"]:
            raise ArgentinaBENContractError("Conflicting duplicate ENO observation within report")
        seen[key] = row
    return list(seen.values()), {**report, "source_url": source_url, "sha256": digest, "page_count": len(texts), "parsed_rows": len(seen), "eno_tables": tables_audit, "status": "parsed" if seen else "archived_no_supported_eno_table", "coverage_note": "Only validated national ENO cumulative table columns normalized; full report retained including other disease/region/laboratory/season tables"}


class ArgentinaBENCrawler(BaseCrawler):
    def __init__(self, *, archive_dir=None, cache_max_age_days=1, **kwargs):
        self.cache_max_age_days = float(cache_max_age_days)
        super().__init__(timeout=60, delay=0.15, max_retries=2)
        self.archive_dir = Path(archive_dir or DEFAULT_ARCHIVE)

    def discover(self, *, start_year=HISTORY_START_YEAR):
        queue = [LANDING_URL, LEGACY_URL]
        visited, references, indexes, failures = set(), {}, [], []
        while queue:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                response = self.get(url)
            except Exception as exc:
                failures.append({"url": url, "stage": "index", "error": str(exc)})
                continue
            indexes.append((response.url, response.content))
            for item in links_from_html(response.content, response.url):
                path = urlparse(item["url"]).path.lower()
                if path.endswith(".pdf") and re.search(r"(?:\bben[_ -]|bolet|biv[_ -]|integrado)", item["label"] + " " + path, re.I):
                    references.setdefault(item["url"], item)
                elif re.search(r"boletines[-/]?(20\d{2})", path) or "boletines-epidemiologicos" in path:
                    year = re.search(r"20\d{2}", path)
                    if not year or int(year.group()) >= int(start_year):
                        queue.append(item["url"])
        if not references:
            raise ArgentinaBENContractError("No official BEN PDF links discovered")
        return list(references.values()), indexes, failures

    def download(self, reference, *, force=False):
        url = reference["url"]
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        cache = self.archive_dir / (hashlib.sha256(url.encode()).hexdigest() + ".pdf")
        receipt = cache.with_suffix(".json")
        if cache.exists() and receipt.exists() and not force:
            payload = cache.read_bytes();meta = json.loads(receipt.read_text())
            try:
                retrieved = datetime.fromisoformat(meta.get("retrieved_at", ""))
                if retrieved.tzinfo is None:
                    retrieved = retrieved.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc)-retrieved).total_seconds()
            except (ValueError, TypeError):
                age = float("inf")
            if 0 <= age < self.cache_max_age_days*86400 and hashlib.sha256(payload).hexdigest() == meta.get("sha256") and payload[:4] == b"%PDF":
                return payload, {**meta, "cache_reused": True, "reference": reference}
        response = self.get(url)
        payload = response.content
        if payload[:4] != b"%PDF":
            raise ArgentinaBENContractError("Downloaded BEN URL does not contain PDF bytes")
        meta = {"source_url": url, "sha256": hashlib.sha256(payload).hexdigest(), "retrieved_at": datetime.now(timezone.utc).isoformat(), "byte_size": len(payload), "reference": reference, "archive_path": str(cache)}
        _replace_cache(cache, payload);_replace_cache(receipt, json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
        return payload, meta

    def fetch_rows(self, *, start_year=HISTORY_START_YEAR, force=False):
        refs, indexes, failures = self.discover(start_year=start_year)
        rows, audits = [], []
        for ref in refs:
            try:
                payload, meta = self.download(ref, force=force)
                parsed, audit = parse_ben_payload(payload, source_url=ref["url"], reference=ref)
                rows.extend(parsed);audits.append(audit)
            except Exception as exc:
                failures.append({"url": ref["url"], "stage": "download_or_parse", "error": str(exc)})
        self.last_audit = {"discovered_reports": len(refs), "reports": audits, "failures": failures}
        return deduplicate_revisions(rows)

    async def crawl(self, **kwargs):
        rows = await asyncio.to_thread(self.fetch_rows, **kwargs)
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL, raw_data={"rows": rows, "source_audit": self.last_audit})]

    def parse(self, response):
        rows, audit = parse_ben_payload(response.content, source_url=response.url)
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=response.url, raw_data={"rows": rows, "source_audit": audit})]


def deduplicate_revisions(rows):
    """Later BEN issues revise identical source periods without summing reports."""
    chosen = {}
    for row in rows:
        key = (row["SourceDiseaseCode"], row["Date"], row["GeographyKey"], row["DefinitionVersion"])
        if key not in chosen or int(row["SourceReportNumber"]) > int(chosen[key]["SourceReportNumber"]):
            chosen[key] = row
        elif int(row["SourceReportNumber"]) == int(chosen[key]["SourceReportNumber"]) and row["Cases"] != chosen[key]["Cases"]:
            raise ArgentinaBENContractError("Same BEN issue contains conflicting observations")
    return sorted(chosen.values(), key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
