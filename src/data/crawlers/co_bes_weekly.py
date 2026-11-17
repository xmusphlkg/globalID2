"""Strict proof of BES national last-week low-frequency notification counts.

The Observado column is usable ONLY in this named body table when its own
methodology explicitly defines the analyzed week excluding discarded records.
This contract never applies to the territorial annex or other Observado columns.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import timedelta

from src.data.crawlers.co_bes import BESContractError, BESReport, inspect_pdf

PARSER_VERSION = "co-ins-bes-national-last-week-notifications-v2"
EVENTS = {
    "leishmaniasis cutanea": ("420", "cases"),
    "leishmaniasis mucosa": ("430", "cases"),
    "zika": ("895", "cases"),
    "chicungunya": ("217", "cases"),
    "chikungunya": ("217", "cases"),
    "chikunguna": ("217", "cases"),
    "chicunguya": ("217", "cases"),
    "mortalidad por dengue": ("580", "deaths"),
    "lepra": ("450", "cases"),
    "fiebre tifoidea y paratifoidea": ("320", "cases"),
}


def plain(text):
    return " ".join(unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().split())


def page_weekly_table(text: str, *, year: int, week: int) -> list[dict]:
    """Require table identity AND local methodology, not a global column rule."""
    normalized = plain(text)
    method = ("los casos observados corresponden a la notificacion de la semana analizada "
              "exceptuando los casos descartados")
    if method not in normalized:
        return []
    title = re.search(r"tabla \d+\. comparacion de casos notificados de eventos priorizados,? "
                      r"de baja frecuencia,? segun .*?colombia,? a (?:la )?semana epidemiologica "
                      r"(\d{1,2}) (?:de|del) (20\d{2})", normalized)
    if not title or (int(title[2]), int(title[1])) != (year, week):
        return []
    # Isolate this named table, ending at its own source footnote. Other tables
    # on the same page can use a different Observado window.
    raw_title = re.search(r"Tabla\s+\d+\.\s+Comparaci[oó]n de casos notificados de eventos priorizados",
                          text, re.IGNORECASE)
    if raw_title is None:
        return []
    block = re.split(r"Fuente\s*:", text[raw_title.end():], maxsplit=1, flags=re.IGNORECASE)[0]
    rows = []
    for line in block.splitlines():
        match = re.fullmatch(r"\s*(.+?)\s+(\d+)\s+(\d+(?:[,.]\d+)?)\s+([<>]?\d+[,.]\d+)\s*", line)
        if match is None:
            continue
        event = EVENTS.get(plain(match[1]))
        # Pertussis appears in a few issues only. It is outside this seven-event
        # contract and must not make otherwise valid reviewed rows disappear.
        if plain(match[1]) == "tos ferina":
            continue
        if event is None:
            raise BESContractError("Unreviewed event in national last-week table")
        rows.append({"event_code": event[0], "metric": event[1], "event_label_raw": match[1].strip(),
                     "value": int(match[2]), "expected_raw": match[3], "p_raw": match[4],
                     "source_row_raw": line.strip(), "definition": method})
    codes = {r["event_code"] for r in rows}
    # W39-40 2026 omit dengue mortality in the original. Preserve that absence
    # explicitly, never manufacture zero or discard the six reported events.
    if codes == {"217", "320", "420", "430", "450", "895"} and len(rows) == 6:
        rows.append({"event_code": "580", "metric": "deaths", "event_label_raw": "Mortalidad por dengue",
                     "value": None, "expected_raw": "", "p_raw": "", "source_row_raw": "",
                     "definition": method, "missing_reason": "event_absent_from_source_table"})
    elif len(rows) != 7 or len(codes) != 7:
        raise BESContractError("National last-week table is incomplete or changed")
    return rows


def parse_weekly_notifications(payload: bytes, report: BESReport) -> tuple[list[dict], dict]:
    import pypdfium2 as pdfium

    cover = inspect_pdf(payload)
    if report.listed_year != cover.year or cover.period_end is None:
        raise BESContractError("National weekly point needs a verified report period")
    digest = hashlib.sha256(payload).hexdigest()
    observations = []
    with pdfium.PdfDocument(payload) as document:
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
                page.close()
            for row in page_weekly_table(text, year=cover.year, week=cover.week):
                observations.append(row | {
                    "source_epi_year": cover.year, "source_epi_week": cover.week,
                    "period_start": (cover.period_end - timedelta(days=6)).isoformat(),
                    "period_end": cover.period_end.isoformat(), "calendar": "INS_source_epi_week",
                    "value_kind": "interval_count", "reporting_basis": "weekly_notifications_excluding_discarded",
                    "geography_key": "country:CO:national", "source_url": report.pdf_url,
                    "pdf_sha256": digest, "page": index + 1, "parser_version": PARSER_VERSION,
                    "provisional": True, "public_release_enabled": False,
                })
    if len(observations) != 7:
        raise BESContractError("Exactly one verified national last-week table required")
    return observations, {"source_url": report.pdf_url, "sha256": digest,
                          "year": cover.year, "week": cover.week, "points": len(observations),
                          "scope": "seven_prioritized_low_frequency_events_only",
                          "annex_observed_used": False, "cumulative_differencing_used": False}


def ingestion_rows(observations: list[dict]) -> list[dict]:
    """Translate verified points to the common source ledger contract."""
    rows = []
    for point in observations:
        rows.append({
            "Date": point["period_start"], "Year": str(point["source_epi_year"]),
            "Month": point["period_start"][5:7], "DiseaseCode": "__source_native__",
            "SourceDiseaseCode": "bes_weekly_" + point["event_code"],
            "RawDiseaseLabel": point["event_label_raw"],
            "Cases": "" if point["value"] is None else str(point["value"]),
            "MissingValueStatus": "source_missing" if point["value"] is None else "reported",
            "Measure": "death_notifications" if point["metric"] == "deaths" else "case_notifications",
            "Unit": "count", "Frequency": "weekly", "PeriodType": "weekly",
            "PeriodStart": point["period_start"], "PeriodEnd": point["period_end"],
            "Calendar": point["calendar"], "ObservationKind": "interval_count",
            "ReportingBasis": point["reporting_basis"],
            "DefinitionVersion": "co_bes_explicit_weekly_notifications_v1",
            "TimeBasis": "source_verified_week_boundaries", "Geography": "Colombia",
            "GeographyKey": point["geography_key"], "Source": "Colombia INS Weekly Epidemiological Bulletin (BES)",
            "SourceURL": point["source_url"], "RawSHA256": point["pdf_sha256"],
            "SourceTablePage": point["page"], "SourceTableColumn": "Observado",
            "SourceRowRaw": point["source_row_raw"], "SourceExpectedRaw": point["expected_raw"],
            "SourcePRaw": point["p_raw"], "SourceCaseDefinition": point["definition"],
            "ParserVersion": PARSER_VERSION, "IsProvisional": "true",
            "AuthoritativeRevision": "true", "PublicReleaseEnabled": "true",
            "Dimensions": json.dumps({"source_epi_year": point["source_epi_year"],
                                      "source_epi_week": point["source_epi_week"],
                                      "calendar": point["calendar"]}, sort_keys=True),
        })
    return rows
