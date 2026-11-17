"""Maldives HPA reports published in the Ministry's public archive.

Only printed numeric labels attached to the current-year bars are read.
Line heights, historical comparison curves and Atoll incidence are not counts.
"""
from __future__ import annotations

import calendar
import hashlib
import io
import json
import re
from datetime import date
from urllib.parse import parse_qs, urljoin, urlparse

from lxml import html

from src.data.crawlers.base import BaseCrawler

DEFAULT_SCOPE = "mv_hpa_communicable"
DEFAULT_SOURCE_NAME = "Maldives HPA Communicable Disease Reports"
ONTOLOGY_SOURCE_ID = "SRC_MV_HPA_COMMUNICABLE"
HISTORY_START_YEAR = 2015
INDEX_URL = "https://health.gov.mv/en/publications?search=monthly"
HPA_URL = "https://hpa.gov.mv/en/publications"
MONTHS = {calendar.month_name[n].lower(): n for n in range(1, 13)}


class MaldivesContractError(ValueError):
    pass


class MaldivesHPACrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=35, max_retries=1, delay=0, **kwargs)

    def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged Maldives collector")

    def parse(self, content, **kwargs):
        raise NotImplementedError("Use parse_report_pdf on captured originals")


def official_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {"health.gov.mv", "hpa.gov.mv"} and not parsed.username and not parsed.password


def discover_reports(payload, url=INDEX_URL, *, start_year=HISTORY_START_YEAR, end_year=None):
    if not official_url(url):
        raise MaldivesContractError("Unexpected Maldives directory host")
    tree = html.fromstring(payload)
    reports, pages = {}, set()
    for a in tree.xpath('//a[@href]'):
        target = urljoin(url, a.get("href").strip())
        if not official_url(target):
            continue
        parsed = urlparse(target)
        title = " ".join(a.text_content().split())
        if parsed.path == "/en/publications" and parse_qs(parsed.query).get("search") == ["monthly"] and "page" in parse_qs(parsed.query):
            pages.add(target)
        if not re.search(r"monthly\s+(?:communicable\s+disease|cd)\s+report", title, re.I):
            continue
        period = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(20\d{2})\b", title, re.I)
        if not period or not parsed.path.startswith("/en/publications/"):
            continue
        year, month = int(period[2]), MONTHS[period[1].lower()]
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        reports[target] = {"url": target, "title": title, "year": year, "month": month}
    return list(reports.values()), sorted(pages)


def discover_pdfs(payload, report):
    tree = html.fromstring(payload)
    # The sidebar contains a site-wide recruitment PDF. Select attachments
    # whose own labels identify this disease report, never every page PDF.
    urls = []
    for a in tree.xpath('//a[@href]'):
        target = urljoin(report["url"], a.get("href").strip())
        label = " ".join(a.text_content().split())
        if (official_url(target) and urlparse(target).path.lower().endswith(".pdf")
                and re.search(r"monthly\s+(?:cd|communicable)", label, re.I)):
            urls.append(target)
    if len(set(urls)) != 1:
        raise MaldivesContractError("Expected one explicitly labelled monthly CD attachment")
    return {**report, "pdf_url": urls[0]}


def _bar_labels(page, *, box, heading, month, year):
    region = page.crop(box)
    words = region.extract_words(return_chars=True)
    text = region.extract_text() or ""
    if heading not in text:
        raise MaldivesContractError(f"Monthly chart lost its {heading} heading")
    months = [w for w in words if w["text"] in list(calendar.month_abbr)[1:]]
    months.sort(key=lambda w: w["x0"])
    if [w["text"] for w in months] != list(calendar.month_abbr)[1:]:
        raise MaldivesContractError("Monthly chart must establish all twelve month positions")
    # A filled legend rectangle immediately left of the printed report year
    # binds the bars to that year, independently of historical line labels.
    legends = [w for w in words if w["text"] == str(year) and w["top"] > months[0]["bottom"]]
    if len(legends) != 1:
        raise MaldivesContractError("Chart has no unambiguous current-year legend")
    legend = legends[0]
    swatches = [r for r in region.rects if r.get("fill") and 0 < legend["x0"] - r["x1"] < 5
                and abs((r["top"] + r["bottom"])/2 - (legend["top"] + legend["bottom"])/2) < 4]
    if len(swatches) != 1:
        raise MaldivesContractError("Cannot bind current-year legend to a bar fill")
    fill = swatches[0]["non_stroking_color"]
    bars = [r for r in region.rects if r.get("fill") and r["non_stroking_color"] == fill
            and 3 < r["width"] < 15 and r["bottom"] < months[0]["top"]]
    bars.sort(key=lambda r: r["x0"])
    if len(bars) != month or max(r["bottom"] for r in bars) - min(r["bottom"] for r in bars) > 1:
        raise MaldivesContractError("Current-year bars do not match report month or baseline")
    values = []
    for n, bar in enumerate(bars):
        center = (bar["x0"] + bar["x1"])/2
        axis = months[n]
        if abs(center - (axis["x0"] + axis["x1"])/2) > 2:
            raise MaldivesContractError("Current-year bar does not align with its month")
        chars = [c for c in region.chars if c["text"].isdigit()
                 and abs(c["matrix"][0]) < .001 and c["matrix"][1] > .99
                 and abs((c["x0"] + c["x1"])/2 - center) < 2
                 and c["bottom"] < bar["top"] and c["top"] > box[1]]
        chars.sort(key=lambda c: -c["top"])
        vertical = bool(chars) and 2 < bar["top"] - max(c["bottom"] for c in chars) < 15
        # Rotation is bottom-to-top; reassemble source glyphs in text order.
        discontinuous = vertical and any(abs(a["top"] - b["bottom"]) > 1 for a, b in zip(chars, chars[1:]))
        # Early templates print some labels horizontally. Bind the whole
        # numeric word to the bar center and top; nearby historical-line
        # labels and chart-axis values do not satisfy this attachment rule.
        horizontal = [w for w in words if re.fullmatch(r"\d+", w["text"])
                      and all(abs(c["matrix"][0] - 1) < .001 and abs(c["matrix"][1]) < .001 for c in w["chars"])
                      and abs((w["x0"] + w["x1"])/2 - center) < max(2, bar["width"]/2)
                      and -w["height"]/2 < bar["top"] - w["bottom"] < 15
                      and w["top"] > box[1]]
        candidates = ([int("".join(c["text"] for c in chars))] if vertical else [])
        candidates += [int(w["text"]) for w in horizontal]
        # One overlapped or missing label does not invalidate other explicitly
        # labelled months. Retain its absence, never derive its bar height.
        values.append(candidates[0] if candidates and not discontinuous and len(set(candidates)) == 1 else None)
    return values


def _chart_panels(page):
    """Locate complete vector chart panels and identify their own headings.

    Panel order and vertical placement change between official reports. The
    four stroked borders and their printed headings are more stable than page
    coordinates and avoid accidentally reading another disease's chart.
    """
    boxes = {(r["x0"], r["top"], r["x1"], r["bottom"]) for r in page.rects
             if r.get("stroke") and 200 < r["width"] < 300 and 130 < r["height"] < 260}
    if len(boxes) != 4:
        raise MaldivesContractError("Monthly figure does not establish four vector chart panels")
    contracts = {
        "Dengue Fever": ("dengue_fever", "Dengue Fever"),
        "Viral Fever": ("viral_fever", "Viral Fever"),
        "VF": ("viral_fever", "Viral Fever"),
        "AGE": ("acute_gastroenteritis", "AGE"),
        "ARI": ("acute_respiratory_infection", "ARI"),
    }
    panels, missing, identified = [], [], set()
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        heading_region = page.crop((box[0], box[1], box[2], box[1] + 40))
        text = heading_region.extract_text() or ""
        headings = [label for label in contracts if re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", text)]
        if len(headings) != 1:
            missing.append({"box": list(box), "error": "Panel heading is absent or ambiguous (possibly image-only)"})
            continue
        printed = headings[0]
        code, label = contracts[printed]
        if code in identified:
            raise MaldivesContractError("Duplicate monthly disease panel headings")
        identified.add(code)
        panels.append((code, label, printed, box))
    return panels, missing


def parse_report_pdf(payload, report):
    if not official_url(report.get("pdf_url", "")) or not official_url(report.get("url", "")):
        raise MaldivesContractError("Monthly report lacks official publication/attachment URLs")
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise MaldivesContractError("Incomplete or non-PDF HPA attachment")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        cover = pdf.pages[0].extract_text() or ""
        match = re.search(r"Monthly\s+Communicable\s+Disease\s+Report\s+(\w+)\s+(20\d{2})", cover, re.I)
        if not match or match[1].lower() not in MONTHS:
            raise MaldivesContractError("HPA cover does not identify an explicit monthly reporting period")
        year, month = int(match[2]), MONTHS[match[1].lower()]
        if (year, month) != (report["year"], report["month"]):
            raise MaldivesContractError("Report period contradicts official publication title")
        # These years' originals have reviewed vector monthly bar charts.
        # Other layouts are archived for review, never approximated.
        if year not in {2022, 2023, 2024} or len(pdf.pages) != 2:
            raise MaldivesContractError("Unreviewed HPA monthly chart layout")
        page = pdf.pages[1]
        text = page.extract_text() or ""
        if (abs(page.width - 595.2) > 1 or abs(page.height - 841.92) > 1
                or not re.search(r"Figure\s+2\s*:\s+Trends.*by Month", text)):
            raise MaldivesContractError("HPA chart identity or layout changed")
        panels, missing = _chart_panels(page)
        rows, skipped = [], missing
        for code, label, printed, box in panels:
            try:
                values = _bar_labels(page, box=box, heading=printed, month=month, year=year)
            except MaldivesContractError as exc:
                skipped.append({"disease": label, "error": str(exc)})
                continue
            for n, value in enumerate(values, 1):
                if value is None:
                    skipped.append({"disease": label, "month": n,
                                    "error": "Month lacks one unambiguous directly attached printed count"})
                    continue
                end = date(year, n, calendar.monthrange(year, n)[1]).isoformat()
                rows.append({
                    "Date": end, "Year": str(year), "Month": str(n),
                    "DiseaseCode": "__source_native__", "RawDiseaseLabel": label,
                    "SourceDiseaseCode": f"hpa:{code}:reported:monthly:cases",
                    "Cases": str(value), "Measure": "cases", "Unit": "count",
                    "Frequency": "monthly", "PeriodType": "monthly", "ReportingBasis": "notification",
                    "DefinitionVersion": ("mv_hpa_monthly_2024_printed_bar_labels_v1" if year == 2024
                                          else "mv_hpa_monthly_2022_2023_printed_bar_labels_v1"),
                    "Geography": "Maldives", "GeographyKey": "country:MV:national",
                    "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["pdf_url"],
                    "SourceListingURL": report["url"], "SourceReportYear": str(year),
                    "SourceReportMonth": str(month), "SourcePeriodStart": date(year, n, 1).isoformat(),
                    "SourcePeriodEnd": end, "SourcePage": "2", "RawSHA256": hashlib.sha256(payload).hexdigest(),
                    "DatasetStatus": "provisional", "IsProvisional": "true",
                    "PublicReleaseEnabled": "false", "License": "reuse_terms_unverified",
                    "Dimensions": json.dumps({"source_category": label, "extraction": "printed_vector_bar_label", "figure": "Figure 2"}, sort_keys=True),
                })
    if not rows:
        raise MaldivesContractError("No verified current-year printed case counts")
    return rows, {"year": year, "month": month, "observations": len(rows), "skipped_panels": skipped,
                  "coverage_complete": False, "unparsed": ["Atoll incidence rates", "historical comparison lines", "weekly SIDAS reports"]}
