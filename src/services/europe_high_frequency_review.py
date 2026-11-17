"""Validate frozen Europe source contracts before website publication.

Laboratory series retain every original stratum and can never acquire a
patient-case projection. Monthly totals are direct source cells, not rollups.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from functools import lru_cache
import hashlib
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _contracts():
    path = Path(__file__).resolve().parents[2] / "configs/high_frequency_europe_ontology.json"
    document = json.loads(path.read_text())
    return {s["id"]: s for s in document["source_series"]}


def _period(record, *, monthly):
    try:
        start = date.fromisoformat(str(record["period_start"])[:10])
        end = date.fromisoformat(str(record["period_end_exclusive"])[:10])
        reported = date.fromisoformat(str(record["date"])[:10])
    except (ValueError, KeyError, TypeError):
        return False
    if reported != start:
        return False
    if monthly:
        return start.day == 1 and end == start + timedelta(days=monthrange(start.year, start.month)[1])
    return start.weekday() == 0 and end == start + timedelta(days=7)


def reviewed_europe_high_frequency_record(record: dict, country_code: str) -> dict | None:
    """Admit only an exact registered category, metric, source and grain."""
    series = _contracts().get(str(record.get("source_series_code") or record.get("series_code") or ""))
    if series is None or not series.get("concept_id") or series["concept_id"] != record.get("disease_id"):
        return None
    source = str(record.get("source_system") or "")
    if source != series["source_id"] or not source.startswith(f"SRC_{country_code}_"):
        return None
    fields = {"definition_version": "definition_version", "metric_type": "measure", "reporting_basis": "reporting_basis", "temporal_granularity": "frequency",
        "mapping_relation": "mapping_relation", "comparability": "comparability", "aggregation_policy": "aggregation_policy"}
    if any(record.get(k) != series[v] for k, v in fields.items()):
        return None
    if record.get("series_unit") != series["unit"] or record.get("observation_unit") != series["unit"]:
        return None
    if record.get("geography_key") != f"country:{country_code}:national":
        return None
    code = record.get("reviewed_source_code") or series["local_codes"][0]
    label = record.get("reviewed_source_label") or record.get("source_label")
    if code not in series["local_codes"] or label not in series["local_labels"]:
        return None
    dims = record.get("dimensions")
    if not isinstance(dims, dict):
        return None
    result = dict(record)
    if source == f"SRC_{country_code}_ECDC_RESPIRATORY":
        if set(dims) != {"survtype", "pathogen", "pathogentype", "pathogensubtype", "indicator", "age", "source_dataset"}:
            return None
        expected_code = "resp_" + hashlib.sha256(json.dumps(dims, sort_keys=True).encode()).hexdigest()[:28]
        expected_label = " | ".join(dims[k] for k in ("pathogen", "pathogentype", "pathogensubtype", "indicator", "survtype", "age"))
        if code != expected_code or label != expected_label or not _period(record, monthly=False):
            return None
        # Remove potentially inherited projection overrides. The raw dimensions
        # stay unchanged and the projection layer sees a source-only metric.
        result.pop("projection_dimension_key", None)
        result.pop("projection_metric_type", None)
        result["projection_policy"] = "source_observations_only"
        return result
    if source not in {"SRC_CZ_SZU_MONTHLY", "SRC_EE_TERVISEAMET_MONTHLY"} or dims or not _period(record, monthly=True):
        return None
    if series["projection_policy"] == "source_observations_only":
        result["projection_policy"] = "source_observations_only"
        result.pop("projection_dimension_key", None)
    else:
        result["projection_dimension_key"] = "all"
    return result


__all__ = ["reviewed_europe_high_frequency_record"]
