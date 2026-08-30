"""Canadian national CNDSS annual surveillance baseline.

The Public Health Agency of Canada's Notifiable Diseases Online application
publishes a machine-readable description contract and a national annual raw
extract.  The latter contains one slot per disease and year; a null count is
unknown and must not be converted to zero.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from .base import BaseCrawler, CrawlerResult


COUNTRY_CODE = "CA"
DEFAULT_SCOPE = "phac_cndss_annual"
DEFAULT_SOURCE_NAME = "Canadian Notifiable Disease Surveillance System (CNDSS)"
ONTOLOGY_SOURCE_ID = "SRC_CA_PHAC_CNDSS"
SOURCE_PAGE_URL = "https://diseases.canada.ca/notifiable/extract-dataset"
DESCRIBE_URL = "https://diseases.canada.ca/ndc/json/en_US/1924/describe.json"
RAW_URL = "https://diseases.canada.ca/ndc/s/raw"
OPEN_DATASET_URL = "https://open.canada.ca/data/en/dataset/f2a711f9-be80-4b98-a205-8ac9e5e0d126"
REUSE_TERMS_URL = "https://open.canada.ca/en/open-government-licence-canada"
ATTRIBUTION = (
    "Contains information licensed under the Open Government Licence – Canada; "
    "source: Public Health Agency of Canada, Canadian Notifiable Disease "
    "Surveillance System (CNDSS)."
)
CONTRACT_VERSION = "phac-cndss-national-annual-v1-1924-2023-2026-08"
EXPECTED_FIRST_YEAR = 1924
EXPECTED_LAST_YEAR = 2023
MANITOBA_2023_UNAVAILABLE_CODES = frozenset({
    6, 13, 15, 16, 21, 32, 43, 48, 49, 61, 64, 66, 69, 70, 74, 78, 85,
    89, 94, 98, 106, 107, 108, 111, 112, 120, 127, 129, 134, 136, 141, 142, 144,
    147, 153, 173, 176, 179, 180, 182, 199, 211, 220, 242,
})
MANITOBA_2023_NOTICE = (
    "The official disease-specific reporting tables state that Manitoba 2023 "
    "data were unavailable at preparation time for 44 disease contracts."
)
NATIONAL_COMPLETENESS_NOTICE = (
    "A published CNDSS national aggregate is not evidence of complete reporting "
    "by every province and territory; inclusion varies by disease and year."
)


class CNDSSContractError(ValueError):
    """Raised when the official CNDSS extract no longer matches its contract."""


@dataclass(frozen=True)
class CNDSSDiseaseContract:
    code: int
    label: str
    concept_id: Optional[str]
    mapping_relation: str = "exact"
    projection_policy: str = "canonical"
    target_group: str = "G_UNMAPPED_SOURCE_CATEGORIES"

    @property
    def source_code(self) -> str:
        return str(self.code)


@dataclass(frozen=True)
class CNDSSFetchSummary:
    row_count: int
    series_count: int
    latest_date: Optional[date]
    first_date: Optional[date]
    source_last_year: int


# These mappings describe the source disease identity only. Broad aggregates
# and source slices that cannot safely become one canonical public curve are
# retained as source series with ``no_projection``.
CNDSS_DISEASE_CONTRACTS: tuple[CNDSSDiseaseContract, ...] = (
    CNDSSDiseaseContract(86, "Acquired Immune Deficiency Syndrome", "D005"),
    CNDSSDiseaseContract(177, "Acute Flaccid Paralysis", "D178"),
    CNDSSDiseaseContract(2, "Amoebiasis", "D165"),
    CNDSSDiseaseContract(6, "Anthrax", "D023"),
    CNDSSDiseaseContract(13, "Botulism", "D091"),
    CNDSSDiseaseContract(15, "Brucellosis", "D032"),
    CNDSSDiseaseContract(16, "Campylobacteriosis", "D092"),
    CNDSSDiseaseContract(20, "Chickenpox", "D054"),
    CNDSSDiseaseContract(138, "Chlamydia", "D094"),
    CNDSSDiseaseContract(21, "Cholera", "D002"),
    CNDSSDiseaseContract(211, "Clostridium difficile Associated Diarrhea", None, projection_policy="no_projection"),
    CNDSSDiseaseContract(107, "Congenital Rubella Syndrome", "D168"),
    CNDSSDiseaseContract(178, "Creutzfeldt-Jakob Disease", "D144"),
    CNDSSDiseaseContract(176, "Cryptosporidiosis", "D096"),
    CNDSSDiseaseContract(179, "Cyclosporiasis", "D097"),
    CNDSSDiseaseContract(32, "Diphtheria", "D029"),
    CNDSSDiseaseContract(170, "Dysentery - Type Unspecified", None, projection_policy="no_projection"),
    CNDSSDiseaseContract(157, "Food Poisoning", None, projection_policy="no_projection"),
    CNDSSDiseaseContract(43, "Giardiasis", "D099"),
    CNDSSDiseaseContract(45, "Gonorrhea", "D033"),
    CNDSSDiseaseContract(153, "Group A Streptococcal Disease, Invasive", "D157"),
    CNDSSDiseaseContract(136, "Group B Streptococcal Disease of the Newborn", "D245"),
    CNDSSDiseaseContract(242, "Haemophilus influenzae Disease, non-b, Invasive", "D100", "narrower", "no_projection"),
    CNDSSDiseaseContract(142, "Haemophilus influenzae Disease, type b, Invasive", "D100", "narrower", "no_projection"),
    CNDSSDiseaseContract(180, "Hantavirus Pulmonary Syndrome", "D102", "narrower"),
    CNDSSDiseaseContract(48, "Hepatitis A", "D007"),
    CNDSSDiseaseContract(49, "Hepatitis B", "D008"),
    CNDSSDiseaseContract(173, "Hepatitis C", "D009"),
    CNDSSDiseaseContract(
        51,
        "Hepatitis, non-A, non-B",
        "D071",
        "related",
        "no_projection",
    ),
    CNDSSDiseaseContract(52, "Hepatitis, Unspecified", "D071"),
    CNDSSDiseaseContract(128, "Human Immunodeficiency Virus Infection", "D162"),
    CNDSSDiseaseContract(958, "Influenza, Epidemic", "D038"),
    CNDSSDiseaseContract(58, "Influenza, Laboratory Confirmed", "D038", "narrower"),
    CNDSSDiseaseContract(61, "Legionellosis", "D107"),
    CNDSSDiseaseContract(64, "Leprosy", "D042"),
    CNDSSDiseaseContract(66, "Listeriosis", "D108"),
    CNDSSDiseaseContract(144, "Lyme Disease", "D109"),
    CNDSSDiseaseContract(69, "Malaria", "D037"),
    CNDSSDiseaseContract(70, "Measles", "D017"),
    CNDSSDiseaseContract(72, "Meningitis, Other Bacterial", "D135", "narrower"),
    CNDSSDiseaseContract(151, "Meningitis, Pneumococcal", "D106", "narrower"),
    CNDSSDiseaseContract(73, "Meningitis, Viral", "D134"),
    CNDSSDiseaseContract(74, "Meningococcal Disease, Invasive", "D110"),
    CNDSSDiseaseContract(78, "Mumps", "D039"),
    CNDSSDiseaseContract(220, "Norovirus Infection", None, projection_policy="no_projection"),
    CNDSSDiseaseContract(221, "Paralytic Shellfish Poisoning", "D247"),
    CNDSSDiseaseContract(83, "Paratyphoid", "D234"),
    CNDSSDiseaseContract(85, "Pertussis", "D028"),
    CNDSSDiseaseContract(89, "Plague", "D001"),
    CNDSSDiseaseContract(182, "Pneumococcal Disease, Invasive", "D106"),
    CNDSSDiseaseContract(94, "Poliomyelitis", "D013"),
    CNDSSDiseaseContract(98, "Rabies", "D019"),
    CNDSSDiseaseContract(106, "Rubella", "D040"),
    CNDSSDiseaseContract(108, "Salmonellosis", "D114"),
    CNDSSDiseaseContract(115, "Scarlet Fever and Streptococcal Sore Throat", None, projection_policy="no_projection", target_group="G_STREPTOCOCCAL_DISEASE_SPECTRUM"),
    CNDSSDiseaseContract(199, "Severe Acute Respiratory Syndrome", "D003"),
    CNDSSDiseaseContract(111, "Shigellosis", "D105"),
    CNDSSDiseaseContract(112, "Smallpox", "D064"),
    CNDSSDiseaseContract(117, "Syphilis", "D034"),
    CNDSSDiseaseContract(174, "Syphilis, Congenital", "D167"),
    CNDSSDiseaseContract(120, "Tetanus", "D120"),
    CNDSSDiseaseContract(123, "Trichinosis", "D122"),
    CNDSSDiseaseContract(119, "Tuberculosis", "D025"),
    CNDSSDiseaseContract(127, "Tularemia", "D123"),
    CNDSSDiseaseContract(161, "Typhoid and Paratyphoid", "D026"),
    CNDSSDiseaseContract(129, "Typhoid Fever", "D124"),
    CNDSSDiseaseContract(147, "Verotoxigenic Escherichia coli Infection", "D115"),
    CNDSSDiseaseContract(141, "Viral Haemorrhagic Fever", "D127"),
    CNDSSDiseaseContract(196, "West Nile Virus Infection", "D128"),
    CNDSSDiseaseContract(134, "Yellow Fever", "D059"),
)


def _text(value: object) -> str:
    plain = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return " ".join(plain.replace("\ufeff", "").replace("\xa0", " ").split())


class CanadaCNDSSNationalCrawler(BaseCrawler):
    def __init__(self, *, save_raw: bool = False, raw_dir: Optional[Path] = None) -> None:
        super().__init__(
            user_agent="GlobalID/2.0 (Canada CNDSS national annual baseline)",
            timeout=90,
            max_retries=5,
            delay=0.1,
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir or "data/raw/ca")

    def _json(self, url: str, **kwargs) -> dict:
        payload = self.get(url, **kwargs).json()
        if not isinstance(payload, dict):
            raise CNDSSContractError(f"CNDSS returned a non-object payload from {url}")
        return payload

    @staticmethod
    def _validate_describe(payload: Mapping[str, object]) -> tuple[int, int]:
        try:
            first_year = int(payload["year_min"])
            last_year = int(payload["year_max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CNDSSContractError("CNDSS describe has invalid year bounds") from exc
        if first_year != EXPECTED_FIRST_YEAR:
            raise CNDSSContractError(f"CNDSS first year changed: {first_year}")
        if last_year != EXPECTED_LAST_YEAR:
            raise CNDSSContractError(
                "CNDSS last year changed and requires an explicit contract review: "
                f"{last_year} != {EXPECTED_LAST_YEAR}"
            )

        descriptions = payload.get("descriptions")
        if not isinstance(descriptions, list):
            raise CNDSSContractError("CNDSS describe has no disease descriptions")
        actual: dict[int, str] = {}
        for item in descriptions:
            if not isinstance(item, dict):
                raise CNDSSContractError("CNDSS disease description is not an object")
            try:
                code = int(item["code"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CNDSSContractError("CNDSS disease code is invalid") from exc
            if code in actual:
                raise CNDSSContractError(f"Duplicate CNDSS disease code: {code}")
            actual[code] = _text(item.get("name"))
        expected = {item.code: item.label for item in CNDSS_DISEASE_CONTRACTS}
        if actual != expected:
            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            changed = sorted(code for code in expected.keys() & actual.keys() if expected[code] != actual[code])
            raise CNDSSContractError(
                f"CNDSS disease contract changed; missing={missing}, extra={extra}, labels={changed}"
            )
        affected = {
            int(item["code"])
            for item in descriptions
            if "The 2023 data from MB were not available at time of data preparation."
            in str(item.get("limitation") or "")
        }
        if affected != MANITOBA_2023_UNAVAILABLE_CODES:
            raise CNDSSContractError(
                "CNDSS Manitoba 2023 limitation contract changed; "
                f"expected={sorted(MANITOBA_2023_UNAVAILABLE_CODES)}, "
                f"actual={sorted(affected)}"
            )
        incomplete = [
            int(item["code"])
            for item in descriptions
            if not str(item.get("limitation") or "").strip()
            or not isinstance(item.get("table"), dict)
            or not isinstance(item.get("tableyears"), list)
        ]
        if incomplete:
            raise CNDSSContractError(
                "CNDSS disease-specific reporting limitations are incomplete: "
                f"{sorted(incomplete)}"
            )
        return first_year, last_year

    @staticmethod
    def _rows_from_payload(
        payload: Mapping[str, object], *, first_year: int, last_year: int
    ) -> list[dict[str, str]]:
        if payload.get("status") != "ok" or not isinstance(payload.get("records"), list):
            raise CNDSSContractError("CNDSS raw endpoint did not return status=ok records")
        expected_keys = {
            (year, contract.code)
            for year in range(first_year, last_year + 1)
            for contract in CNDSS_DISEASE_CONTRACTS
        }
        records: dict[tuple[int, int], Mapping[str, object]] = {}
        for raw in payload["records"]:  # type: ignore[index]
            if not isinstance(raw, dict):
                raise CNDSSContractError("CNDSS raw record is not an object")
            try:
                key = (int(raw["y"]), int(raw["d"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise CNDSSContractError("CNDSS raw record has invalid identity") from exc
            if key in records:
                raise CNDSSContractError(f"Duplicate CNDSS annual slot: {key}")
            records[key] = raw
        if set(records) != expected_keys:
            missing = len(expected_keys - set(records))
            extra = len(set(records) - expected_keys)
            raise CNDSSContractError(f"CNDSS annual grid changed; missing={missing}, extra={extra}")

        contract_by_code = {item.code: item for item in CNDSS_DISEASE_CONTRACTS}
        retrieved_at = datetime.now(timezone.utc).isoformat()
        rows: list[dict[str, str]] = []
        for (year, code), raw in sorted(records.items()):
            value = raw.get("t")
            if value is None:
                continue
            try:
                count = float(value)
            except (TypeError, ValueError) as exc:
                raise CNDSSContractError(f"CNDSS count is invalid for {code}/{year}") from exc
            if count < 0 or not count.is_integer():
                raise CNDSSContractError(f"CNDSS count is not a non-negative integer: {count}")
            contract = contract_by_code[code]
            rows.append({
                "Date": date(year, 1, 1).isoformat(),
                "Year": str(year),
                "RawDiseaseLabel": contract.label,
                "SourceDiseaseCode": contract.source_code,
                "DiseaseCode": "__source_native__",
                "Cases": str(int(count)),
                "ValueStatus": "reported",
                "Frequency": "annual",
                "Measure": "case_notifications",
                "Unit": "count",
                "ReportingArea": COUNTRY_CODE,
                "GeographyKey": "country:CA:national",
                "ReportingBasis": "provincial_territorial_voluntary_notifications_national",
                "TimeBasis": "calendar year",
                "DatasetStatus": "closed_revisable",
                "IsProvisional": "false",
                "AuthoritativeRevision": "true",
                "MissingValuePolicy": "missing_is_unknown",
                "Source": DEFAULT_SOURCE_NAME,
                "SourceScope": DEFAULT_SCOPE,
                "SourceURL": SOURCE_PAGE_URL,
                "RetrievedAt": retrieved_at,
                "SourceContract": CONTRACT_VERSION,
                "SourceAttribution": ATTRIBUTION,
                "ReuseTermsURL": REUSE_TERMS_URL,
                "LicenseReviewStatus": "open_government_licence_canada",
                "PublicReleaseEnabled": "true",
                "Dimensions": "{}",
                "SourceDimensions": json.dumps({
                    "cndss_disease_code": code,
                    "cndss_group_code": raw.get("g"),
                    "disease_reporting_contract_url": DESCRIBE_URL,
                    "national_aggregate_is_all_jurisdiction_complete": False,
                    "manitoba_2023_data_unavailable": (
                        year == 2023
                        and code in MANITOBA_2023_UNAVAILABLE_CODES
                    ),
                    "national_rate_per_100000": raw.get("r"),
                }, sort_keys=True),
            })
        return rows

    def _archive(self, describe: dict, raw: dict) -> None:
        if not self.save_raw:
            return
        payload = {
            "contract_version": CONTRACT_VERSION,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "national_aggregate_is_all_jurisdiction_complete": False,
            "national_completeness_notice": NATIONAL_COMPLETENESS_NOTICE,
            "manitoba_2023_unavailable_disease_count": len(
                MANITOBA_2023_UNAVAILABLE_CODES
            ),
            "manitoba_2023_notice": MANITOBA_2023_NOTICE,
            # The official description is retained verbatim because every
            # disease carries its own jurisdiction/year reporting table and
            # limitation text; a generic summary cannot replace that evidence.
            "describe": describe,
            "raw": raw,
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        folder = self.raw_dir / datetime.now(timezone.utc).strftime("%Y/%m/%d")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"cndss_national_annual_{digest[:12]}.json").write_bytes(content + b"\n")

    @staticmethod
    def _write_authoritative(output_csv: Path, rows: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
        prepared = sorted(
            ({str(key): _text(value) for key, value in row.items()} for row in rows),
            key=lambda row: (row["Date"], row["SourceDiseaseCode"]),
        )
        fields = sorted({key for row in prepared for key in row})
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_csv.with_suffix(output_csv.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(prepared)
        os.replace(temporary, output_csv)
        return prepared

    def crawl_annual_baseline(
        self, output_csv: Path, *, start_year: int = EXPECTED_FIRST_YEAR
    ) -> CNDSSFetchSummary:
        describe = self._json(DESCRIBE_URL)
        source_first_year, source_last_year = self._validate_describe(describe)
        requested_start_year = max(source_first_year, int(start_year))
        if requested_start_year > source_last_year:
            raise CNDSSContractError("Requested CNDSS start year is after source coverage")
        # The current CSV is an authoritative snapshot. Always retrieve and
        # rewrite the full reviewed contract so a bounded request cannot erase
        # older local history. Database replacement may still use its own
        # bounded window; full rows are safe to upsert outside that window.
        first_year = source_first_year
        codes = ",".join(str(item.code) for item in CNDSS_DISEASE_CONTRACTS)
        raw = self._json(RAW_URL, params=[
            ("s", str(EXPECTED_FIRST_YEAR)),
            ("f", f"d:in:{codes}"),
            ("f", f"y:..:{first_year},{source_last_year}"),
        ])
        rows = self._rows_from_payload(raw, first_year=first_year, last_year=source_last_year)
        self._archive(describe, raw)
        written = self._write_authoritative(Path(output_csv), rows)
        dates = [date.fromisoformat(row["Date"]) for row in written]
        return CNDSSFetchSummary(
            row_count=len(written),
            series_count=len({row["SourceDiseaseCode"] for row in written}),
            latest_date=max(dates, default=None),
            first_date=min(dates, default=None),
            source_last_year=source_last_year,
        )

    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        del kwargs
        return []

    def parse(self, response) -> List[CrawlerResult]:
        del response
        return []


__all__ = [
    "ATTRIBUTION", "CNDSSContractError", "CNDSSDiseaseContract",
    "CNDSSFetchSummary", "CNDSS_DISEASE_CONTRACTS", "CONTRACT_VERSION",
    "CanadaCNDSSNationalCrawler", "DEFAULT_SCOPE", "DEFAULT_SOURCE_NAME",
    "DESCRIBE_URL", "EXPECTED_LAST_YEAR", "MANITOBA_2023_NOTICE",
    "MANITOBA_2023_UNAVAILABLE_CODES", "NATIONAL_COMPLETENESS_NOTICE",
    "ONTOLOGY_SOURCE_ID", "OPEN_DATASET_URL", "RAW_URL",
    "REUSE_TERMS_URL", "SOURCE_PAGE_URL",
]
