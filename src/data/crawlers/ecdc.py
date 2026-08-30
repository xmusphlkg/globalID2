"""ECDC Surveillance Atlas annual country baselines.

The Atlas exposes aggregate EU/EEA surveillance data through the same REST
contract used by its public interface.  This adapter discovers measure ids on
every run, but keeps the epidemiological population and canonical mapping
contract explicit so a dashboard reconfiguration cannot silently change the
meaning of a series.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlencode

from .base import BaseCrawler, CrawlerResult
from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRY_CODES, source_geo_code

DEFAULT_SCOPE = "ecdc_atlas_annual"
DEFAULT_SOURCE_NAME = "ECDC Surveillance Atlas of Infectious Diseases"
ONTOLOGY_SOURCE_ID_TEMPLATE = "SRC_{country}_ECDC_ATLAS"
ATLAS_URL = "https://atlas.ecdc.europa.eu/public/index.aspx/"
API_BASE = "https://atlas.ecdc.europa.eu/public/AtlasService/rest/"
DATASET_ID = 27
DATASET_CODE = "CURRENT.GENERAL"
CONTRACT_VERSION = "ecdc-atlas-current-general-annual-v1-observed-2026-08"
ATTRIBUTION = "Data provided by ECDC based on data reported by EU/EEA Member States."
REUSE_TERMS_URL = "https://www.ecdc.europa.eu/en/publications-data/access-eueea-surveillance-data-third-parties"
TARGET_COUNTRIES = ECDC_BASELINE_COUNTRY_CODES


class ECDCContractError(ValueError):
    """Raised when the public Atlas no longer matches the reviewed contract."""


@dataclass(frozen=True)
class ECDCTopicContract:
    code: str
    label: str
    populations: tuple[str, ...]
    concept_id: Optional[str]
    mapping_relation: str = "exact"
    local_code: Optional[str] = None

    @property
    def source_code(self) -> str:
        return self.local_code or self.code.casefold()


@dataclass(frozen=True)
class ECDCFetchSummary:
    row_count: int
    series_count: int
    latest_date: Optional[date]
    first_date: Optional[date]


# Hospital-event, AMR, influenza-detection and RSV-detection datasets are
# intentionally excluded: they are not national reported-case counts.
TOPIC_CONTRACTS: tuple[ECDCTopicContract, ...] = (
    ECDCTopicContract("ANTH", "Anthrax", ("Confirmed cases",), "D023"),
    ECDCTopicContract("ARENA", "Arenavirus infection", ("All cases",), None, "unmapped"),
    ECDCTopicContract("BOTU", "Botulism", ("Confirmed cases",), "D091"),
    ECDCTopicContract("BRUC", "Brucellosis", ("Confirmed cases",), "D032"),
    ECDCTopicContract("CAMP", "Campylobacteriosis", ("Disease surveillance|Confirmed cases",), "D092"),
    ECDCTopicContract("CCHF", "Crimean-Congo haemorrhagic fever", ("All cases",), "D203"),
    ECDCTopicContract("CHIK", "Chikungunya virus disease", ("All cases",), "D052"),
    ECDCTopicContract("CHLAM", "Chlamydia infection", ("Confirmed cases",), "D094"),
    ECDCTopicContract("CHOL", "Cholera", ("Confirmed cases",), "D002"),
    ECDCTopicContract("CONSYPH", "Syphilis, congenital", ("Confirmed cases",), "D167"),
    ECDCTopicContract("CRYP", "Cryptosporidiosis", ("Confirmed cases",), "D096"),
    ECDCTopicContract("DENGUE", "Dengue", ("All cases",), "D021"),
    ECDCTopicContract("DIPH", "Diphtheria", ("C. diphtheriae cases", "C. ulcerans cases"), "D029"),
    ECDCTopicContract("ECHI", "Echinococcosis", ("Confirmed cases",), "D045"),
    ECDCTopicContract("FILO", "Ebola and Marburg virus disease", ("All cases",), None, "aggregate"),
    ECDCTopicContract("GIAR", "Giardiasis", ("Confirmed cases",), "D099"),
    ECDCTopicContract("GONO", "Gonorrhoea", ("Confirmed cases",), "D033"),
    ECDCTopicContract("HAEINF", "Invasive Haemophilus influenzae disease", ("Confirmed cases",), "D100"),
    ECDCTopicContract("HANTA", "Hantavirus infection", ("All cases",), "D102"),
    ECDCTopicContract("HEPA", "Hepatitis A", ("Confirmed cases",), "D007"),
    ECDCTopicContract("HEPC", "Hepatitis C", ("All cases (for countries reporting both acute and chronic cases)",), "D009"),
    ECDCTopicContract("HIVAIDS", "HIV infection", ("HIV infection|Confirmed cases",), "D162", local_code="hiv_infection"),
    ECDCTopicContract("HIVAIDS", "AIDS", ("AIDS|Confirmed cases",), "D005", local_code="aids"),
    ECDCTopicContract("LEGI", "Legionnaires' disease", ("All cases",), "D107"),
    ECDCTopicContract("LEPT", "Leptospirosis", ("Confirmed cases",), "D035"),
    ECDCTopicContract("LGV", "Chlamydia infection, lymphogranuloma venereum", ("Confirmed cases",), "D242"),
    ECDCTopicContract("LIST", "Listeriosis", ("Confirmed cases",), "D108"),
    ECDCTopicContract("LYMENEURO", "Lyme Neuroborreliosis", ("All cases",), "D109", "narrower"),
    ECDCTopicContract("MALA", "Malaria", ("All cases",), "D037"),
    ECDCTopicContract("MEAS", "Measles", ("All cases",), "D017"),
    ECDCTopicContract("MENI", "Invasive meningococcal disease", ("Confirmed cases",), "D110"),
    ECDCTopicContract("TETA", "Tetanus", ("All cases",), "D120"),
    ECDCTopicContract("PERT", "Pertussis", ("All cases",), "D028"),
    ECDCTopicContract("PLAG", "Plague", ("Confirmed cases",), "D001"),
    ECDCTopicContract("PNEU", "Invasive pneumococcal disease", ("Confirmed cases",), "D106"),
    ECDCTopicContract("QFEV", "Q fever", ("Confirmed cases",), "D113"),
    ECDCTopicContract("RABI", "Rabies", ("Confirmed cases",), "D019"),
    ECDCTopicContract("RIFT", "Rift valley fever", ("All cases",), "D173"),
    ECDCTopicContract("RUBE", "Rubella", ("All cases",), "D040"),
    ECDCTopicContract("SALM", "Salmonellosis", ("Disease surveillance|Confirmed cases",), "D114"),
    ECDCTopicContract("SHIG", "Shigellosis", ("Disease surveillance|Confirmed cases",), "D105"),
    ECDCTopicContract("SYPH", "Syphilis", ("Confirmed cases",), "D034"),
    ECDCTopicContract("TBE", "Tick-borne encephalitis", ("All cases",), "D205"),
    ECDCTopicContract("MUMP", "Mumps", ("All cases",), "D039"),
    ECDCTopicContract("TOXO", "Toxoplasmosis, congenital", ("Confirmed cases - Age  below 1",), "D085", "narrower"),
    ECDCTopicContract("TRIC", "Trichinellosis", ("Confirmed cases",), "D122"),
    ECDCTopicContract("TUBE", "Tuberculosis", ("All cases",), "D025"),
    ECDCTopicContract("TULA", "Tularaemia", ("Confirmed cases",), "D123"),
    ECDCTopicContract("VCJD", "Creutzfeldt-Jakob disease, variant", ("All cases",), "D144", "narrower"),
    ECDCTopicContract("VHFOTH", "Other Viral Haemorrhagic fevers", ("All cases",), "D127"),
    ECDCTopicContract("STEC", "STEC/VTEC infection", ("Confirmed cases",), "D115"),
    ECDCTopicContract("WNF", "West Nile virus infection", ("All cases",), "D128"),
    ECDCTopicContract("YELF", "Yellow fever", ("Confirmed cases",), "D059"),
    ECDCTopicContract("YERS", "Yersiniosis", ("Confirmed cases",), "D155"),
    ECDCTopicContract("ZIKA", "Zika virus infection", ("Reported cases",), "D051"),
)


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").replace("\xa0", " ").split())


def _normalized(value: object) -> str:
    return _text(value).replace("–", "-").casefold()


def _annual_count_measure(measures: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    candidates: list[Mapping[str, object]] = []
    for measure in measures:
        resolutions = ((measure.get("ResolutionList") or {}).get("Resolutions") or [])  # type: ignore[union-attr]
        annual = any(
            str(item.get("GeoLevelNumber")) == "2" and item.get("TimeUnitCode") == "Y"
            for item in resolutions
        )
        label = _normalized(measure.get("Label"))
        if (
            annual
            and measure.get("Unit") == "N"
            and "COUNT" in str(measure.get("Code") or "").split(".")
            and "death" not in label
            and "fatal" not in label
        ):
            candidates.append(measure)
    if not candidates:
        raise ECDCContractError("ECDC population exposes no annual country count measure")
    candidates.sort(key=lambda item: (not bool(item.get("IsDefault")), int(item.get("Index") or 999999)))
    return candidates[0]


class ECDCAtlasCrawler(BaseCrawler):
    def __init__(self, country_code: str, *, save_raw: bool = False, raw_dir: Optional[Path] = None) -> None:
        code = _text(country_code).upper()
        if code not in TARGET_COUNTRIES:
            raise ValueError(f"Unsupported ECDC baseline country: {country_code!r}")
        super().__init__(user_agent=f"GlobalID/2.0 ({code} ECDC Atlas annual baseline)", timeout=90, max_retries=5, delay=0.05)
        self.country_code = code
        self.source_geo_code = source_geo_code(code)
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir or f"data/raw/{code.casefold()}")

    def _json(self, path: str, **params) -> dict:
        response = self.get(f"{API_BASE}{path}", params=params)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ECDCContractError(f"ECDC {path} returned a non-object payload")
        return payload

    def _topic_rows(self, contract: ECDCTopicContract, topic_id: int, *, start_year: int) -> tuple[list[dict], dict]:
        by_population: list[dict[int, float]] = []
        measure_meta: list[dict] = []
        raw_results: list[dict] = []
        for population in contract.populations:
            measures_payload = self._json(
                "GetIndicatorMeasuresForHealthTopicDatasetAndPopulation",
                datasetId=DATASET_ID,
                healthtopicId=topic_id,
                measurePopulation=population,
            )
            measure = _annual_count_measure(measures_payload.get("Measures") or [])
            result_payload = self._json(
                "GetMeasureResultsForTimeUnitAndGeoRegion",
                measureId=int(measure["Id"]),
                timeUnit="Y",
                geoCode=self.source_geo_code,
            )
            values: dict[int, float] = {}
            for item in result_payload.get("MeasureResults") or []:
                if item.get("GeoCountry") != self.source_geo_code or item.get("YValue") is None:
                    continue
                raw_year = _text(item.get("TimeCode"))
                if not re.fullmatch(r"\d{4}", raw_year):
                    raise ECDCContractError(f"Unexpected ECDC annual time code: {raw_year!r}")
                year = int(raw_year)
                if year < start_year:
                    continue
                # An annual baseline must never publish an in-progress calendar
                # year as a closed total. ECDC topics update asynchronously, so
                # current-year cells are withheld until the next calendar year.
                if year >= datetime.now(timezone.utc).year:
                    continue
                value = float(item["YValue"])
                if value < 0 or not value.is_integer():
                    raise ECDCContractError(f"ECDC annual count is not a non-negative integer: {value}")
                if year in values:
                    raise ECDCContractError(f"Duplicate ECDC {contract.source_code}/{population}/{year}")
                values[year] = value
            by_population.append(values)
            measure_meta.append({
                "population": population,
                "measure_id": int(measure["Id"]),
                "measure_code": measure.get("Code"),
                "measure_label": measure.get("Label"),
            })
            raw_results.append(result_payload)

        # For a reviewed combined population (currently diphtheria species), a
        # year is emitted only when every component is present. Missing is not
        # coerced to zero.
        years = set.intersection(*(set(values) for values in by_population)) if by_population else set()
        rows: list[dict] = []
        for year in sorted(years):
            cases = int(sum(values[year] for values in by_population))
            source_url = f"{ATLAS_URL}?{urlencode({'Dataset': DATASET_ID, 'HealthTopic': topic_id})}"
            rows.append({
                "Date": date(year, 1, 1).isoformat(),
                "Year": str(year),
                "RawDiseaseLabel": contract.label,
                "SourceDiseaseCode": contract.source_code,
                "DiseaseCode": "__source_native__",
                "Cases": str(cases),
                "ValueStatus": "reported",
                "Frequency": "annual",
                "Measure": "case_notifications",
                "Unit": "count",
                "ReportingArea": self.country_code,
                "GeographyKey": f"country:{self.country_code}:national",
                "ReportingBasis": "ecdc_member_state_reported_aggregate_surveillance",
                "TimeBasis": "calendar year",
                "DatasetStatus": "closed_revisable",
                "IsProvisional": "false",
                "AuthoritativeRevision": "true",
                "MissingValuePolicy": "missing_is_unknown",
                "Source": DEFAULT_SOURCE_NAME,
                "SourceScope": DEFAULT_SCOPE,
                "SourceURL": source_url,
                "RetrievedAt": datetime.now(timezone.utc).isoformat(),
                "SourceContract": CONTRACT_VERSION,
                "SourceAttribution": ATTRIBUTION,
                "ReuseTermsURL": REUSE_TERMS_URL,
                "LicenseReviewStatus": "ecdc_publication_and_reproduction_authorized_with_attribution",
                "PublicReleaseEnabled": "true",
                # These are national all-population facts. Atlas topic and
                # measure identifiers are provenance, not analytic strata;
                # keeping the fact dimension empty makes the site/API expose
                # the records at the canonical national ``all`` grain.
                "Dimensions": "{}",
                "SourceDimensions": json.dumps({"topic_code": contract.code, "topic_id": topic_id, "source_geo_code": self.source_geo_code, "populations": list(contract.populations), "measure_ids": [item["measure_id"] for item in measure_meta]}, sort_keys=True),
            })
        return rows, {"contract": contract.source_code, "topic_id": topic_id, "measures": measure_meta, "results": raw_results}

    def _archive(self, payload: dict) -> None:
        if not self.save_raw:
            return
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        folder = self.raw_dir / datetime.now(timezone.utc).strftime("%Y/%m/%d")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"ecdc_atlas_annual_{digest[:12]}.json"
        path.write_bytes(content + b"\n")

    @staticmethod
    def _write_merged(
        output_csv: Path,
        fresh_rows: Iterable[Mapping[str, str]],
        *,
        replace_from_year: int,
    ) -> list[dict[str, str]]:
        merged: Dict[tuple[str, str], dict[str, str]] = {}
        if output_csv.exists():
            with output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    row = {str(key): _text(value) for key, value in raw.items()}
                    row_year = int(row["Date"][:4]) if re.match(r"^\d{4}", row.get("Date", "")) else None
                    if (
                        row.get("Date")
                        and row.get("SourceDiseaseCode")
                        and row_year is not None
                        and row_year < replace_from_year
                    ):
                        merged[(row["Date"], row["SourceDiseaseCode"])] = row
        for raw in fresh_rows:
            row = {str(key): _text(value) for key, value in raw.items()}
            merged[(row["Date"], row["SourceDiseaseCode"])] = row
        rows = sorted(merged.values(), key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
        fields = sorted({key for row in rows for key in row})
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_csv.with_suffix(output_csv.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        os.replace(temporary, output_csv)
        return rows

    def crawl_annual_baseline(self, output_csv: Path, *, start_year: int = 1990) -> ECDCFetchSummary:
        topics_payload = self._json("GetHealthTopicsForDataset", datasetId=DATASET_ID)
        topic_by_code = {item.get("Code"): item for item in topics_payload.get("HealthTopics") or []}
        for contract in TOPIC_CONTRACTS:
            topic = topic_by_code.get(contract.code)
            if topic is None or (contract.code != "HIVAIDS" and _text(topic.get("Label")) != contract.label):
                raise ECDCContractError(f"ECDC topic contract changed for {contract.code}")

        fresh: list[dict] = []
        raw_topics: list[dict] = []
        # Four workers keep the public service load bounded while avoiding a
        # multi-minute serial refresh. BaseCrawler's retry adapter handles
        # transient TLS/5xx failures observed from the Atlas service.
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._topic_rows, contract, int(topic_by_code[contract.code]["Id"]), start_year=start_year): contract
                for contract in TOPIC_CONTRACTS
            }
            for future in as_completed(futures):
                rows, raw = future.result()
                fresh.extend(rows); raw_topics.append(raw)
        self._archive({
            "contract_version": CONTRACT_VERSION,
            "dataset_id": DATASET_ID,
            "dataset_code": DATASET_CODE,
            "country_code": self.country_code,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "attribution": ATTRIBUTION,
            "reuse_terms_url": REUSE_TERMS_URL,
            "topics": raw_topics,
        })
        # Treat the fetched window as an authoritative snapshot. This removes
        # cells ECDC has withdrawn instead of retaining a stale local value;
        # history before a bounded start_year remains untouched.
        rows = self._write_merged(
            Path(output_csv), fresh, replace_from_year=start_year
        )
        dates = [date.fromisoformat(row["Date"]) for row in rows]
        return ECDCFetchSummary(len(rows), len({row["SourceDiseaseCode"] for row in rows}), max(dates, default=None), min(dates, default=None))

    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        del kwargs
        return []

    def parse(self, response) -> List[CrawlerResult]:
        del response
        return []


__all__ = [
    "ATLAS_URL", "ATTRIBUTION", "CONTRACT_VERSION", "DATASET_CODE", "DATASET_ID",
    "DEFAULT_SCOPE", "DEFAULT_SOURCE_NAME", "ECDCAtlasCrawler", "ECDCContractError",
    "ECDCFetchSummary", "ECDCTopicContract", "ONTOLOGY_SOURCE_ID_TEMPLATE",
    "REUSE_TERMS_URL", "TARGET_COUNTRIES", "TOPIC_CONTRACTS",
]
