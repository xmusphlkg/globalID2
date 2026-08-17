"""Iceland Directorate of Health public surveillance dashboard crawler.

The Directorate currently publishes three complementary Power BI reports:

* selected infectious diseases at annual grain;
* STI diagnoses at monthly fact grain (the report is refreshed quarterly);
* respiratory diagnoses at ISO-week grain.

Only national, all-demographic counts are queried here.  The reports expose
additional demographic dimensions, rates, hospital activity, tests and
vaccination measures; those are intentionally not folded into the case-count
contract because they have different denominators or reporting bases.
"""

from __future__ import annotations

import csv
import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config

from .base import BaseCrawler, CrawlerResult
from .powerbi_public import (
    PowerBIQueryResult,
    PowerBIReportContext,
    PublicPowerBIClient,
)

logger = get_logger(__name__)

DEFAULT_SOURCE_PAGE_URL = "https://island.is/en/smitsjukdomar-tolur"
DEFAULT_RESPIRATORY_PAGE_URL = "https://island.is/en/respiratory-tract-infections"
DEFAULT_ANNUAL_VIEW_URL = (
    "https://app.powerbi.com/view?"
    "r=eyJrIjoiY2Q4Mjk2NDQtNDA1MS00YTcxLTk1NzEtZTBlZDYwMTU3ZDNiIiwidCI6"
    "IjRkNzYyYWMwLTYyMDUtNDJjZS1iOTY0LWMzYjg5NThmZDRhOSIsImMiOjh9"
)
DEFAULT_STI_VIEW_URL = (
    "https://app.powerbi.com/view?"
    "r=eyJrIjoiNTNmN2ViYTEtZjdiZi00MmRkLWFjYWQtOWI0ZmEwNjhjYmQyIiwidCI6"
    "IjRkNzYyYWMwLTYyMDUtNDJjZS1iOTY0LWMzYjg5NThmZDRhOSIsImMiOjh9"
)
DEFAULT_RESPIRATORY_VIEW_URL = (
    "https://app.powerbi.com/view?"
    "r=eyJrIjoiZjgyOWI0YzgtNjNkZC00Y2QzLTllMzctMWIxMTAxZThlMDJkIiwidCI6"
    "IjRkNzYyYWMwLTYyMDUtNDJjZS1iOTY0LWMzYjg5NThmZDRhOSIsImMiOjh9"
)

SOURCE_SCOPE_ANNUAL = "is_doh_annual"
SOURCE_SCOPE_STI = "is_doh_sti"
SOURCE_SCOPE_RESPIRATORY = "is_doh_respiratory"
SUPPORTED_SOURCE_SCOPES = (
    SOURCE_SCOPE_ANNUAL,
    SOURCE_SCOPE_STI,
    SOURCE_SCOPE_RESPIRATORY,
)

ANNUAL_SOURCE_NAME = "Iceland Directorate of Health Annual Dashboard"
STI_SOURCE_NAME = "Iceland Directorate of Health STI Dashboard"
RESPIRATORY_SOURCE_NAME = "Iceland Directorate of Health Respiratory Dashboard"

SOURCE_IDS = {
    SOURCE_SCOPE_ANNUAL: "SRC_IS_DOH_ANNUAL",
    SOURCE_SCOPE_STI: "SRC_IS_DOH_STI",
    SOURCE_SCOPE_RESPIRATORY: "SRC_IS_DOH_RESPIRATORY",
}
SOURCE_NAMES = {
    SOURCE_SCOPE_ANNUAL: ANNUAL_SOURCE_NAME,
    SOURCE_SCOPE_STI: STI_SOURCE_NAME,
    SOURCE_SCOPE_RESPIRATORY: RESPIRATORY_SOURCE_NAME,
}


@dataclass(frozen=True)
class ISSeriesDefinition:
    source_scope: str
    source_id: str
    source_name: str
    entity: str
    raw_disease_label: str
    disease_code: str
    frequency: str
    period_type: str
    measure: str
    reporting_basis: str
    unit: str = "count"
    population_scope: str = "national_all_residents"


def _series(
    scope: str,
    entity: str,
    raw_label: str,
    code: str,
    frequency: str,
    period_type: str,
    reporting_basis: str,
    *,
    measure: str = "case_notifications",
    population_scope: str = "national_all_residents",
) -> ISSeriesDefinition:
    return ISSeriesDefinition(
        source_scope=scope,
        source_id=SOURCE_IDS[scope],
        source_name=SOURCE_NAMES[scope],
        entity=entity,
        raw_disease_label=raw_label,
        disease_code=code,
        frequency=frequency,
        period_type=period_type,
        measure=measure,
        reporting_basis=reporting_basis,
        population_scope=population_scope,
    )


SERIES_DEFINITIONS: Tuple[ISSeriesDefinition, ...] = (
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Matarsýkingar",
        "Giardíusýking",
        "annual:gastrointestinal:giardiasis:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Matarsýkingar",
        "Kampýlóbaktersýking",
        "annual:gastrointestinal:campylobacteriosis:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Matarsýkingar",
        "Salmonellusýking",
        "annual:gastrointestinal:salmonellosis:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Kynsjúkdómar",
        "HIV sýking",
        "annual:sti:hiv-infection:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Kynsjúkdómar",
        "Klamydíusýking",
        "annual:sti:chlamydia:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Kynsjúkdómar",
        "Lekandi",
        "annual:sti:gonorrhoea:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Kynsjúkdómar",
        "Sárasótt",
        "annual:sti:syphilis:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Lifrarbólgur",
        "Lifrarbólga B",
        "annual:hepatitis:hepatitis-b:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Lifrarbólgur",
        "Lifrarbólga C",
        "annual:hepatitis:hepatitis-c:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Ónæmir sýklar",
        "Breiðvirkir beta laktamasar (ESBL/AmpC)",
        "annual:antimicrobial-resistance:esbl-ampc:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Ónæmir sýklar",
        "Meticillín ónæmur staph. aureus (MÓSA)",
        "annual:antimicrobial-resistance:mrsa:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Öndunarfærasýkingar",
        "COVID-19",
        "annual:respiratory:covid-19:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Öndunarfærasýkingar",
        "Inflúensa",
        "annual:respiratory:influenza:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_ANNUAL,
        "Öndunarfærasýkingar",
        "Pneumókokkasýking, ífarandi",
        "annual:respiratory:invasive-pneumococcal-disease:cases",
        "annual",
        "year",
        "registry_and_laboratory_surveillance",
    ),
    _series(
        SOURCE_SCOPE_STI,
        "Kynsjúkdómar",
        "Klamydía",
        "sti:chlamydia:monthly-diagnoses",
        "monthly",
        "month",
        "laboratory_and_registry_diagnoses",
    ),
    _series(
        SOURCE_SCOPE_STI,
        "Kynsjúkdómar",
        "Lekandi",
        "sti:gonorrhoea:monthly-diagnoses",
        "monthly",
        "month",
        "laboratory_and_registry_diagnoses",
    ),
    _series(
        SOURCE_SCOPE_STI,
        "Kynsjúkdómar",
        "Sárasótt",
        "sti:syphilis:monthly-diagnoses",
        "monthly",
        "month",
        "laboratory_and_registry_diagnoses",
    ),
    _series(
        SOURCE_SCOPE_RESPIRATORY,
        "Sýkingar",
        "Covid",
        "respiratory:covid-19:weekly-diagnoses",
        "weekly",
        "iso_week",
        "laboratory_reporting_catchment",
        measure="laboratory_diagnoses",
        population_scope="national_reporting_catchment",
    ),
    _series(
        SOURCE_SCOPE_RESPIRATORY,
        "Sýkingar",
        "inflúensa",
        "respiratory:influenza:weekly-diagnoses",
        "weekly",
        "iso_week",
        "laboratory_reporting_catchment",
        measure="laboratory_diagnoses",
        population_scope="national_reporting_catchment",
    ),
    _series(
        SOURCE_SCOPE_RESPIRATORY,
        "Sýkingar",
        "RSV",
        "respiratory:rsv:weekly-diagnoses",
        "weekly",
        "iso_week",
        "laboratory_reporting_catchment",
        measure="laboratory_diagnoses",
        population_scope="national_reporting_catchment",
    ),
    _series(
        SOURCE_SCOPE_RESPIRATORY,
        "Sýkingar",
        "kíghósti",
        "respiratory:pertussis:weekly-diagnoses",
        "weekly",
        "iso_week",
        "source_reported_respiratory_diagnoses",
        measure="reported_diagnoses",
        population_scope="national_reporting_catchment",
    ),
    _series(
        SOURCE_SCOPE_RESPIRATORY,
        "Sýkingar",
        "mycoplasma",
        "respiratory:mycoplasma:weekly-diagnoses",
        "weekly",
        "iso_week",
        "physician_reported_clinical_diagnoses",
        measure="clinical_diagnoses",
        population_scope="national_reporting_catchment",
    ),
)

_SERIES_BY_KEY = {
    (definition.source_scope, definition.entity, definition.raw_disease_label): definition
    for definition in SERIES_DEFINITIONS
}

_ANNUAL_ENTITIES = (
    "Matarsýkingar",
    "Kynsjúkdómar",
    "Lifrarbólgur",
    "Ónæmir sýklar",
    "Öndunarfærasýkingar",
)
_SOURCE_LINK_RE = re.compile(
    r'<a\b[^>]*href=["\'](?P<url>https://app\.powerbi\.com/view\?[^"\']+)'
    r'[^>]*>(?P<label>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

CSV_FIELDNAMES = [
    "",
    "Disease",
    "DiseaseCode",
    "SourceSeriesCode",
    "Year",
    "Month",
    "ISOYear",
    "ISOWeek",
    "Date",
    "PeriodType",
    "PeriodValue",
    "Cases",
    "CountryCode",
    "GeographyKey",
    "Dimensions",
    "Frequency",
    "Measure",
    "ReportingBasis",
    "Unit",
    "PopulationScope",
    "DatasetStatus",
    "AuthoritativeRevision",
    "SourceScope",
    "SourceId",
    "Source",
    "SourceURL",
    "SourcePageURL",
    "SourceLastRefresh",
    "RetrievedAt",
    "ResourceKey",
    "ModelId",
    "DatasetId",
    "ReportId",
    "SchemaFingerprint",
    "RawArtifact",
]


@dataclass
class ISFetchSummary:
    row_count: int
    latest_date: Optional[date]
    source_row_counts: Dict[str, int]
    source_last_refresh: Dict[str, Optional[str]]
    schema_fingerprints: Dict[str, str]
    source_url: str


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_year(value: object) -> int:
    try:
        year = int(float(_norm_text(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Iceland surveillance year: {value!r}") from exc
    if not 1900 <= year <= 2100:
        raise ValueError(f"Iceland surveillance year out of range: {year}")
    return year


def _parse_count(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Boolean is not a valid Iceland case count: {value!r}")
    try:
        numeric = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Iceland case count: {value!r}") from exc
    if not numeric.is_integer() or numeric < 0:
        raise ValueError(f"Iceland case count is not a non-negative integer: {value!r}")
    return int(numeric)


def _strip_tags(value: str) -> str:
    return _norm_text(html_lib.unescape(re.sub(r"<[^>]+>", " ", value)))


def _normalize_scopes(scopes: Optional[Iterable[str]]) -> Tuple[str, ...]:
    if isinstance(scopes, str):
        raw_values: Iterable[str] = (scopes,)
    else:
        raw_values = scopes or SUPPORTED_SOURCE_SCOPES
    values = tuple(_norm_text(value).lower() for value in raw_values)
    if not values or "all" in values:
        return SUPPORTED_SOURCE_SCOPES
    unknown = sorted(set(values) - set(SUPPORTED_SOURCE_SCOPES))
    if unknown:
        raise ValueError(f"Unsupported Iceland source scope(s): {', '.join(unknown)}")
    return tuple(scope for scope in SUPPORTED_SOURCE_SCOPES if scope in values)


class IcelandDOHCrawler(BaseCrawler):
    """Fetch current national Iceland surveillance series from public reports."""

    SOURCE_URL = DEFAULT_SOURCE_PAGE_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        powerbi_client: Optional[PublicPowerBIClient] = None,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; IS-DOH)",
            timeout=120,
            max_retries=3,
            delay=0.1,
        )
        cfg = get_country_bootstrap_config("IS")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        self.source_page_url = str(
            crawler_cfg.get("landing_url")
            or crawler_cfg.get("source_page_url")
            or DEFAULT_SOURCE_PAGE_URL
        )
        self.source_page_urls = {
            SOURCE_SCOPE_ANNUAL: self.source_page_url,
            SOURCE_SCOPE_STI: self.source_page_url,
            SOURCE_SCOPE_RESPIRATORY: str(
                crawler_cfg.get("respiratory_landing_url")
                or DEFAULT_RESPIRATORY_PAGE_URL
            ),
        }
        self.configured_view_urls = {
            SOURCE_SCOPE_ANNUAL: str(
                crawler_cfg.get("annual_dashboard_url")
                or crawler_cfg.get("annual_powerbi_url")
                or DEFAULT_ANNUAL_VIEW_URL
            ),
            SOURCE_SCOPE_STI: str(
                crawler_cfg.get("sti_dashboard_url")
                or crawler_cfg.get("sti_powerbi_url")
                or DEFAULT_STI_VIEW_URL
            ),
            SOURCE_SCOPE_RESPIRATORY: str(
                crawler_cfg.get("respiratory_dashboard_url")
                or crawler_cfg.get("respiratory_powerbi_url")
                or DEFAULT_RESPIRATORY_VIEW_URL
            ),
        }
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/is")
        self.powerbi = powerbi_client or PublicPowerBIClient(
            session=self.session,
            timeout=120,
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; IS-DOH)",
        )

    def discover_view_urls(self) -> Dict[str, str]:
        """Discover current report links from the Directorate's canonical page."""

        discovered: Dict[str, str] = {}
        try:
            page = self.get(self.source_page_url)
            for match in _SOURCE_LINK_RE.finditer(page.text):
                label = _strip_tags(match.group("label")).casefold()
                url = html_lib.unescape(match.group("url"))
                if "annual incidence" in label:
                    discovered[SOURCE_SCOPE_ANNUAL] = url
                elif all(word in label for word in ("gonorrhoea", "syphilis", "chlamydia")):
                    discovered[SOURCE_SCOPE_STI] = url
                elif "respiratory tract infections" in label:
                    discovered[SOURCE_SCOPE_RESPIRATORY] = url
        except Exception as exc:
            logger.warning(
                f"[IS-DOH] Official source-page discovery failed; using configured links | {exc}"
            )

        resolved = dict(self.configured_view_urls)
        resolved.update(discovered)
        return resolved

    @staticmethod
    def _definition(scope: str, entity: str, raw_label: object) -> ISSeriesDefinition:
        label = _norm_text(raw_label)
        definition = _SERIES_BY_KEY.get((scope, entity, label))
        if definition is None:
            if not label or scope not in SUPPORTED_SOURCE_SCOPES:
                raise RuntimeError(
                    "Invalid Iceland source series identity: "
                    f"scope={scope} entity={entity} label={label!r}"
                )
            frequency, period_type = {
                SOURCE_SCOPE_ANNUAL: ("annual", "year"),
                SOURCE_SCOPE_STI: ("monthly", "month"),
                SOURCE_SCOPE_RESPIRATORY: ("weekly", "iso_week"),
            }[scope]
            digest = hashlib.sha256(
                f"{scope}\x1f{entity}\x1f{label}".encode("utf-8")
            ).hexdigest()[:20]
            definition = ISSeriesDefinition(
                source_scope=scope,
                source_id=SOURCE_IDS[scope],
                source_name=SOURCE_NAMES[scope],
                entity=entity,
                raw_disease_label=label,
                disease_code=f"source-native:{digest}",
                frequency=frequency,
                period_type=period_type,
                measure="case_notifications",
                reporting_basis="source_reported_surveillance",
            )
            logger.warning(
                "Iceland source published an unregistered disease category; "
                "retaining it for mapping review | scope={} entity={} label={} code={}",
                scope,
                entity,
                label,
                definition.disease_code,
            )
        return definition

    def _base_row(
        self,
        definition: ISSeriesDefinition,
        *,
        report_date: date,
        period_value: str,
        cases: int,
        context: PowerBIReportContext,
        retrieved_at: str,
    ) -> Dict[str, str]:
        iso = report_date.isocalendar()
        return {
            "Date": report_date.isoformat(),
            "RawDiseaseLabel": definition.raw_disease_label,
            "DiseaseCode": definition.disease_code,
            "SourceSeriesCode": definition.disease_code,
            "Year": str(report_date.year),
            "Month": str(report_date.month),
            "ISOYear": str(iso.year) if definition.frequency == "weekly" else "",
            "ISOWeek": str(iso.week) if definition.frequency == "weekly" else "",
            "PeriodType": definition.period_type,
            "PeriodValue": period_value,
            "Cases": str(cases),
            "CountryCode": "IS",
            "GeographyKey": "country:IS:national",
            "Dimensions": "{}",
            "Frequency": definition.frequency,
            "Measure": definition.measure,
            "ReportingBasis": definition.reporting_basis,
            "Unit": definition.unit,
            "PopulationScope": definition.population_scope,
            "DatasetStatus": "provisional_revised",
            "IsProvisional": "true",
            "AuthoritativeRevision": "true",
            "SourceScope": definition.source_scope,
            "SourceId": definition.source_id,
            "Source": definition.source_name,
            "SourceURL": context.view_url,
            "SourcePageURL": self.source_page_urls.get(
                definition.source_scope, self.source_page_url
            ),
            "SourceLastRefresh": context.last_refresh or "",
            "RetrievedAt": retrieved_at,
            "ResourceKey": context.resource_key,
            "ModelId": str(context.model_id),
            "DatasetId": context.dataset_id,
            "ReportId": context.report_id,
            "SchemaFingerprint": context.schema_fingerprint,
            "RawArtifact": "",
        }

    def _fetch_annual(
        self,
        context: PowerBIReportContext,
        retrieved_at: str,
    ) -> Tuple[List[Dict[str, str]], List[Tuple[str, PowerBIQueryResult]]]:
        rows: List[Dict[str, str]] = []
        queries: List[Tuple[str, PowerBIQueryResult]] = []
        for entity in _ANNUAL_ENTITIES:
            result = self.powerbi.query_entity_sum(
                context,
                entity=entity,
                group_columns=("AR", "SJUKDOMUR"),
                value_column="FJOLDI",
            )
            queries.append((entity, result))
            for source_row in result.rows:
                definition = self._definition(
                    SOURCE_SCOPE_ANNUAL,
                    entity,
                    source_row.get(f"{entity}.SJUKDOMUR"),
                )
                year = _parse_year(source_row.get(f"{entity}.AR"))
                count = _parse_count(source_row.get(f"Sum({entity}.FJOLDI)"))
                rows.append(
                    self._base_row(
                        definition,
                        report_date=date(year, 1, 1),
                        period_value=str(year),
                        cases=count,
                        context=context,
                        retrieved_at=retrieved_at,
                    )
                )
        return rows, queries

    def _fetch_sti(
        self,
        context: PowerBIReportContext,
        retrieved_at: str,
    ) -> Tuple[List[Dict[str, str]], List[Tuple[str, PowerBIQueryResult]]]:
        entity = "Kynsjúkdómar"
        result = self.powerbi.query_entity_sum(
            context,
            entity=entity,
            group_columns=("AR", "MAN", "SJUKDOMUR"),
            value_column="FJOLDI",
        )
        rows: List[Dict[str, str]] = []
        for source_row in result.rows:
            definition = self._definition(
                SOURCE_SCOPE_STI,
                entity,
                source_row.get(f"{entity}.SJUKDOMUR"),
            )
            year = _parse_year(source_row.get(f"{entity}.AR"))
            try:
                month = int(float(_norm_text(source_row.get(f"{entity}.MAN"))))
                report_date = date(year, month, 1)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid Iceland STI month: {source_row.get(f'{entity}.MAN')!r}"
                ) from exc
            count = _parse_count(source_row.get(f"Sum({entity}.FJOLDI)"))
            rows.append(
                self._base_row(
                    definition,
                    report_date=report_date,
                    period_value=f"{year}{month:02d}",
                    cases=count,
                    context=context,
                    retrieved_at=retrieved_at,
                )
            )
        return rows, [(entity, result)]

    def _fetch_respiratory(
        self,
        context: PowerBIReportContext,
        retrieved_at: str,
    ) -> Tuple[List[Dict[str, str]], List[Tuple[str, PowerBIQueryResult]]]:
        entity = "Sýkingar"
        result = self.powerbi.query_entity_sum(
            context,
            entity=entity,
            group_columns=("ISO_AR", "VIKUNUMER_ISO", "SJUKDOMUR"),
            value_column="FJOLDI",
        )
        rows: List[Dict[str, str]] = []
        for source_row in result.rows:
            definition = self._definition(
                SOURCE_SCOPE_RESPIRATORY,
                entity,
                source_row.get(f"{entity}.SJUKDOMUR"),
            )
            iso_year = _parse_year(source_row.get(f"{entity}.ISO_AR"))
            try:
                iso_week = int(float(_norm_text(source_row.get(f"{entity}.VIKUNUMER_ISO"))))
                report_date = date.fromisocalendar(iso_year, iso_week, 1)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Invalid Iceland respiratory ISO week: "
                    f"{iso_year}-W{source_row.get(f'{entity}.VIKUNUMER_ISO')!r}"
                ) from exc
            count = _parse_count(source_row.get(f"Sum({entity}.FJOLDI)"))
            row = self._base_row(
                definition,
                report_date=report_date,
                period_value=f"{iso_year}{iso_week:02d}",
                cases=count,
                context=context,
                retrieved_at=retrieved_at,
            )
            row["ISOYear"] = str(iso_year)
            row["ISOWeek"] = str(iso_week)
            rows.append(row)
        return rows, [(entity, result)]

    @staticmethod
    def _validate_scope_rows(scope: str, rows: Sequence[Mapping[str, str]]) -> None:
        if not rows:
            raise RuntimeError(f"Iceland source produced no rows: {scope}")
        expected = {
            definition.disease_code
            for definition in SERIES_DEFINITIONS
            if definition.source_scope == scope
        }
        observed = {str(row.get("DiseaseCode") or "") for row in rows}
        missing = sorted(expected - observed)
        unknown = sorted(observed - expected)
        if missing:
            raise RuntimeError(
                f"Iceland series coverage drift for {scope}; missing={missing} unknown={unknown}"
            )
        if unknown:
            logger.warning(
                "Iceland source coverage includes new categories retained for review | "
                "scope={} codes={}",
                scope,
                unknown,
            )

        seen: Dict[Tuple[str, str], str] = {}
        for row in rows:
            key = (str(row.get("Date") or ""), str(row.get("DiseaseCode") or ""))
            cases = str(row.get("Cases") or "")
            previous = seen.get(key)
            if previous is not None:
                raise RuntimeError(
                    f"Duplicate Iceland source observation: scope={scope} key={key}"
                )
            seen[key] = cases

    def _archive_scope(
        self,
        *,
        scope: str,
        context: PowerBIReportContext,
        queries: Sequence[Tuple[str, PowerBIQueryResult]],
        retrieved_at: str,
    ) -> Path:
        timestamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        run_dir = (
            self.raw_dir
            / scope
            / timestamp.strftime("%Y")
            / timestamp.strftime("%m")
            / timestamp.strftime("%d")
            / timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        )
        run_dir.mkdir(parents=True, exist_ok=False)

        artifacts: List[Dict[str, str]] = []

        def save_bytes(filename: str, content: bytes) -> None:
            path = run_dir / filename
            path.write_bytes(content)
            artifacts.append(
                {
                    "file": filename,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": str(len(content)),
                }
            )

        save_bytes("report.html", context.landing_html.encode("utf-8"))
        save_bytes(
            "models-and-exploration.json",
            json.dumps(
                context.models_payload, ensure_ascii=False, sort_keys=True, indent=2
            ).encode("utf-8"),
        )
        save_bytes(
            "conceptual-schema.json",
            json.dumps(
                context.schema_payload, ensure_ascii=False, sort_keys=True, indent=2
            ).encode("utf-8"),
        )
        for index, (entity, query) in enumerate(queries, start=1):
            safe_entity = re.sub(r"[^a-z0-9]+", "-", entity.casefold()).strip("-")
            prefix = f"query-{index:02d}-{safe_entity or 'entity'}"
            save_bytes(
                f"{prefix}-request.json",
                json.dumps(
                    query.request_payload, ensure_ascii=False, sort_keys=True, indent=2
                ).encode("utf-8"),
            )
            save_bytes(
                f"{prefix}-response.json",
                json.dumps(
                    query.response_payload, ensure_ascii=False, sort_keys=True, indent=2
                ).encode("utf-8"),
            )

        manifest = {
            "country_code": "IS",
            "source_scope": scope,
            "source_id": SOURCE_IDS[scope],
            "source_url": context.view_url,
            "source_page_url": self.source_page_urls.get(scope, self.source_page_url),
            "retrieved_at": retrieved_at,
            "resource_key": context.resource_key,
            "model_id": context.model_id,
            "dataset_id": context.dataset_id,
            "report_id": context.report_id,
            "source_last_refresh": context.last_refresh,
            "schema_fingerprint": context.schema_fingerprint,
            "artifacts": artifacts,
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return manifest_path

    @staticmethod
    def write_rows(output_csv: Path, rows: Sequence[Mapping[str, str]]) -> None:
        """Atomically replace the normalized current snapshot."""

        output_csv.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_csv.with_name(output_csv.name + ".tmp")
        ordered = sorted(
            rows,
            key=lambda row: (
                str(row.get("Date") or ""),
                str(row.get("SourceScope") or ""),
                str(row.get("DiseaseCode") or ""),
            ),
        )
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for index, row in enumerate(ordered, start=1):
                writer.writerow(
                    {
                        "": str(index),
                        "Disease": row.get("RawDiseaseLabel", ""),
                        **{
                            field: row.get(field, "")
                            for field in CSV_FIELDNAMES
                            if field not in {"", "Disease"}
                        },
                    }
                )
        temporary.replace(output_csv)

    def crawl_national(
        self,
        output_csv: Path,
        *,
        source_scopes: Optional[Iterable[str]] = None,
    ) -> ISFetchSummary:
        """Fetch selected current sources and write one normalized snapshot."""

        scopes = _normalize_scopes(source_scopes)
        view_urls = self.discover_view_urls()
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        all_rows: List[Dict[str, str]] = []
        counts: Dict[str, int] = {}
        refreshes: Dict[str, Optional[str]] = {}
        fingerprints: Dict[str, str] = {}

        for scope in scopes:
            context = self.powerbi.discover(view_urls[scope])
            if scope == SOURCE_SCOPE_ANNUAL:
                rows, queries = self._fetch_annual(context, retrieved_at)
            elif scope == SOURCE_SCOPE_STI:
                rows, queries = self._fetch_sti(context, retrieved_at)
            else:
                rows, queries = self._fetch_respiratory(context, retrieved_at)
            self._validate_scope_rows(scope, rows)

            if self.save_raw:
                manifest_path = self._archive_scope(
                    scope=scope,
                    context=context,
                    queries=queries,
                    retrieved_at=retrieved_at,
                )
                for row in rows:
                    row["RawArtifact"] = str(manifest_path)

            counts[scope] = len(rows)
            refreshes[scope] = context.last_refresh
            fingerprints[scope] = context.schema_fingerprint
            all_rows.extend(rows)
            logger.info(
                f"[IS-DOH] Source fetched | scope={scope} rows={len(rows)} "
                f"model={context.model_id} refresh={context.last_refresh}"
            )

        self.write_rows(output_csv, all_rows)
        latest = max(
            (date.fromisoformat(row["Date"]) for row in all_rows),
            default=None,
        )
        return ISFetchSummary(
            row_count=len(all_rows),
            latest_date=latest,
            source_row_counts=counts,
            source_last_refresh=refreshes,
            schema_fingerprints=fingerprints,
            source_url=self.source_page_url,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/is/iceland_doh_current.csv"
        )
        raw_scopes = kwargs.get("source_scopes")
        if raw_scopes is None and kwargs.get("source"):
            raw_scopes = [kwargs["source"]]
        summary = self.crawl_national(output_csv, source_scopes=raw_scopes)
        return [
            CrawlerResult(
                title="Iceland Directorate of Health surveillance dashboards",
                url=self.source_page_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "country_code": "IS",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "source_row_counts": summary.source_row_counts,
                    "source_last_refresh": summary.source_last_refresh,
                    "schema_fingerprints": summary.schema_fingerprints,
                },
            )
        ]

    def parse(self, response: Any) -> List[CrawlerResult]:
        """BaseCrawler contract; parsing is integrated in ``crawl_national``."""

        return []


__all__ = [
    "ANNUAL_SOURCE_NAME",
    "CSV_FIELDNAMES",
    "DEFAULT_ANNUAL_VIEW_URL",
    "DEFAULT_RESPIRATORY_VIEW_URL",
    "DEFAULT_SOURCE_PAGE_URL",
    "DEFAULT_STI_VIEW_URL",
    "ISFetchSummary",
    "ISSeriesDefinition",
    "IcelandDOHCrawler",
    "RESPIRATORY_SOURCE_NAME",
    "SERIES_DEFINITIONS",
    "SOURCE_IDS",
    "SOURCE_NAMES",
    "SOURCE_SCOPE_ANNUAL",
    "SOURCE_SCOPE_RESPIRATORY",
    "SOURCE_SCOPE_STI",
    "STI_SOURCE_NAME",
    "SUPPORTED_SOURCE_SCOPES",
]
