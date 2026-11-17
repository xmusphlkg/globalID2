"""Verify national calendar-month AGES facts without replacing holding identity."""
from calendar import monthrange
from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path

from src.data.crawlers.at import DEFINITION_VERSION, ONTOLOGY_SOURCE_ID


@lru_cache(maxsize=1)
def _mappings():
    path = Path(__file__).resolve().parents[2] / "configs/onboarding/high_frequency_at_mappings.json"
    return {row["local_code"]: row for row in json.loads(path.read_text())["rows"]}


def reviewed_at_high_frequency_record(record: dict, country_code: str) -> dict | None:
    """Accept reviewed source categories only at their original national grain."""
    if (country_code != "AT" or record.get("source_system") != ONTOLOGY_SOURCE_ID
            or record.get("series_code") != "SER_AT_AGES_RADAR_SOURCE_NATIVE"):
        return None
    code = record.get("reviewed_source_code")
    mapping = _mappings().get(code)
    if mapping is None or mapping["projection_policy"] != "canonical" or mapping["target_kind"] != "concept":
        return None
    expected = {"definition_version": DEFINITION_VERSION, "disease_id": mapping["target_code"],
                "metric_type": "case_notifications", "reporting_basis": "national_notifiable_disease_radar",
                "temporal_granularity": "monthly", "geography_key": "country:AT:national",
                "mapping_relation": mapping["mapping_relation"], "comparability": mapping["comparability"],
                "aggregation_policy": mapping["aggregation_policy"],
                "series_unit": "count", "observation_unit": "count"}
    if any(record.get(key) != value for key, value in expected.items()):
        return None
    dims = record.get("dimensions")
    if dims != {"source_disease_code": code, "source_disease_label": mapping["local_name"]}:
        return None
    if record.get("reviewed_source_label") != mapping["local_name"]:
        return None
    try:
        anchor = date.fromisoformat(str(record["date"])[:10])
        # AGES native rows are keyed by the first day of the calendar month.
        # Older imports carry only ``Date``/``Month`` in raw provenance, so
        # derive the period boundary from the stored observation date while
        # preserving any explicit period metadata when present.
        start = date.fromisoformat(str(record.get("period_start") or anchor)[:10])
        expected_end = start + timedelta(days=monthrange(start.year, start.month)[1])
        end = date.fromisoformat(str(record.get("period_end_exclusive") or expected_end)[:10])
    except (KeyError, TypeError, ValueError):
        return None
    if start.day != 1 or anchor != start or end != start + timedelta(days=monthrange(start.year, start.month)[1]):
        return None
    result = dict(record)
    result["period_start"] = start.isoformat()
    result["period_end_exclusive"] = end.isoformat()
    result["calendar"] = "Gregorian"
    result["projection_dimension_key"] = "all"
    result["projection_metric_type"] = "case_notifications"
    return result


__all__ = ["reviewed_at_high_frequency_record"]
