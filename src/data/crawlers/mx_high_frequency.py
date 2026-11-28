"""Source-native DGE monthly tables and weekly national notification facts.

Parsing is offline. Each table owns its period; bulletin covers never replace
the conventional/special surveillance cutoffs printed on individual tables.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import BaseCrawler, CrawlerResult
from .mx import MexicoDGEAnnualCrawler, MexicoDGEContractError, NATIONAL_URL, _fold, _groups, _lines, _number, _text

MONTHLY_SCOPE = "mx_dge_monthly"
WEEKLY_SCOPE = "mx_dge_weekly"
SOURCE_IDS = {MONTHLY_SCOPE: "SRC_MX_DGE_MONTHLY", WEEKLY_SCOPE: "SRC_MX_DGE_WEEKLY"}
SOURCE_LABELS = {MONTHLY_SCOPE: "Mexico DGE Monthly Morbidity", WEEKLY_SCOPE: "Mexico DGE Weekly Epidemiological Bulletin"}
WEEKLY_LANDING_URL = "https://www.gob.mx/salud/acciones-y-programas/direccion-general-de-epidemiologia-boletin-epidemiologico"
WEEKLY_CURRENT_URL = "https://www.gob.mx/salud/documentos/boletinepidemiologico-sistema-nacional-de-vigilancia-epidemiologica-sistema-unico-de-informacion-417103"
HISTORY_START_YEAR = 1984
MONTH_NAMES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
SPANISH_MONTHS = {name: i for i, name in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"), 1)}


def normalize_scope(source):
    value = str(source or MONTHLY_SCOPE).strip().casefold()
    aliases = {"monthly": MONTHLY_SCOPE, "weekly": WEEKLY_SCOPE, MONTHLY_SCOPE: MONTHLY_SCOPE, WEEKLY_SCOPE: WEEKLY_SCOPE}
    if value not in aliases:
        raise ValueError(f"Select an explicit MX high-frequency source: {source!r}")
    return aliases[value]


def discover_monthly_tables(payload, *, start_year=HISTORY_START_YEAR, end_year=None):
    """Use actual edition labels/paths, including legacy female/male tokens."""
    soup = BeautifulSoup(payload, "lxml")
    selector = soup.find("select", attrs={"name": "anio"})
    if selector is None:
        raise MexicoDGEContractError("DGE national year selector missing")
    refs = []
    for option in selector.find_all("option"):
        label = _text(option.get_text(" ", strip=True))
        match = re.fullmatch(r"(19\d{2}|20\d{2})(?:\s+(General|Femenino|Masculino))?", label, re.I)
        if not match:
            raise MexicoDGEContractError(f"Unrecognized DGE edition {label!r}")
        year = int(match[1]); token = str(option.get("value", ""))
        if not token.isdigit():
            raise MexicoDGEContractError("Unexpected DGE edition path")
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        menu = soup.find("div", id="A_" + token)
        if menu is None:
            raise MexicoDGEContractError(f"Missing national menu for {label}")
        for table in menu.find_all("option"):
            name = str(table.get("value", ""))
            if name != "distribucion_casos_nuevos_enfermedad_mes":
                continue
            refs.append({"year": year, "sex": {"femenino": "female", "masculino": "male"}.get((match[2] or "").casefold(), "all"), "edition_path": token, "edition_label": label, "table": "national_monthly_cases", "url": urljoin(NATIONAL_URL, f"../{token}/morbilidad/nacional/{name}.pdf")})
    return sorted(refs, key=lambda r: (r["year"], r["sex"]))


def discover_weekly_links(payload, *, page_url, start_year=None, end_year=None):
    """Find only linked PDFs, preserving CMS titles instead of inventing URLs."""
    soup = BeautifulSoup(payload, "lxml")
    page_text = soup.get_text(" ", strip=True)
    years = re.findall(r"Bolet[ií]n\s+Epidemiol[oó]gico\s+(20\d{2})", page_text, re.I)
    title = soup.find("h1")
    title_year = re.search(r"\b(20\d{2})\b", title.get_text(" ", strip=True)) if title else None
    article = soup.select_one(".col-article-body")
    declared = re.search(r"semana\s+epidemiologica\s+(20\d{2})", _fold(article.get_text(" ",strip=True))) if article else None
    year = int(declared[1]) if declared else (int(title_year[1]) if title_year else (int(years[0]) if years else None))
    # The current CMS title includes the year in a separate heading/metadata.
    if year is None:
        dates = re.findall(r"(?:202\d|203\d)", str(soup.find("meta", attrs={"property": "og:title"}) or ""))
        year = int(dates[0]) if dates else None
    refs = []
    for anchor in soup.select("a[href]"):
        url = urljoin(page_url, anchor["href"])
        path = urlparse(url).path
        if not path.lower().endswith(".pdf") or urlparse(url).hostname not in {"www.gob.mx", "epidemiologia.salud.gob.mx"}:
            continue
        filename = path.rsplit("/", 1)[-1]
        match = re.search(r"(?:sem(?:ana)?[-_ ]*|boletin[-_ ]*)(\d{1,2})(?:(\d{2}))?\.pdf$", filename, re.I)
        if not match:
            continue
        report_year = (2000 + int(match[2])) if match[2] else year
        if report_year is None:
            raise MexicoDGEContractError("Official weekly index does not establish a source year")
        if start_year is not None and report_year < int(start_year): continue
        if end_year is not None and report_year > int(end_year): continue
        week = int(match[1])
        if not 1 <= week <= 53:
            raise MexicoDGEContractError("Invalid linked bulletin week")
        refs.append({"year": report_year, "issue_week": week, "table": "national_weekly_bulletin", "url": url, "index_url": page_url})
    unique = {r["url"]: r for r in refs}
    return sorted(unique.values(), key=lambda r: (r["year"], r["issue_week"], r["url"]))


def _row(*, scope, label, icd, value, missing, period_start, period_end, frequency, measure, sex, definition, dimensions, source_url, digest, retrieved_at, year=None):
    """Stable category identity excludes report page/issue provenance."""
    identity_label = re.sub(r"[§*•]", "", _fold(label)).strip()
    identity_icd = re.sub(r"\s+", "", icd).casefold()
    category = hashlib.sha256((identity_label + "|" + identity_icd + "|" + definition).encode()).hexdigest()[:20]
    code = f"dge_{category}__{sex}__{measure}"
    provenance_keys = {"source_page","source_table","bulletin_issue_year","bulletin_issue_week","source_footnotes","annual_total","annual_total_missing_status","monthly_sum_verified","dash_legend_pages","equivalent_source_pages","source_caption_ocr"}
    provenance = {k:v for k,v in dimensions.items() if k in provenance_keys}
    dims = {k:v for k,v in dimensions.items() if k not in provenance_keys}
    if measure.startswith("cumulative"):
        dims["cumulative_start"] = "source_year_start_not_independently_verified"
    dims.update(source_disease_label=label,source_icd_code=icd,sex=sex,age_group="all",geography_level="national")
    return {"Date": period_start.isoformat(), "PeriodStart": "" if measure.startswith("cumulative") else period_start.isoformat(), "PeriodEnd": period_end.isoformat(), "SourceReportPeriodStart": period_start.isoformat(), "SourceReportPeriodEnd": period_end.isoformat(), "Year": str(year or period_start.year), "Month": str(period_start.month), "DiseaseCode": "__source_native__", "RawDiseaseLabel": label, "SourceDiseaseCode": code, "SourceICDCode": icd, "Cases": value, "MissingValueStatus": missing, "PeriodType": "cumulative" if measure.startswith("cumulative") else frequency, "Frequency": frequency, "Measure": measure, "Unit": "count", "ReportingBasis": definition, "DefinitionVersion": f"{scope}_{definition}_v1", "Geography": "Mexico", "GeographyKey": "country:MX:national", "Source": SOURCE_LABELS[scope], "SourceURL": source_url, "LandingURL": NATIONAL_URL if scope == MONTHLY_SCOPE else WEEKLY_LANDING_URL, "RawSHA256": digest, "RetrievedAt": retrieved_at or "", "Sex": sex, "AgeGroup": "all", "SourcePage":str(provenance.get("source_page","")),"SourceProvenance":json.dumps(provenance,ensure_ascii=False,sort_keys=True),"QualityStatus": "provisional" if scope == WEEKLY_SCOPE else "raw", "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true", "PublicReleaseEnabled": "true", "Dimensions": json.dumps(dims, ensure_ascii=False, sort_keys=True)}


def _words_text(words):
    return _text(" ".join(w["text"] for line in _lines(words,tolerance=3) for w in line))


def parse_monthly_pdf(payload, *, reference, source_url=None, retrieved_at=None):
    import pdfplumber
    if not payload.startswith(b"%PDF"):
        raise MexicoDGEContractError("DGE monthly original is not PDF")
    year = int(reference["year"]); sex = reference["sex"]
    digest = hashlib.sha256(payload).hexdigest(); rows = []; failures = []; seen = set(); pages = []
    excluded_summary_rows = []
    with pdfplumber.open(io.BytesIO(payload)) as document:
        for page_no, original in enumerate(document.pages, 1):
            page = original.dedupe_chars(tolerance=.7, extra_attrs=())
            text = page.extract_text() or ""; folded = _fold(text)
            try:
                if not re.search(rf"estados\s+unidos\s+mexicanos\s+{year}\b", folded): raise MexicoDGEContractError("Monthly national geography/year heading mismatch")
                expected = {"all": "general", "female": "femenina", "male": "masculina"}[sex]
                if not re.search(r"poblacion\s+" + expected, folded[:600]): raise MexicoDGEContractError("Monthly population heading mismatch")
                words = page.extract_words(x_tolerance=1, y_tolerance=2)
                header = [w for w in words if w["top"] < page.height * .25]
                months = []
                for month in MONTH_NAMES:
                    matches = [w for w in header if _fold(w["text"]).strip(".") == month]
                    if len(matches) != 1: raise MexicoDGEContractError("Twelve explicit month columns required")
                    months.append(matches[0])
                totals = [w for w in header if _fold(w["text"]) == "total" and w["x0"] < months[0]["x0"]]
                if len(totals) != 1: raise MexicoDGEContractError("Explicit annual total column required")
                headings = [totals[0], *months]
                centers = [(w["x0"] + w["x1"]) / 2 for w in headings]
                bounds = [centers[0] - (centers[1]-centers[0])/2] + [(a+b)/2 for a,b in zip(centers,centers[1:])] + [page.width]
                code_heads = [w for w in header if _fold(w["text"]).startswith("codigo")]
                if len(code_heads) != 1: raise MexicoDGEContractError("Monthly source code heading required")
                split = code_heads[0]["x0"] - 2
                top = max(w["bottom"] for w in months)
                bottom = min((w["top"] for w in words if _fold(w["text"]).startswith("fuente")), default=page.height)
                body = [w for w in words if top < w["top"] < bottom]
                anchors = []
                for line in _lines(body):
                    groups = _groups([w for w in line if w["x0"] >= bounds[0]-12],gap=3)
                    if len(groups)==13:
                        anchors.append((groups[0]["top"], groups))
                if not anchors: raise MexicoDGEContractError("No complete monthly numeric rows")
                accepted = 0
                for i, (y, cells) in enumerate(anchors):
                    try:
                        y0 = (anchors[i-1][0]+y)/2 if i else top
                        y1 = (y+anchors[i+1][0])/2 if i+1<len(anchors) else bottom
                        prefix = [w for w in body if y0 <= w["top"] < y1 and w["x1"] < bounds[0]]
                        label = _words_text([w for w in prefix if w["x0"] < split]); icd = _words_text([w for w in prefix if w["x0"] >= split])
                        if _fold(label).startswith(("total","fuente")) and not icd:continue
                        if (not label and not icd and page_no == len(document.pages)
                            and i == len(anchors)-1 and not failures and len(seen) > 1
                            and rows and all(r["Cases"] for r in rows)):
                            # The 2025 female edition omits the label on its final
                            # grand-total row. Recognize it only after all thirteen
                            # printed columns independently match the parsed edition.
                            # An arbitrary unnamed disease row must remain a failure.
                            summary_values = [_number(c["text"], rate=False) for c in cells]
                            monthly_sums = [sum(int(r["Cases"]) for r in rows if int(r["Month"]) == m)
                                            for m in range(1, 13)]
                            expected_summary = [sum(monthly_sums), *monthly_sums]
                            if (all(v for v, _ in summary_values) and expected_summary[0] > 0
                                and [int(v) for v, _ in summary_values] == expected_summary):
                                excluded_summary_rows.append({
                                    "page": page_no, "y": round(y, 2),
                                    "reason": "unlabelled_edition_total_reconciled",
                                    "verified_columns": 13, "disease_categories": len(seen),
                                    "annual_total": expected_summary[0], "monthly_totals": monthly_sums,
                                })
                                continue
                        if not label or not icd: raise MexicoDGEContractError("Monthly source label/ICD missing")
                        values = [_number(c["text"], rate=False) for c in cells]
                        if all(v for v,_ in values) and sum(int(v) for v,_ in values[1:]) != int(values[0][0]): raise MexicoDGEContractError("Monthly cells do not reconcile with explicit annual total")
                        key = (label,icd)
                        if key in seen: raise MexicoDGEContractError("Duplicate monthly source label/ICD in edition")
                        seen.add(key)
                        for month, (value, missing) in enumerate(values[1:], 1):
                            start = date(year,month,1); end = (date(year+1,1,1) if month==12 else date(year,month+1,1))-timedelta(days=1)
                            dims = {"source_page": page_no, "edition_path": reference["edition_path"], "annual_total": values[0][0], "annual_total_missing_status": values[0][1], "monthly_sum_verified": all(v for v,_ in values), "source_case_definition": "estimated_source_formula" if "§" in label else "new_case_notifications", "source_footnotes": text[text.find("FUENTE"): ] if "FUENTE" in text else ""}
                            definition = "monthly_estimated_cases" if "§" in label else "monthly_new_case_notifications"
                            rows.append(_row(scope=MONTHLY_SCOPE,label=label,icd=icd,value=value,missing=missing,period_start=start,period_end=end,frequency="monthly",measure="case_notifications",sex=sex,definition=definition,dimensions=dims,source_url=source_url or reference["url"],digest=digest,retrieved_at=retrieved_at))
                        accepted += 1
                    except MexicoDGEContractError as exc:
                        failures.append({"page":page_no,"y":round(y,2),"error":str(exc)})
                pages.append({"page":page_no,"candidate_rows":len(anchors),"accepted_diseases":accepted})
            except MexicoDGEContractError as exc:
                failures.append({"page":page_no,"error":str(exc)})
    if not rows: raise MexicoDGEContractError("No validated DGE monthly facts: " + json.dumps(failures,ensure_ascii=False))
    return rows,{"status":"partial" if failures else "parsed","sha256":digest,"source_url":source_url or reference["url"],"rows":len(rows),"pages":pages,"failures":failures,"failed_count":len(failures),"excluded_summary_rows":excluded_summary_rows}


def _issue_period(text, reference):
    folded = _fold(text)
    match = re.search(r"semana\s+(\d{1,2})\s*/\s*del\s+(\d{1,2})(?:\s+de\s+(\w+))?\s+al\s+(\d{1,2})\s+de\s+(\w+)\s+del?\s+(20\d{2})",folded)
    if not match: raise MexicoDGEContractError("Explicit bulletin cover week/date interval missing")
    week, first, first_month, last, month, year = match.groups(); year=int(year); week=int(week)
    if month not in SPANISH_MONTHS: raise MexicoDGEContractError("Unknown source cover month")
    if first_month and first_month not in SPANISH_MONTHS:raise MexicoDGEContractError("Unknown source cover starting month")
    start=date(year,SPANISH_MONTHS[first_month or month],int(first)); end=date(year,SPANISH_MONTHS[month],int(last))
    if end < start:
        start = date(year if end.month>1 else year-1, end.month-1 if end.month>1 else 12, int(first))
    if (end-start).days!=6 or start.weekday()!=6 or end.weekday()!=5: raise MexicoDGEContractError("Source weekly interval is not a Sunday-Saturday week")
    if year!=int(reference["year"]) or week!=int(reference["issue_week"]): raise MexicoDGEContractError("Bulletin cover differs from discovered reference")
    return year,week,start,end


def _table_period(title, label, *, issue_year, issue_week, issue_start, issue_end):
    folded = _fold(title)
    main = re.search(r"hasta\s+la\s+semana(?:\s+epidemiologica)?\s+(\d{1,2})\s+del?\s+(20\d{2})", folded)
    if not main: raise MexicoDGEContractError("Table's explicit epidemiological cutoff missing")
    week=int(main[1]);year=int(main[2])
    for clause in folded.split(";")[1:]:
        override=re.search(r"(.+?)\s+hasta\s+la(?:\s+semana(?:\s+epidemiologica)?)?\s+(\d{1,2})\s+del?\s+(20\d{2})",clause)
        aliases={re.sub(r'[^a-z0-9]','',_fold(label).strip('§*& '))}
        aliases.update(re.sub(r'[^a-z0-9]','',_fold(v)) for v in re.findall(r'\(([A-Z]{2,})\)',label))
        # This abbreviation is printed in the verified table caption; the
        # disease heading spells out "primaria". No fuzzy name matching.
        if re.sub(r'[^a-z0-9]','',_fold(label))=='meningoencefalitisamebianaprimaria':
            aliases.add('meningoencefalitisamebianap')
        if override and re.sub(r'[^a-z0-9]','',override[1]) in aliases:
            week=int(override[2]);year=int(override[3])
    if year!=issue_year or not 1<=week<=issue_week: raise MexicoDGEContractError("Table cutoff outside verified bulletin calendar")
    delta=timedelta(weeks=issue_week-week)
    return year,week,issue_start-delta,issue_end-delta


def _weekly_number(raw, *, zero_dash):
    compact=_text(raw).casefold()
    if compact in {"-","—"}:
        return ("0","") if zero_dash else ("","source_missing")
    if compact in {"n.d.","n.d","nd","s.n.","s.n"}:return "","source_missing"
    if compact in {"n.a.","n.a","na"}:return "","not_applicable"
    return _number(raw,rate=False)


def parse_weekly_pdf(payload, *, reference, source_url=None, retrieved_at=None, title_ocr=None):
    """Read national summary cells and explicit TOTAL rows, never sum states."""
    import pdfplumber
    import pypdfium2
    if not payload.startswith(b"%PDF"):raise MexicoDGEContractError("DGE weekly original is not PDF")
    digest=hashlib.sha256(payload).hexdigest();rows=[];failures=[];pages=[]
    quick=pypdfium2.PdfDocument(payload)
    try:
        cover=quick[0].get_textpage().get_text_range()
        year,issue_week,issue_start,issue_end=_issue_period(cover,reference)
        candidates=[];legend_pages=[];unreadable_caption_pages=[]
        for i in range(len(quick)):
            p=quick[i];tp=p.get_textpage();txt=tp.get_text_range();tp.close();p.close()
            if re.search(r"\(\s*-\s*\)\s*:\s*no\s+se\s+presentaron\s+casos",_fold(txt)):legend_pages.append(i+1)
            if re.search(r"CUADRO\s+\d",txt,re.I) and ("ENFERMEDAD" in txt or "ENTIDAD" in txt):candidates.append(i)
            elif 'CIE-' in txt and ('ENFERMEDAD' in txt or 'ENTIDAD' in txt):unreadable_caption_pages.append(i+1)
    finally:quick.close()
    from .mx_title_ocr import verify_caption_evidence
    ocr_captions=verify_caption_evidence(title_ocr,pdf_sha256=digest,issue_year=year,issue_week=issue_week) if title_ocr is not None else {}
    candidates=sorted(set(candidates)|{number-1 for number in ocr_captions})
    failures.extend({'page':number,'error':'Table caption/cutoff absent from text layer and not covered by verified OCR evidence'} for number in unreadable_caption_pages if number not in ocr_captions)
    if not candidates and not ocr_captions:
        raise MexicoDGEContractError(
            "Bulletin cover is verified but table captions/cutoffs are absent from the PDF text layer; "
            "original archived, numerical extraction blocked pending verified title OCR"
        )
    with pdfplumber.open(io.BytesIO(payload)) as doc:
        for index in candidates:
            page=doc.pages[index].dedupe_chars(tolerance=.7,extra_attrs=());text=page.extract_text() or "";words=page.extract_words(x_tolerance=1,y_tolerance=2)
            try:
                zero_dash=bool(legend_pages)
                caption_evidence=ocr_captions.get(index+1)
                # Text-layer captions remain authoritative where available;
                # OCR supplies only independently agreed missing title text.
                if caption_evidence and re.search(r"CUADRO\s+\d",text,re.I):raise MexicoDGEContractError("OCR cannot replace an existing source text caption")
                table_match=re.search(r"CUADRO\s+(\d+(?:\.\d+)?)\.?",caption_evidence['caption'] if caption_evidence else text,re.I)
                table_no=table_match[1]
                entidad=[w for w in words if w["text"]=="ENTIDAD"]
                year_heads=[w for w in words if w["text"]==str(year) and 70<w["top"]<270]
                if not year_heads:raise MexicoDGEContractError("Source current-year column headers missing")
                head_y=max(w["top"] for w in year_heads)
                title_words=[w for w in words if 70<w["top"]<head_y]
                # Full table caption retains named special-surveillance exceptions.
                cie=[w for w in title_words if w["text"].startswith("CIE-")]
                if not cie:raise MexicoDGEContractError("Source ICD heading missing")
                first_cie=min(w["top"] for w in cie)
                caption=caption_evidence['caption'] if caption_evidence else _words_text([w for w in words if 100<w["top"]<first_cie-12])
                foot=min((w["top"] for w in words if "FUENTE:" in w["text"]),default=page.height)
                common={"source_page":index+1,"source_table":table_no,"bulletin_issue_year":year,"bulletin_issue_week":issue_week,"source_footnotes":_words_text([w for w in words if w["top"]>=foot]),"dash_semantics":"reported_zero" if zero_dash else "source_missing","dash_legend_pages":legend_pages}
                if caption_evidence:common['source_caption_ocr']=caption_evidence
                before=len(rows)
                if entidad and caption_evidence:
                    from .mx_weekly_tables import parse_national_total
                    parsed = parse_national_total(words,caption_evidence=caption_evidence,
                        issue_year=year,issue_week=issue_week,issue_start=issue_start,issue_end=issue_end,
                        page_number=index+1,source_url=source_url or reference['url'],digest=digest,
                        retrieved_at=retrieved_at,legend_pages=legend_pages)
                    rows.extend(parsed)
                    pages.append({'page':index+1,'source_table':table_no,'rows':len(parsed),
                        'excluded_source_cells':[cell for r in parsed for cell in json.loads(r['SourceProvenance']).get('excluded_source_cells',[])]})
                    continue
                if not entidad:
                    # Table 1/2: current weekly, current cumulative, historical
                    # median, previous weekly/cumulative. Only current facts.
                    heads=sorted([w for w in words if w["top"]==head_y and re.fullmatch(r"20\d{2}(?:-20\d{2})?",w["text"])],key=lambda w:w["x0"])
                    if len(heads)!=5 or [w["text"] for w in heads[:2]]!=[str(year)]*2:raise MexicoDGEContractError("Five national comparison headers not verified")
                    centers=[(w["x0"]+w["x1"])/2 for w in heads];bounds=[centers[0]-(centers[1]-centers[0])/2]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[page.width]
                    code_left=min(w["x0"] for w in cie)-12
                    body=[w for w in words if head_y+10<w["top"]<foot]
                    anchors=[]
                    for line in _lines(body):
                        cells=[[w for w in line if bounds[i] <= (w["x0"]+w["x1"])/2 < bounds[i+1]] for i in range(5)]
                        if all(cells):anchors.append((sum(w["top"] for w in cells[0])/len(cells[0]),cells))
                    for i,(y,cells) in enumerate(anchors):
                        low=(anchors[i-1][0]+y)/2 if i else head_y+10;high=(y+anchors[i+1][0])/2 if i+1<len(anchors) else foot
                        prefix=[w for w in body if low<=w["top"]<high and w["x1"]<bounds[0]]
                        icd_words=[w for w in prefix if w["x0"]>=code_left-80 and (re.match(r"^[A-Z]\d",w["text"]) or w["text"]=="S/C")]
                        if not icd_words:raise MexicoDGEContractError("Explicit source ICD token missing")
                        row_split=min(w["x0"] for w in icd_words)-.1
                        label=_words_text([w for w in prefix if w["x0"]<row_split]);icd=_words_text([w for w in prefix if w["x0"]>=row_split])
                        if not label or not icd:raise MexicoDGEContractError("National disease label/code missing")
                        sy,week,start,end=_table_period(caption,label,issue_year=year,issue_week=issue_week,issue_start=issue_start,issue_end=issue_end)
                        definition="special_confirmed_surveillance" if table_no=="1" else "conventional_including_probable"
                        for j,measure in [(0,"case_notifications"),(1,"cumulative_case_notifications")]:
                            value,missing=_weekly_number(_words_text(cells[j]),zero_dash=zero_dash)
                            dims={**common,"source_epi_year":sy,"source_epi_week":week,"table_family":"national_summary","calendar":"source_verified_sunday_saturday","cumulative_start":"source_year_start_not_independently_verified" if j else None}
                            rows.append(_row(scope=WEEKLY_SCOPE,label=label,icd=icd,value=value,missing=missing,period_start=start,period_end=end,frequency="weekly",measure=measure,sex="all",definition=definition,dimensions=dims,source_url=source_url or reference["url"],digest=digest,retrieved_at=retrieved_at,year=sy))
                else:
                    # Retain explicit national TOTAL only; state and previous
                    # year comparison cells remain in the archived original.
                    total=[w for w in words if w["text"]=="TOTAL"]
                    if len(total)!=1:raise MexicoDGEContractError("Exactly one explicit national TOTAL row required")
                    y=total[0]["top"];tw=[w for w in words if abs(w["top"]-y)<3 and w["x0"]>total[0]["x1"]+3]
                    cells=_groups(tw,gap=5)
                    cie=sorted(cie,key=lambda w:w["x0"]);n=len(cie)
                    if n not in (1,2,3) or len(cells)!=4*n:raise MexicoDGEContractError("TOTAL columns do not match explicit disease/sex headers")
                    h=[w for w in words if w["text"]=="H" and head_y<w["top"]<head_y+45];m=[w for w in words if w["text"]=="M" and head_y<w["top"]<head_y+45]
                    if len(h)!=n or len(m)!=n:raise MexicoDGEContractError("Explicit H/M cumulative headers required")
                    centers=[(w["x0"]+w["x1"])/2 for w in cie];gb=[130]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[page.width]
                    # Caption may span two lines; disease labels end directly
                    # above CIE headers, separated by a larger source gap.
                    title_lines=_lines([w for w in words if 100<w["top"]<first_cie])
                    disease_lines=[]
                    for line in reversed(title_lines):
                        if any(w["text"].casefold().startswith(("cuadro","hasta")) for w in line):break
                        if not disease_lines or disease_lines[-1][0]["top"]-line[0]["top"]<12:disease_lines.append(line)
                        else:break
                    disease_words=[w for line in disease_lines for w in line]
                    caption=_words_text([w for w in words if 100<w["top"]<min(w["top"] for w in disease_words)]) if disease_words else caption
                    for g in range(n):
                        label=_words_text([w for w in disease_words if gb[g]<=(w["x0"]+w["x1"])/2<gb[g+1]])
                        code_words=[w for w in words if first_cie+9<w["top"]<head_y-3 and gb[g]<=(w["x0"]+w["x1"])/2<gb[g+1] and w["text"] not in {"ENTIDAD","FEDERATIVA"}]
                        icd=_words_text(code_words)
                        if not label or not icd:raise MexicoDGEContractError("National TOTAL disease heading/code missing")
                        sy,week,start,end=_table_period(caption,label,issue_year=year,issue_week=issue_week,issue_start=issue_start,issue_end=issue_end)
                        definition="special_confirmed_surveillance" if "§" in label else "conventional_including_probable"
                        for j,measure,sex in [(0,"case_notifications","all"),(1,"cumulative_case_notifications","male"),(2,"cumulative_case_notifications","female")]:
                            value,missing=_weekly_number(cells[g*4+j]["text"],zero_dash=zero_dash)
                            dims={**common,"source_epi_year":sy,"source_epi_week":week,"table_family":"national_total_from_state_table","calendar":"source_verified_sunday_saturday"}
                            rows.append(_row(scope=WEEKLY_SCOPE,label=label,icd=icd,value=value,missing=missing,period_start=start,period_end=end,frequency="weekly",measure=measure,sex=sex,definition=definition,dimensions=dims,source_url=source_url or reference["url"],digest=digest,retrieved_at=retrieved_at,year=sy))
                pages.append({"page":index+1,"source_table":table_no,"rows":len(rows)-before})
            except (MexicoDGEContractError,ValueError,IndexError) as exc:
                page_error=exc
                # Roll back the page to avoid partial/conflicting header use.
                rows=[r for r in rows if r["SourcePage"]!=str(index+1)]
                if any(w['text']=='ENTIDAD' for w in words) and not ocr_captions.get(index+1):
                    from .mx_weekly_tables import parse_national_total
                    try:
                        parsed=parse_national_total(words,caption_evidence=None,
                            issue_year=year,issue_week=issue_week,issue_start=issue_start,issue_end=issue_end,
                            page_number=index+1,source_url=source_url or reference['url'],digest=digest,
                            retrieved_at=retrieved_at,legend_pages=legend_pages)
                        rows.extend(parsed)
                        pages.append({'page':index+1,'rows':len(parsed),'layout':'verified_mixed_columns',
                            'excluded_source_cells':[cell for r in parsed for cell in json.loads(r['SourceProvenance']).get('excluded_source_cells',[])]})
                        continue
                    except (MexicoDGEContractError,ValueError,IndexError) as fallback_exc:
                        page_error=fallback_exc
                failures.append({"page":index+1,"error":str(page_error)})
    rows,conflicts=deduplicate_rows(rows)
    failures.extend(conflicts)
    if not rows:raise MexicoDGEContractError("No verified DGE weekly national observations: "+json.dumps(failures,ensure_ascii=False))
    excluded_cells=[cell for page in pages for cell in page.get('excluded_source_cells',[])]
    return rows,{"status":"partial" if failures or excluded_cells else "parsed","sha256":digest,"source_url":source_url or reference["url"],"issue_year":year,"issue_week":issue_week,"rows":len(rows),"pages":pages,"failures":failures,"failed_count":len(failures),'excluded_source_cells':excluded_cells,"excluded_tables":"Narratives, charts, nonstandard tables, state rows, medians and previous-year comparison columns remain archived; only reviewed current-year national facts are emitted."}


def deduplicate_rows(rows):
    """Identical repeat cells collapse; conflicting same-period cells quarantine."""
    result={};conflicting=set();failures=[]
    for row in rows:
        key=(row["Date"],row["SourceDiseaseCode"],row["DefinitionVersion"],row["GeographyKey"])
        if key in conflicting:continue
        old=result.get(key)
        if old and (old["Cases"],old["MissingValueStatus"])!=(row["Cases"],row["MissingValueStatus"]):
            result.pop(key);conflicting.add(key);failures.append({"identity":list(key),"error":"Conflicting national source facts quarantined"});continue
        if old:
            provenance=json.loads(old["SourceProvenance"])
            provenance.setdefault("equivalent_source_pages",[provenance["source_page"]]).append(int(row["SourcePage"]))
            for excluded in json.loads(row['SourceProvenance']).get('excluded_source_cells',[]):
                if excluded not in provenance.setdefault('excluded_source_cells',[]):
                    provenance['excluded_source_cells'].append(excluded)
            old["SourceProvenance"]=json.dumps(provenance,ensure_ascii=False,sort_keys=True)
        else:result[key]=row
    return list(result.values()),failures


class MexicoDGEHighFrequencyCrawler(BaseCrawler):
    """Live indexes plus integrity-checked, freshness-bounded original cache."""
    def __init__(self, *, source=MONTHLY_SCOPE, cache_directory=None, **kwargs):
        super().__init__(timeout=90, delay=.15, **kwargs)
        self.source_scope=normalize_scope(source)
        self.cache=MexicoDGEAnnualCrawler(cache_directory=cache_directory) if cache_directory else MexicoDGEAnnualCrawler()
        self.last_audit={}

    def discover(self, *, start_year=None, end_year=None, weekly_index_url=None, latest_reports=None):
        if self.source_scope==MONTHLY_SCOPE:
            r=self.get(NATIONAL_URL);payload=r.content
            return discover_monthly_tables(payload,start_year=start_year or HISTORY_START_YEAR,end_year=end_year),[(NATIONAL_URL,payload)]
        # Follow the official DGE hub to discover the current year rather than
        # assuming a fixed CMS suffix or last published week.
        indexes=[];queue=[weekly_index_url] if weekly_index_url else [WEEKLY_LANDING_URL];visited=set();refs=[]
        while queue:
            url=queue.pop(0)
            if url in visited:continue
            visited.add(url);r=self.get(url);payload=r.content;indexes.append((url,payload))
            parsed=discover_weekly_links(payload,page_url=url,start_year=start_year,end_year=end_year);refs.extend(parsed)
            soup=BeautifulSoup(payload,"lxml")
            for a in soup.select("a[href]"):
                target=urljoin(url,a["href"]);name=_fold(a.get_text(" ",strip=True))
                if urlparse(target).hostname!="www.gob.mx" or "/salud/documentos/" not in target:continue
                if "boletin" not in _fold(target+" "+name):continue
                years=re.findall(r"20\d{2}",name)
                if years and ((start_year is not None and int(years[-1])<int(start_year)) or (end_year is not None and int(years[-1])>int(end_year))):continue
                if target not in visited and target not in queue:queue.append(target)
            if len(visited)>100:raise MexicoDGEContractError("Weekly index traversal exceeded reviewed bound")
        refs=sorted({r["url"]:r for r in refs}.values(),key=lambda r:(r["year"],r["issue_week"]))
        if latest_reports is not None:refs=refs[-max(0,int(latest_reports)):]
        if not refs:raise MexicoDGEContractError("No linked official weekly reports found in requested source window")
        return refs,indexes

    def download(self, reference, *, force=False, cache_ttl_days=1):
        return self.cache.download(reference,force=force,cache_ttl_days=cache_ttl_days)

    def close(self):
        self.session.close();self.cache.session.close()

    def fetch_rows(self, *, start_year=None,end_year=None,force=False,**options):
        refs,indexes=self.discover(start_year=start_year,end_year=end_year,**options);rows=[];failures=[];reports=[]
        parser=parse_monthly_pdf if self.source_scope==MONTHLY_SCOPE else parse_weekly_pdf
        for ref in refs:
            try:
                payload,meta=self.download(ref,force=force)
                kwargs={}
                if self.source_scope==WEEKLY_SCOPE:
                    from .mx_title_ocr import capture_summary_captions
                    kwargs['title_ocr']=capture_summary_captions(payload)
                parsed,audit=parser(payload,reference=ref,retrieved_at=meta.get("retrieved_at"),**kwargs)
                rows.extend(parsed);reports.append(audit)
            except Exception as exc:failures.append({"url":ref["url"],"error":str(exc)})
        self.last_audit={"discovered_tables":len(refs),"reports":reports,"failures":failures,"coverage_complete":not failures and all(not a["failed_count"] and not a.get('excluded_source_cells') for a in reports)}
        if not rows:raise MexicoDGEContractError("No source observations; inspect source audit")
        return rows

    async def crawl(self, **kwargs):
        import asyncio
        rows=await asyncio.to_thread(self.fetch_rows,**kwargs)
        return [CrawlerResult(title=SOURCE_LABELS[self.source_scope],url=NATIONAL_URL if self.source_scope==MONTHLY_SCOPE else WEEKLY_LANDING_URL,raw_data={"rows":rows},metadata=self.last_audit)]

    def parse(self, response):
        raise MexicoDGEContractError("Parsing requires a frozen source edition reference")
