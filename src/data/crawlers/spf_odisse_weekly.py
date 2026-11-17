"""SPF SurSaUD weekly territory healthcare-activity rates; never patient counts."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import urlencode

from .base import BaseCrawler
from .spf_overseas import BASE_URL, country_spec, page_document

DEFAULT_SCOPE = "spf_odisse_weekly"
HISTORY_START_YEAR = 1900
DATASETS = {
    "grippe-passages-urgences-et-actes-sos-medecin_reg": (
        "influenza_like_illness", "grippe", "Grippe : Passages aux urgences et Actes SOS Médecins (Région)"),
    "bronchiolite-passages-aux-urgences-et-actes-sos-medecins-region": (
        "bronchiolitis", "bronchio", "Bronchiolite : Passages aux urgences et Actes SOS Médecins (Région)"),
    "covid-19-passages-aux-urgences-et-actes-sos-medecins-region": (
        "covid_19", "covid", "COVID-19 : Passages aux urgences et Actes SOS Médecins (Région)"),
}


def metadata_url(dataset):
    if dataset not in DATASETS:
        raise ValueError("Unreviewed SPF weekly dataset")
    return BASE_URL + dataset


def records_url(dataset, country_code, *, offset=0, limit=100):
    _, region, _ = country_spec(country_code)
    return metadata_url(dataset) + "/records?" + urlencode({
        "where": f'region="{region}"', "limit": limit, "offset": offset,
        "order_by": "date_complet,sursaud_cl_age_gene",
    })


def metric_fields(dataset):
    token = DATASETS[dataset][1]
    return (f"taux_passages_{token}_sau", f"taux_hospit_{token}_sau", f"taux_actes_{token}_sos")


def validate_metadata(dataset, metadata):
    if metadata.get("dataset_id") != dataset:
        raise ValueError("SPF weekly dataset identity changed")
    meta = metadata["metas"]["default"]
    if (meta.get("publisher") != "Santé publique France" or meta.get("title") != DATASETS[dataset][2]
            or meta.get("license") != "Licence ouverte 2.0" or meta.get("update_frequency") != "WEEKLY"):
        raise ValueError("SPF weekly publisher/title/licence/cadence contract changed")
    fields = {f["name"]: f for f in metadata["fields"]}
    if not {"date_complet", "semaine", "region", "reglib", "sursaud_cl_age_gene", *metric_fields(dataset)} <= fields.keys():
        raise ValueError("SPF weekly required fields changed")
    for field in metric_fields(dataset):
        expected = "Pour 100 000 actes médicaux" if field.endswith("_sos") else "Pour 100 000 passages"
        if fields[field].get("description") != expected:
            raise ValueError("SPF weekly field denominator changed")
    description = re.sub("<[^>]*>", " ", meta.get("description", ""))
    if "SurSaUD" not in description or "diagnostic" not in description:
        raise ValueError("SPF weekly syndromic healthcare definition missing")
    return meta


def rate_value(value):
    if value is None or value in ("", "NA", "N/A", "ND", "-"):
        return "", "source_missing"
    if isinstance(value, bool):
        raise ValueError("Boolean SPF activity rate")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Unreviewed SPF activity-rate notation") from None
    if not number.is_finite() or number < 0:
        raise ValueError("SPF activity rates must be finite and nonnegative")
    return str(number), "reported"


def parse_records(records, *, country_code, metadata, as_of):
    _, region, name = country_spec(country_code)
    country_code = country_code.upper()
    for dataset in DATASETS:
        validate_metadata(dataset, metadata[dataset])
    rows, seen = [], set()
    for dataset, raw, provenance in records:
        if dataset not in DATASETS:
            raise ValueError("Unexpected SPF weekly record dataset")
        if raw.get("region") != region or raw.get("reglib") != name:
            raise ValueError("SPF weekly territory differs from requested geography")
        begin = date.fromisoformat(raw["date_complet"])
        iso = begin.isocalendar()
        if begin.weekday() != 0 or raw.get("semaine") != f"{iso.year}-S{iso.week:02d}" or begin.year < HISTORY_START_YEAR or begin > as_of:
            raise ValueError("SPF weekly ISO date/year/week identity disagrees")
        age = raw.get("sursaud_cl_age_gene")
        if not isinstance(age, str) or not age.strip():
            raise ValueError("SPF weekly age stratum missing")
        identity = (dataset, begin, age)
        if identity in seen:
            raise ValueError("Duplicate SPF territory/dataset/week/age")
        seen.add(identity)
        end = begin + timedelta(days=6)
        if end >= as_of:
            continue
        for field in metric_fields(dataset):
            if field not in raw:
                raise ValueError("SPF weekly required metric field missing")
            value, status = rate_value(raw[field])
            meta = metadata[dataset]["metas"]["default"]
            field_meta = next(f for f in metadata[dataset]["fields"] if f["name"] == field)
            is_sos, is_hospit = field.endswith("_sos"), "_hospit_" in field
            dimensions = {"dataset": dataset, "age": age, "metric": field}
            rows.append({
                **provenance,
                "Date": begin.isoformat(), "Year": str(begin.year), "Month": str(begin.month),
                "PeriodStart": begin.isoformat(), "PeriodEnd": end.isoformat(), "Calendar": "ISO-8601",
                "SourceYear": str(iso.year), "SourceWeek": str(iso.week), "SourcePeriodLabel": raw["semaine"],
                "TimeBasis": "healthcare_encounter_source_week", "DiseaseCode": "__source_native__",
                "SourceDiseaseCode": DATASETS[dataset][0] + "__" + field + "__age_" + hashlib.sha256(age.encode()).hexdigest()[:12],
                "RawDiseaseLabel": field_meta["label"] + "; " + age, "Cases": value,
                "MissingValueStatus": status, "Frequency": "weekly", "PeriodType": "weekly",
                "Measure": "healthcare_activity_rate", "Unit": "per_100000_medical_acts" if is_sos else "per_100000_passages",
                "ReportingBasis": "sursaud_healthcare_location_not_patient_residence",
                "DefinitionVersion": "spf_odisse_sursaud_weekly_v1", "Geography": name,
                "GeographyKey": f"country:{country_code}:national", "Source": f"{name} SPF ODISSÉ Weekly SurSaUD",
                "SourceURL": "https://odisse.santepubliquefrance.fr/explore/dataset/" + dataset + "/",
                "SourcePublishedAt": meta["modified"], "SourceDataset": dataset,
                "SourceAgeGroup": age, "SourceMetricField": field,
                "SourceDenominatorFieldDescription": field_meta["description"],
                "SourceDenominatorNarrative": "coded hospital admissions after emergency visit" if is_hospit else ("coded SOS Médecins acts" if is_sos else "coded emergency visits"),
                "DenominatorConsistencyStatus": "field_passages_vs_narrative_admissions_disagree" if is_hospit else "consistent",
                "Dimensions": json.dumps(dimensions, sort_keys=True, ensure_ascii=False),
                "DatasetStatus": "reported_revisable", "IsProvisional": "true",
                "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
                "PublicReleaseEnabled": "true", "AggregationPolicy": "non_additive",
            })
    if not rows:
        raise ValueError("No verified closed-week SPF territory activity rates")
    return rows


class SPFOdisseWeeklyCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=90, delay=0.1)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged SPF weekly capture")

    def parse(self, response):
        raise NotImplementedError("SPF weekly parsing requires captured metadata and provenance")
