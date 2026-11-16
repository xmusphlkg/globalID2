"""Indonesia's official annual Health Profile channel and national facts.

Discovery follows the live Ministry directory and its actual download links.
Source counts are admitted only from explicit national narratives or complete
national table cells with preserved headings. Unreadable fonts, overlapping
digits and provincial subtotals are not repaired into national observations.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
import re
from threading import Lock
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import date, datetime, timezone

import requests
from lxml import html
from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "id_kemkes_profile"
DEFAULT_SOURCE_NAME = "Indonesia Ministry of Health Annual Health Profile"
ONTOLOGY_SOURCE_ID = "SRC_ID_KEMKES_PROFILE"
HISTORY_START_YEAR = 2017
CHANNEL_URL = "https://kemkes.go.id/id/category-download/profil-kesehatan"
INDEX_URL = CHANNEL_URL
ROOT = Path(__file__).resolve().parents[3]
PARSER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_PDF_LOCK = Lock()


class IndonesiaContractError(ValueError):
    pass


def plain(value):
    return " ".join(str(value or "").split())


def discover_profiles(payload, *, start_year=HISTORY_START_YEAR, end_year=None):
    tree = html.fromstring(payload)
    found = {}
    for href in tree.xpath("//a/@href"):
        url = urljoin(CHANNEL_URL, href)
        match = re.fullmatch(r"https://(?:www\.)?kemkes\.go\.id/id/profil-kesehatan-indonesia-(\d{4})/?", url)
        if not match:
            continue
        year = int(match[1])
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        found[url] = {"year": year, "page_url": url, "title": f"Profil Kesehatan Indonesia {year}"}
    if not found:
        raise IndonesiaContractError("Official annual Health Profile directory contains no requested years")
    return sorted(found.values(), key=lambda value: (value["year"], value["page_url"]))


def discover_downloads(payload, *, reference):
    tree = html.fromstring(payload)
    titles = tree.xpath("//h1/text()")
    if not any(str(reference["year"]) in value and "profil kesehatan" in value.lower() for value in titles):
        raise IndonesiaContractError("Annual page title differs from captured directory")
    dates = tree.xpath("//time/@datetime")
    published = dates[0] if dates else None
    # The legacy article markup closes nested <p> tags irregularly. Extract
    # hrefs from captured bytes, with a strict source-host/link-type contract.
    links = re.findall(r"href\s*=\s*['\"]([^'\"]+)", payload.decode("utf-8"), flags=re.I)
    found = {}
    for href in links:
        url = urljoin(reference["page_url"], href)
        host = urlparse(url).hostname
        if host in {"kemkes.go.id", "www.kemkes.go.id"} and re.search(r"/app_asset/file_content_download/[^?]+\.pdf(?:\?.*)?$", url, re.I):
            kind = "direct_pdf"
        elif host == "drive.google.com" and re.search(r"/file/d/[A-Za-z0-9_-]+", url):
            kind = "linked_google_drive"
        elif host == "s.kemkes.go.id" and re.fullmatch(r"/[A-Za-z0-9_-]+/?", urlparse(url).path):
            kind = "official_shortlink"
        else:
            continue
        found[url] = {**reference, "url": url, "download_kind": kind, "published_date": published}
    return sorted(found.values(), key=lambda value: value["url"])


def pdf_text(payload):
    import pypdfium2
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise IndonesiaContractError("Health Profile artifact is not a complete PDF")
    with _PDF_LOCK, pypdfium2.PdfDocument(payload) as document:
        result = []
        for page in document:
            # Several official books retain off-page content from adjacent
            # appendices. Bound extraction to the physical page, otherwise
            # hidden neighbouring headings/counts appear in the wrong table.
            text = page.get_textpage(); result.append(text.get_text_bounded())
            text.close(); page.close()
    return result


def integer(value):
    value = plain(value)
    if re.fullmatch(r"\d+", value):
        return str(int(value))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        return str(int(value.replace(".", "")))
    raise IndonesiaContractError(f"Source count is not a verified Indonesian integer: {value!r}")


DISEASES = {
    "tuberkulosis": r"tuberkulosis|tbc",
    "hiv": r"hiv", "aids": r"aids", "pneumonia": r"pneumonia",
    "diare": r"diare", "kusta": r"kusta", "difteri": r"difteri",
    "pertusis": r"pertusis", "tetanus_neonatorum": r"tetanus\s+neonatorum|tn",
    "campak": r"campak", "rubella": r"rubella",
    "dbd": r"demam\s+berdarah\s+dengue|dbd", "malaria": r"malaria",
    "chikungunya": r"chikungunya", "filariasis": r"filariasis",
    "rabies": r"rabies", "leptospirosis": r"leptospirosis",
    "covid19": r"covid[- ]?19", "mpox": r"mpox|monkeypox",
    "legionellosis": r"legionellosis", "polio": r"polio",
    "hepatitis_b": r"hepatitis\s+b",
}
NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+)"
APPENDIX_NUMBER = r"\d+(?:\.\d+)*(?:[a-z]|\.[a-z])?"


def appendix_start(texts):
    # The contents pages also contain a standalone LAMPIRAN line. Only an
    # isolated physical divider or an actual numbered appendix table starts
    # the appendices; otherwise early books lose their entire disease chapter.
    for index, text in enumerate(texts):
        if re.fullmatch(r"\s*LAMPIRAN\s*", text):
            return index
    return next((i for i,t in enumerate(texts) if i >= len(texts)//2
        and re.search(r"(?:^|[\r\n])\s*lampiran\s+" + APPENDIX_NUMBER, t, re.I)
        and re.search(r"\(1\)", t)), len(texts))


def native_row(label, value, *, code, kind, year, reference, digest, page, evidence,
               definition="national_narrative", extra=None):
    cumulative = kind.startswith("cumulative")
    measure = {"cases": "case_notifications", "suspected_cases": "suspected_case_notifications",
        "confirmed_cases": "confirmed_case_notifications", "deaths": "deaths",
        "cumulative_cases": "cumulative_case_notifications", "cumulative_deaths": "cumulative_deaths",
        "registered_cases": "registered_cases", "laboratory_tests": "laboratory_tests",
        "newly_registered_cases": "newly_registered_cases",
        "treated_cases": "treated_patients", "vaccinated_cases": "vaccinated_case_patients",
        "transfers": "patient_transfers", "outbreaks": "outbreaks"}[kind]
    dimensions = {"source_profile_year": reference["year"], "source_statistic_year": year,
        "source_report_url": reference["url"], "source_report_sha256": digest,
        "source_page": page, "source_case_label": label, "count_kind": kind,
        "source_calendar": "calendar_year_explicit_source_title", "extraction": definition,
        "evidence": evidence, **(extra or {})}
    return {"Date": date(year, 12, 31).isoformat(), "Year": str(year), "Month": "12",
        "PeriodStart": "" if cumulative or kind == "registered_cases" else date(year, 1, 1).isoformat(),
        "PeriodEnd": date(year, 12, 31).isoformat(), "PeriodType": "cumulative" if cumulative else "point_in_time" if kind == "registered_cases" else "annual",
        "DiseaseCode": "__source_native__", "SourceDiseaseCode": f"{code}__{kind}",
        "RawDiseaseLabel": label, "Cases": value, "MissingValueStatus": "source_missing" if value == "" else "", "Measure": measure,
        "Unit": "count", "Frequency": "annual", "ReportingBasis": "national_health_profile",
        "DefinitionVersion": f"id_kemkes_profile_{definition}_{kind}_v1", "SourceReportYear": str(reference["year"]),
        "Geography": "Indonesia", "GeographyKey": "country:ID:national",
        "Source": DEFAULT_SOURCE_NAME, "SourceURL": reference["url"], "SourcePage": str(page),
        "SourceLandingPageURL": reference["page_url"], "SourcePublishedAt": reference.get("published_date") or "",
        "RawSHA256": digest, "SourceEvidence": evidence, "DatasetStatus": "published_revisable",
        "IsProvisional": "false", "AuthoritativeRevision": "false", "PublicReleaseEnabled": "false",
        "License": "source_terms_review_required", "Dimensions": json.dumps(dimensions, ensure_ascii=False, sort_keys=True)}


def parse_national_narratives(texts, *, reference, digest):
    """Explicit current-year national statements; never first local counts."""
    rows = []; rejected = []; year = int(reference["year"])
    for index, text in enumerate(texts[:appendix_start(texts)]):
        normalized = plain(text)
        # The main disease chapter is national; appendices are handled through
        # separately verified national rows. Country framing must be physical.
        if not re.search(r"indonesia", normalized, re.I):
            continue
        for code, label_pattern in DISEASES.items():
            if code in {"covid19", "mpox", "polio", "legionellosis"}:
                continue  # Annual versus outbreak/cumulative schemas need review.
            patterns = [
                rf"(?:Pada\s+)?tahun\s+(?P<year>{year})\s+(?:ditemukan\s+)?(?:jumlah\s+(?:semua\s+)?kasus\s+)(?P<label>{label_pattern})\s+(?:yang\s+ditemukan\s+)?(?:sebanyak|sebesar|adalah)\s+(?P<count>{NUMBER})\s+kasus\b",
                rf"Jumlah\s+kasus\s+(?P<label>{label_pattern})\s+(?:pada\s+)?tahun\s+(?P<year>{year})\s+(?:ditemukan\s+)?(?:sebanyak|sebesar|adalah)\s+(?P<count>{NUMBER})\s+kasus\b",
                rf"(?:Pada\s+)?tahun\s+(?P<year>{year})\s+(?:dilaporkan\s+(?:terdapat\s+|sebanyak\s+)?|ditemukan\s+(?:adanya\s+)?|terdapat\s+)(?P<count>{NUMBER})\s+kasus\s+(?:baru\s+)?(?P<label>{label_pattern})\b",
                rf"(?:Pada\s+)?tahun\s+(?P<year>{year})\s+(?:dilaporkan\s+(?:terdapat\s+)?|ditemukan\s+|terdapat\s+)(?:kasus\s+)(?P<label>{label_pattern})\s+(?:sebanyak|sebesar)\s+(?P<count>{NUMBER})\s+kasus\b",
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, normalized, re.I):
                    prefix = normalized[max(0, match.start()-150):match.start()].rsplit('. ',1)[-1]
                    if re.search(r"\bdi\s+(?:Provinsi|Kabupaten|Kota)\b", prefix, re.I) or re.match(r"\s+(?:di\s+)?(?:Provinsi|Kabupaten|Kota)\b", normalized[match.end():], re.I):
                        continue
                    rows.append(native_row(match["label"], integer(match["count"]), code=code, kind="cases",
                        year=year, reference=reference, digest=digest, page=index + 1, evidence=match[0]))
        # HIV/AIDS are explicitly paired national source counts, not a sum.
        paired = re.search(rf"Pada\s+tahun\s+{year}\s+dilaporkan\s+sebanyak\s+({NUMBER})\s+kasus\s+HIV\s+dan\s+({NUMBER})\s+kasus\s+AIDS", normalized, re.I)
        if paired:
            for code, count in [("hiv", paired[1]), ("aids", paired[2])]:
                rows.append(native_row(code.upper(), integer(count), code=code, kind="cases", year=year,
                    reference=reference, digest=digest, page=index + 1, evidence=paired[0]))
        for code, pattern in [("campak", rf"Pada\s+tahun\s+{year}\s+terdapat\s+kasus\s+suspek\s+(?P<label>campak)\s+sebanyak\s+(?P<count>{NUMBER})\s+kasus"),
            ("campak", rf"Pada\s+tahun\s+{year}[^.]{{0,260}}?kasus\s+(?P<label>campak)\s+konfirmasi\s+laboratorium\s+sebanyak\s+(?P<count>{NUMBER})\s+kasus")]:
            for match in re.finditer(pattern, normalized, re.I):
                kind = "suspected_cases" if "suspek" in match[0].lower() and "konfirmasi" not in match[0].lower() else "confirmed_cases"
                rows.append(native_row(match["label"], integer(match["count"]), code=code, kind=kind, year=year,
                    reference=reference, digest=digest, page=index + 1, evidence=match[0]))
        # DBD deaths are explicit in the same national annual sentence.
        match = re.search(rf"Pada\s+tahun\s+{year}\s+terdapat\s+({NUMBER})\s+kasus\s+DBD\s+dengan\s+jumlah\s+kematian\s+sebanyak\s+({NUMBER})\s+kasus", normalized, re.I)
        if match:
            rows.append(native_row("DBD", integer(match[2]), code="dbd", kind="deaths", year=year,
                reference=reference, digest=digest, page=index + 1, evidence=match[0]))
        # Nationwide chronic filariasis is a stock, not annual new incidence.
        match = re.search(rf"Di\s+Indonesia,?\s+pada\s+tahun\s+{year}\s+terdapat\s+({NUMBER})\s+kasus\s+kronis\s+(Filariasis)", normalized, re.I)
        if match:
            rows.append(native_row(match[2], integer(match[1]), code="filariasis", kind="registered_cases", year=year,
                reference=reference, digest=digest, page=index + 1, evidence=match[0]))
    return rows, rejected


def national_cells(table):
    """Strictly align an explicitly labelled Indonesia line within merged rows."""
    matches = []
    for row in table:
        for label_index, cell in enumerate(row[:2]):
            lines = [plain(line) for line in str(cell or "").splitlines() if plain(line)]
            indices = [i for i, line in enumerate(lines) if line.lower() == "indonesia"]
            for position in indices:
                values = []
                for index, value in enumerate(row):
                    if index == label_index:
                        values.append("Indonesia"); continue
                    if value is None or not str(value).strip():
                        values.append(None); continue
                    parts = [plain(line) for line in str(value).splitlines() if plain(line)]
                    if len(parts) != len(lines):
                        raise IndonesiaContractError("National table line overlaps or differs from source row labels")
                    values.append(parts[position])
                matches.append(values)
    if len(matches) != 1:
        raise IndonesiaContractError("No unique explicit national row in source table")
    return matches[0]


def _headers(table):
    boundary = next((i for i, row in enumerate(table) if any(re.fullmatch(r"\(1\)", plain(cell)) for cell in row)), None)
    if boundary is None or boundary < 1 or boundary > 5:
        raise IndonesiaContractError("No complete numbered source column contract")
    levels = table[:boundary]; result = [[] for _ in table[0]]
    for level, row in enumerate(levels):
        parent = ""
        ancestors = [tuple(parts) for parts in result]
        for col in range(2, len(result)):
            if level and col > 2 and ancestors[col] != ancestors[col - 1]:
                parent = ""
            value = plain(row[col])
            if value:
                parent = value
            # Only true parent levels propagate merged cells. The bottom
            # level's blank gender/rate cells never inherit a sibling label.
            inherited = parent if level < len(levels) - 1 else value
            if inherited and (not result[col] or result[col][-1] != inherited):
                result[col].append(inherited)
    return [" / ".join(parts) for parts in result], levels


def parse_national_tables(payload, texts, *, reference, digest):
    import pdfplumber
    rows = []; audit = []
    first_appendix = appendix_start(texts)
    candidates = [i for i, text in enumerate(texts) if i >= first_appendix and re.search(r"lampiran\s+\d", text, re.I)
        and any(re.search(pattern, text, re.I) for pattern in DISEASES.values())
        and re.search(r"(?:kasus|penderita|situasi|kesakitan)", text, re.I)]
    with pdfplumber.open(io.BytesIO(payload)) as document:
        for index in candidates:
            text = plain(texts[index]); current = {"page": index + 1, "rows": 0, "rejected": []}
            appendices = re.findall(r"lampiran\s+(" + APPENDIX_NUMBER + r")", text, re.I)
            # The PDF's text order can repeat nearby headings. The geometric
            # table's physical page and full source text remain provenance.
            if len(set(plain(a).lower() for a in appendices)) != 1:
                current["rejected"].append("Ambiguous multiple appendix captions on one source page"); audit.append(current); continue
            appendix = plain(appendices[0]).lower()
            page = document.pages[index]
            for table in page.crop(page.bbox).extract_tables():
                try:
                    values = national_cells(table); headers, levels = _headers(table)
                except IndonesiaContractError as exc:
                    current["rejected"].append(str(exc)); continue
                # National count tables only. District coverage, vaccination
                # coverage and treatment-rate tables need separate contracts.
                geometric_text = page.crop(page.bbox).extract_text() or ""
                caption_match = re.search(r"(?i:lampiran)\s+" + APPENDIX_NUMBER + r"\s+([A-Z0-9/(),.%<≥\s–-]{12,}?\bTAHUN\s+\d{4}(?:\s*[-–]\s*\d{4})?)", geometric_text)
                caption = plain(caption_match[1]) if caption_match else ""
                if not caption or not re.search(r"\b(?:KASUS|PENDERITA|KESAKITAN)\b|JUMLAH PENYAKIT YANG DAPAT DICEGAH DENGAN IMUNISASI", caption) or any(word in caption.lower() for word in ["kabupaten", "kab/kota", "selesai berobat", "release from treatment", "klb", "non polio", "non-polio", "covid", "faktor risiko", "pneumonia", "pengobatan lengkap", "idu"]) or re.match(r"PERSENTASE|CAKUPAN|FREKUENSI|ANGKA KESEMBUHAN", caption):
                    current["rejected"].append("Caption is not a supported national disease-count contract"); continue
                families = [code for code, pattern in DISEASES.items() if re.search(pattern, caption + " " + plain(str(levels[0])), re.I)]
                if not families:
                    current["rejected"].append("No source disease identity in appendix caption"); continue
                table_hash = hashlib.sha256(json.dumps(levels, ensure_ascii=False).encode()).hexdigest()[:12]
                for col, value in enumerate(values[2:], 2):
                    if value is None:
                        continue
                    header = headers[col]; lower = header.lower()
                    if re.search(r"\b(?:jan(?:uari)?|feb(?:ruari)?|mar(?:et)?|apr(?:il)?|mei|jun(?:i)?|jul(?:i)?|ags|agu(?:stus)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|des(?:ember)?)\b", lower):
                        continue  # Monthly columns never become annual cases.
                    if not header or re.search(r"%|persen|rate|incidence|prevalensi|per\s+\d|api\b|cnr\b|cfr\b|penduduk|perkiraan|sasaran|target|jumlah balita|risiko|resiko|pelayanan", lower):
                        continue
                    if any(x in lower for x in ["tahun sebelumnya", "ghpr", "var", "lyssa", "id u", "idu"]):
                        continue
                    selected = [code for code in families if re.search(DISEASES[code], header, re.I)]
                    if len(families) == 1:
                        code = families[0]
                    elif len(selected) == 1:
                        code = selected[0]
                    else:
                        current["rejected"].append(f"Column {col + 1} disease group ambiguous"); continue
                    if lower.rsplit(' / ', 1)[-1] in {"k", "m"}:
                        current["rejected"].append(f"Column {col + 1} source abbreviation needs independent measure contract"); continue
                    kind = "cumulative_deaths" if "kumulatif" in lower and re.search(r"meninggal|kematian", lower) else "deaths" if re.search(r"meninggal|kematian", lower) else "transfers" if "pindah" in lower else "vaccinated_cases" if "divaksinasi" in lower else "treated_cases" if re.search(r"pengobatan|dilayani|dilayan", lower + " " + caption.lower()) else "cumulative_cases" if "kumulatif" in lower else "newly_registered_cases" if "kronis baru" in lower else "registered_cases" if re.search(r"terdaftar|kronis", caption + " " + lower, re.I) else "confirmed_cases" if re.search(r"positif|konfirmasi laboratorium", lower) else "laboratory_tests" if re.search(r"mikroskopis|rdt|tes hiv|diperiksa", lower) else "suspected_cases" if "suspek" in header.lower() + " " + caption.lower() else "cases"
                    if code == 'malaria' and 'konfirmasi laboratorium' in lower:
                        kind = 'laboratory_tests'
                    elif 'kumulatif' in caption.lower():
                        kind = 'cumulative_deaths' if kind == 'deaths' else 'cumulative_cases'
                    elif kind == 'cases' and re.search(r'KONFIRMASI LABORATORIUM|TERKONFIRMASI BAKTERIOLOGIS', caption):
                        kind = 'confirmed_cases'
                    elif kind == 'cases' and 'terduga' in lower:
                        kind = 'suspected_cases'
                    years = re.findall(r"\b(20\d{2})\b", header)
                    if len(set(years)) > 1:
                        current["rejected"].append(f"Column {col + 1} has ambiguous reporting years"); continue
                    year = int(years[0]) if years else int(reference["year"])
                    if not years and not re.search(r"TAHUN\s+" + str(year) + r"\b", caption):
                        current["rejected"].append(f"Column {col + 1} annual period not explicit"); continue
                    try:
                        count = "" if plain(value).lower() in {"-", "...", "n/a"} else integer(value)
                    except IndonesiaContractError as exc:
                        current["rejected"].append(f"Column {col + 1}: {exc}"); continue
                    identity = f"{code}__appendix_{appendix.replace(' ', '')}_{table_hash}__column_{col + 1}"
                    rows.append(native_row(code.upper() if code in {"hiv", "aids", "dbd"} else code.replace("_", " "), count,
                        code=identity, kind=kind, year=year, reference=reference, digest=digest, page=index + 1,
                        evidence=json.dumps(values, ensure_ascii=False), definition="national_appendix_table",
                        extra={"source_appendix": appendix, "source_caption": caption, "source_column": col + 1,
                            "source_column_header": header, "source_column_contract": levels, "source_national_row": values}))
                    current["rows"] += 1
            audit.append(current)
    return rows, audit


def parse_profile_pdf(payload, *, reference):
    texts = pdf_text(payload); year = int(reference["year"])
    cover = plain(" ".join(texts[:8]))
    if not re.search(r"profil\s+kesehatan\s+indonesia\s+(?:tahun\s+)?" + str(year) + r"\b", cover, re.I):
        raise IndonesiaContractError("Captured PDF cover does not independently verify profile year")
    digest = hashlib.sha256(payload).hexdigest()
    narratives, issues = parse_national_narratives(texts, reference=reference, digest=digest)
    tables, audit = parse_national_tables(payload, texts, reference=reference, digest=digest)
    unique = {}; conflicts = {}
    for row in narratives + tables:
        key = (row["Date"], row["SourceDiseaseCode"], row["DefinitionVersion"])
        if key in conflicts:
            conflicts[key].append(row)
        elif key in unique and unique[key]["Cases"] != row["Cases"]:
            conflicts[key] = [unique.pop(key), row]
        elif key not in unique:
            unique[key] = row
    rows = sorted(unique.values(), key=lambda r: (r["Date"], r["SourceDiseaseCode"]))
    return rows, {"year": year, "pages": len(texts), "rows": len(rows), "narrative_candidates": len(narratives),
        "national_table_candidates": len(tables), "table_audits": audit, "narrative_issues": issues,
        "conflicts": [{"key": list(k), "candidates": v} for k,v in conflicts.items()],
        "status": "partial", "raw_sha256": digest, "source_url": reference["url"],
        "unsupported_font_or_overlapping_cells_are_not_zero": True}


class IndonesiaKemkesProfileCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=60, delay=0.2, **kwargs)

    async def crawl(self, **kwargs):
        response = await asyncio.to_thread(self.get, CHANNEL_URL)
        return self.parse(response)

    def parse(self, response):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=CHANNEL_URL,
            raw_data={"profiles": discover_profiles(response.content)})]

    def download_pdf(self, reference):
        url = reference["url"]
        if reference["download_kind"] == "linked_google_drive":
            match = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
            url = "https://drive.google.com/uc?export=download&id=" + match[1]
        elif reference["download_kind"] != "direct_pdf":
            raise IndonesiaContractError("Linked official shortlink needs verified public resolver artifacts")
        response = self.get(url)
        payload = response.content
        if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
            raise IndonesiaContractError("Official download did not return a complete PDF")
        return payload, {"resolved_url": response.url, "retrieval_mode": "live", "status_code": response.status_code,
            "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"),
            "retrieved_at": datetime.now(timezone.utc).isoformat()}
