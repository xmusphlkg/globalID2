from __future__ import annotations

import inspect
import re

import pytest
from pydantic import ValidationError

from dashboard.api.location_codes import (
    COUNTRY_REGION_CODE_MAX_LENGTH,
    PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN,
)
from dashboard.api.routers.diseases import (
    list_disease_ontology_availability,
    list_disease_ontology_series,
)
from dashboard.api.schemas.sources import AutomationJobCreate, AutomationJobUpdate
from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.domain.automation_job import AutomationJob
from src.generation.site_data_database import _build_country_metadata
from src.services.automation_service import _country_code_resize_sql


def _query_max_length(endpoint) -> int | None:
    query = inspect.signature(endpoint).parameters["country_code"].default
    for constraint in query.metadata:
        value = getattr(constraint, "max_length", None)
        if value is not None:
            return value
    return None


def test_public_location_pattern_accepts_iso_subdivision_but_not_scope_alias() -> None:
    assert re.fullmatch(PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN, "CA")
    assert re.fullmatch(PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN, "CA-ON")
    assert not re.fullmatch(PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN, "CA_ON")


def test_disease_filters_accept_country_region_code_length() -> None:
    assert _query_max_length(list_disease_ontology_series) == (
        COUNTRY_REGION_CODE_MAX_LENGTH
    )
    assert _query_max_length(list_disease_ontology_availability) == (
        COUNTRY_REGION_CODE_MAX_LENGTH
    )


def test_automation_api_and_model_accept_ca_on() -> None:
    created = AutomationJobCreate(
        job_id="ca-on-monthly", name="Ontario", country_code="CA-ON"
    )
    updated = AutomationJobUpdate(country_code="CA-ON")

    assert created.country_code == "CA-ON"
    assert updated.country_code == "CA-ON"
    assert AutomationJob.__table__.c.country_code.type.length == 10

    with pytest.raises(ValidationError):
        AutomationJobUpdate(country_code="CA-ONTARIO-LONG")


def test_existing_postgres_automation_column_is_widened_in_place() -> None:
    assert _country_code_resize_sql("postgresql", 2) == (
        "ALTER TABLE automation_jobs ALTER COLUMN country_code TYPE VARCHAR(10)"
    )
    assert _country_code_resize_sql("postgresql", 10) is None
    assert _country_code_resize_sql("sqlite", 2) is None


def test_ontario_database_metadata_is_subdivision_not_alpha2() -> None:
    profile = get_country_profile("CA-ON")
    bootstrap = get_country_bootstrap_config("CA-ON")

    metadata = _build_country_metadata(profile, bootstrap)

    assert "iso_alpha2" not in metadata
    assert metadata["parent_country_code"] == "CA"
    assert metadata["location_type"] == "subdivision"
    assert metadata["iso_subdivision_code"] == "CA-ON"
    assert metadata["flag_country_code"] == "CA"
