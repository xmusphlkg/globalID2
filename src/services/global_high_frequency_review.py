"""Publication boundaries for typed laboratory and healthcare-activity series.

Concept mappings attach a series to a disease page. They never convert
laboratory tests, detections, positivity or healthcare activity into cases.
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from src.data.crawlers.ca_respiratory_weekly import VIRUSES
from src.data.crawlers.spf_odisse_weekly import DATASETS, metric_fields

FLUNET_INFLUENZA_FIELDS = frozenset({
    "AH1N12009", "AH1", "AH3", "AH5", "AH7N9", "AOTHER_SUBTYPE", "ANOTSUBTYPED",
    "ANOTSUBTYPABLE", "INF_A", "BVIC_2DEL", "BVIC_3DEL", "BVIC_DELUNK", "BVIC_NODEL",
    "BYAM", "BNOTDETERMINED", "INF_B", "INF_ALL",
})
FLUNET_TARGETS = {**{field: "D038" for field in FLUNET_INFLUENZA_FIELDS}, "RSV": "D142"}
CA_TARGETS = {"Influenza": "D038", "Influenza A": "D038", "Influenza B": "D038",
              "SARS-CoV-2": "D004", "RSV": "D142"}
SPF_TARGETS = {dataset: "D004" for dataset, spec in DATASETS.items() if spec[0] == "covid_19"}


@lru_cache(maxsize=1)
def _contracts():
    path = Path(__file__).resolve().parents[2] / "configs/high_frequency_global_ontology.json"
    return {row["id"]: row for row in json.loads(path.read_text())["source_series"]}


def reviewed_global_high_frequency_record(record: dict, country_code: str) -> dict | None:
    """Return the unchanged typed record only for a reviewed source contract."""
    country_code = str(country_code).upper()
    source = str(record.get("source_system") or "")
    series = _contracts().get(str(record.get("source_series_code") or record.get("series_code") or ""))
    code = str(record.get("reviewed_source_code") or (series["local_codes"][0] if series else ""))
    version = str(record.get("definition_version") or "")
    dimensions = record.get("dimensions")
    if (not isinstance(dimensions, dict)
            or record.get("geography_key") != f"country:{country_code}:national"
            or record.get("temporal_granularity") != "weekly"
            or record.get("aggregation_policy") != "non_additive"
            or record.get("mapping_relation") != "narrower"
            or record.get("comparability") != "conditional"
            or record.get("projection_dimension_key") == "all"
            or record.get("projection_metric_type") in {"cases", "case_notifications", "confirmed_case_notifications"}):
        return None
    if series is not None and (source != series["source_id"] or version != series["definition_version"]
                               or code not in series["local_codes"]):
        return None
    series_unit = record.get("series_unit")
    observation_unit = record.get("observation_unit")
    if source == f"SRC_{country_code}_WHO_FLUNET":
        expected_version = "my_who_flunet_detections_v1" if country_code == "MY" else "who_flunet_detections_v1"
        target = FLUNET_TARGETS.get(code)
        if (version != expected_version or record.get("metric_type") != "laboratory_detections"
                or series_unit != "count" or observation_unit != "count"
                or record.get("reporting_basis") != "who_gisrs_laboratory_surveillance"
                or set(dimensions) != {"surveillance_origin"}
                or dimensions["surveillance_origin"] not in {"SENTINEL", "NONSENTINEL", "NOTDEFINED"}):
            return None
    elif source == "SRC_CA_PHAC_RESPIRATORY" and country_code == "CA":
        metric = dimensions.get("metric")
        expected = {"detections": ("laboratory_detections", "count"),
                    "tests": ("laboratory_tests", "count"),
                    "percentpositive": ("laboratory_positivity", "percent")}.get(metric)
        virus = dimensions.get("virus")
        target = CA_TARGETS.get(virus)
        if (version != "ca_phac_respiratory_weekly_v1" or expected is None or set(dimensions) != {"virus", "metric"}
                or (record.get("metric_type"), series_unit) != expected or observation_unit != series_unit
                or record.get("reporting_basis") != "phac_reporting_laboratory_network"
                or virus not in VIRUSES or code != VIRUSES[virus] + "__" + metric):
            return None
    elif source == f"SRC_{country_code}_SPF_ODISSE_WEEKLY" and country_code in {"GP", "MQ", "GF"}:
        dataset, age, field = dimensions.get("dataset"), dimensions.get("age"), dimensions.get("metric")
        target = SPF_TARGETS.get(dataset)
        if (version != "spf_odisse_sursaud_weekly_v1" or set(dimensions) != {"dataset", "age", "metric"}
                or dataset not in DATASETS or field not in metric_fields(dataset)
                or not isinstance(age, str) or not age.strip()
                or record.get("metric_type") != "healthcare_activity_rate"
                or record.get("reporting_basis") != "sursaud_healthcare_location_not_patient_residence"
                or series_unit != ("per_100000_medical_acts" if field.endswith("_sos") else "per_100000_passages")
                or observation_unit != series_unit
                or code != DATASETS[dataset][0] + "__" + field + "__age_" + hashlib.sha256(age.encode()).hexdigest()[:12]):
            return None
    else:
        return None
    if target is None or record.get("disease_id") != target:
        return None
    # Preserve every origin, age and metric dimension; no 'all' projection key.
    result = dict(record)
    result.pop("projection_dimension_key", None)
    result.pop("projection_metric_type", None)
    result["projection_policy"] = "source_observations_only"
    return result
