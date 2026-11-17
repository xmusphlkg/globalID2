"""Publication review boundaries for the previously unpublished jurisdictions.

Mapping releases and publication eligibility are separate decisions. These
helpers never enable a country or alter a stored observation.
"""
from __future__ import annotations

from functools import lru_cache

from src.core.remaining_country_sources import REMAINING_COUNTRIES, WHO_COUNTRIES, SPF_TERRITORIES
from src.services.disease_mapping_config import load_reviewed_source_category_mappings


UNPUBLISHED_COUNTRY_CODES = frozenset(
    {*REMAINING_COUNTRIES, "AR", "CL", "CO", "ID", "KH", "MW", "MX", "MY", "NG", "PE", "TH", "ZA", "BQ"}
)
REVIEWED_COLLECTION_COUNTRIES = (UNPUBLISHED_COUNTRY_CODES - {"BQ"}) | {"NL"}


def collection_country(code: str) -> str:
    """BQ observations belong to the independently identified Dutch source."""
    return "NL" if code == "BQ" else code.split("-", 1)[0]


@lru_cache(maxsize=1)
def _reviewed_mappings() -> dict:
    return {
        (item.country_code, item.source_id, item.source_code, item.definition_version): item
        for item in load_reviewed_source_category_mappings()
    }


def reviewed_public_record(record: dict, country_code: str) -> dict | None:
    """Keep only explicitly reviewed identities, preserving their raw strata.

    Source identity dimensions are not population strata. A separate projection
    key admits a verified total without deleting its original dimensions.
    """
    parent = collection_country(country_code)
    if parent not in REVIEWED_COLLECTION_COUNTRIES:
        return record
    source = str(record.get("source_system") or "")
    if source == "SRC_NL_ECDC_ATLAS" and country_code == "NL":
        return record
    code = str(record.get("reviewed_source_code") or "")
    version = str(record.get("definition_version") or "")
    mappings = _reviewed_mappings()
    mapping = mappings.get((parent, source, code, version)) or mappings.get((parent, source, code, "*"))
    if mapping is None or mapping.projection_policy != "canonical" or mapping.target_kind != "concept":
        return None
    if any((
        str(record.get("disease_id") or "") != mapping.target_code,
        record.get("mapping_relation") != mapping.mapping_relation,
        record.get("comparability") != mapping.comparability,
        record.get("aggregation_policy") != mapping.aggregation_policy,
        record.get("geography_key") != f"country:{country_code}:national",
    )):
        return None
    result = dict(record)
    if parent in WHO_COUNTRIES and source == f"SRC_{parent}_WHO_GHO":
        from src.services.annual_source_mapping_review import who_mapping
        try:
            contract = who_mapping(code, record.get("reviewed_source_label") or "", version)
        except ValueError:
            return None
    elif parent in SPF_TERRITORIES and source == f"SRC_{parent}_SPF_ODISSE":
        from src.services.annual_source_mapping_review import spf_mapping
        try:
            contract = spf_mapping(code, record.get("reviewed_source_label") or "", version)
        except ValueError:
            return None
    else:
        from src.services.national_source_mapping_review import reviewed_national_total
        if not reviewed_national_total(record, country_code):
            return None
        result["projection_dimension_key"] = "all"
        if record.get("metric_type") in {"cases", "confirmed_case_notifications"}:
            result["projection_metric_type"] = "case_notifications"
        return result
    if any(record.get(field) != contract[field] for field in (
        "metric_type", "reporting_basis", "temporal_granularity"
    )) or record.get("dimensions") != contract["total_dimensions"]:
        return None
    result["projection_dimension_key"] = "all"
    if contract["metric_type"] == "confirmed_case_notifications":
        result["projection_metric_type"] = "case_notifications"
    return result
