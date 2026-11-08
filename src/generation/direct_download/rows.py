"""Flatten country and disease observations into lossless download rows."""
from __future__ import annotations

from src.services.disease_series_policy import is_case_count_series

SOURCE_SERIES_FIELDS = (
    "source_series_code",
    "source_system",
    "source_label",
    "metric_type",
    "reporting_basis",
    "temporal_granularity",
    "unit",
    "geography_key",
    "dimension_key",
    "mapping_relation",
    "comparability",
    "aggregation_policy",
    "availability_status",
    "missing_value_policy",
    "definition_version",
    "case_definition",
    "case_definition_uri",
)


def _source_columns(source_info: dict) -> dict:
    sources = source_info.get("sources") or []
    return {
        "primary_source_scope": source_info.get("primary_scope"),
        "primary_source_label": source_info.get("primary_label"),
        "primary_source_url": source_info.get("primary_url"),
        "primary_source_type": source_info.get("primary_type"),
        "source_scopes": "; ".join(
            source.get("scope") or "" for source in sources if source.get("scope")
        ),
        "source_labels": "; ".join(
            source.get("label") or "" for source in sources if source.get("label")
        ),
        "source_urls": "; ".join(
            source.get("url") or "" for source in sources if source.get("url")
        ),
        "source_types": "; ".join(
            source.get("type") or "" for source in sources if source.get("type")
        ),
    }


def _projection_provenance_columns(
    series: dict,
    source_series: dict | None = None,
) -> dict:
    selected_codes = [
        str(code) for code in series.get("selected_series_codes") or []
    ]
    data_provenance = series.get("data_provenance") or {}
    columns = {
        "series_code": (
            source_series.get("series_code")
            if source_series
            else (selected_codes[0] if len(selected_codes) == 1 else None)
        ),
        "is_selected_series": (
            source_series.get("series_code") in selected_codes
            if source_series else None
        ),
        "data_layer": series.get("data_layer"),
        "projection_policy": series.get("projection_policy"),
        "coverage_status": series.get("coverage_status"),
        "loss_risk": series.get("loss_risk"),
        "selected_series_codes": "; ".join(selected_codes),
        "available_series_count": series.get("available_series_count") or 0,
        "provenance_note": data_provenance.get("note_en"),
    }
    if source_series:
        columns.update(
            {field: source_series.get(field) for field in SOURCE_SERIES_FIELDS}
        )
        quality_statuses = source_series.get("quality_statuses") or []
        columns["quality_status"] = "; ".join(
            str(value) for value in quality_statuses if value
        )
    else:
        columns["temporal_granularity"] = series.get("period_granularity")
    return columns


def _source_observation_rows(
    series: dict,
    base_columns: dict,
    source_columns: dict,
) -> list[dict]:
    rows: list[dict] = []
    for source_series in series.get("source_series") or []:
        dates = source_series.get("dates") or []
        values = source_series.get("values") or []
        granularity = str(source_series.get("temporal_granularity") or "").lower()
        for index, value_date in enumerate(dates):
            value = values[index] if index < len(values) else None
            case_value = value if is_case_count_series(source_series) else None
            rows.append(
                {
                    **base_columns,
                    "record_kind": "source_series_observation",
                    "date": value_date,
                    "year_month": value_date[:7] if value_date else None,
                    "value": value,
                    "cases": case_value,
                    "weekly_equiv_cases": (
                        float(value)
                        if granularity == "weekly" and case_value is not None
                        else None
                    ),
                    "deaths": None,
                    "incidence_rate_per_100k": None,
                    "incidence_rate_source": None,
                    "mortality_rate": None,
                    **_projection_provenance_columns(series, source_series),
                    **source_columns,
                }
            )
    return rows


def _require_lossless_download_series(series: dict) -> None:
    if series.get("projection_policy") == "published_series":
        raise ValueError(
            "Public curve snapshots omit source provenance and cannot generate "
            "lossless downloads; refresh the affected country from observations."
        )


def build_country_download_rows(country_data: dict, source_info: dict) -> list[dict]:
    """Flatten a country projection without release-volatile fields."""

    rows: list[dict] = []
    source_columns = _source_columns(source_info)
    dataset_id = str(country_data.get("country_code") or "").lower()
    for series in (country_data.get("disease_series") or {}).values():
        _require_lossless_download_series(series)
        base_columns = {
            "dataset_kind": "country",
            "dataset_id": dataset_id,
            "dataset_slug": dataset_id,
            "dataset_name": country_data.get("country_name"),
            "country_code": country_data.get("country_code"),
            "country_name": country_data.get("country_name"),
            "disease_id": series.get("disease_id"),
            "disease_name_en": series.get("name_en"),
            "disease_name_zh": series.get("name_zh"),
            "category": series.get("category"),
        }
        rows.extend(
            _source_observation_rows(series, base_columns, source_columns)
        )
        dates = series.get("dates") or []
        cases = series.get("cases") or []
        weekly_equiv = series.get("weekly_equiv_cases") or []
        deaths = series.get("deaths") or []
        incidence_rates = series.get("incidence_rates") or []
        incidence_sources = series.get("incidence_sources") or []
        mortality_rates = series.get("mortality_rates") or []
        for index, value_date in enumerate(dates):
            rows.append(
                {
                    **base_columns,
                    "record_kind": "public_projection",
                    "date": value_date,
                    "year_month": value_date[:7] if value_date else None,
                    "cases": cases[index] if index < len(cases) else 0,
                    "value": cases[index] if index < len(cases) else 0,
                    "weekly_equiv_cases": (
                        weekly_equiv[index] if index < len(weekly_equiv) else None
                    ),
                    "deaths": deaths[index] if index < len(deaths) else 0,
                    "incidence_rate_per_100k": (
                        incidence_rates[index] if index < len(incidence_rates) else None
                    ),
                    "incidence_rate_source": (
                        incidence_sources[index] if index < len(incidence_sources) else None
                    ),
                    "mortality_rate": (
                        mortality_rates[index] if index < len(mortality_rates) else None
                    ),
                    **_projection_provenance_columns(series),
                    **source_columns,
                }
            )
    return _sort_rows(rows)


def build_disease_download_rows(
    disease_data: dict,
    source_info_by_country: dict[str, dict],
    country_name_by_code: dict[str, str],
) -> list[dict]:
    """Flatten a disease projection without release-volatile fields."""

    rows: list[dict] = []
    dataset_id = disease_data.get("disease_id")
    for country_code, series in (disease_data.get("country_series") or {}).items():
        try:
            _require_lossless_download_series(series)
        except ValueError as error:
            raise ValueError(f"Disease {dataset_id}, country {country_code}: {error}") from error
        dates = series.get("dates") or []
        cases = series.get("cases") or []
        weekly_equiv = series.get("weekly_equiv_cases") or []
        deaths = series.get("deaths") or []
        incidence_rates = series.get("incidence_rates") or []
        incidence_sources = series.get("incidence_sources") or []
        source_columns = _source_columns(
            source_info_by_country.get(country_code, {"sources": []})
        )
        base_columns = {
            "dataset_kind": "disease",
            "dataset_id": dataset_id,
            "dataset_slug": disease_data.get("slug"),
            "dataset_name": disease_data.get("name_en"),
            "country_code": country_code,
            "country_name": country_name_by_code.get(country_code),
            "disease_id": dataset_id,
            "disease_name_en": disease_data.get("name_en"),
            "disease_name_zh": disease_data.get("name_zh"),
            "category": disease_data.get("category"),
        }
        rows.extend(
            _source_observation_rows(series, base_columns, source_columns)
        )
        for index, value_date in enumerate(dates):
            rows.append(
                {
                    **base_columns,
                    "record_kind": "public_projection",
                    "date": value_date,
                    "year_month": value_date[:7] if value_date else None,
                    "cases": cases[index] if index < len(cases) else 0,
                    "value": cases[index] if index < len(cases) else 0,
                    "weekly_equiv_cases": (
                        weekly_equiv[index] if index < len(weekly_equiv) else None
                    ),
                    "deaths": deaths[index] if index < len(deaths) else 0,
                    "incidence_rate_per_100k": (
                        incidence_rates[index] if index < len(incidence_rates) else None
                    ),
                    "incidence_rate_source": (
                        incidence_sources[index] if index < len(incidence_sources) else None
                    ),
                    "mortality_rate": None,
                    **_projection_provenance_columns(series),
                    **source_columns,
                }
            )
    return _sort_rows(rows)


def _sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("country_code") or ""),
            str(row.get("disease_id") or ""),
            str(row.get("record_kind") or ""),
            str(row.get("series_code") or ""),
        ),
    )
