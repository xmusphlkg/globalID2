from __future__ import annotations

import pytest
from fastapi import HTTPException

from dashboard.api.routers.diseases import (
    get_disease_ontology,
    get_disease_ontology_concept,
    get_disease_ontology_facets,
    list_disease_ontology_availability,
    list_disease_ontology_series,
)


@pytest.mark.asyncio
async def test_ontology_api_exposes_facets_and_hiv_concept() -> None:
    registry = await get_disease_ontology()
    facets = await get_disease_ontology_facets()
    concept = await get_disease_ontology_concept("d005")

    assert registry["default_rollup_policy"] == "no_auto_rollup"
    assert {facet["id"] for facet in facets} >= {
        "etiology",
        "clinical_course",
    }
    assert concept["id"] == "D005"
    assert any(
        relation["type"] == "clinical_stage_of"
        for relation in concept["relations"]["outgoing"]
    )


@pytest.mark.asyncio
async def test_ontology_series_api_distinguishes_us_hepatitis_components() -> None:
    series = await list_disease_ontology_series(
        source_id=None,
        country_code="US",
        local_code=None,
        local_label=None,
        concept_id="D208",
        group_id=None,
        status=None,
        availability_status=None,
    )

    assert len(series) == 2
    assert {item["facet_tags"]["case_status"][0] for item in series} == {
        "case_status.confirmed",
        "case_status.probable",
    }


@pytest.mark.asyncio
async def test_availability_api_explains_us_hiv_source_difference() -> None:
    availability = await list_disease_ontology_availability(
        source_id=None,
        country_code="US",
        concept_id="d162",
        group_id=None,
        series_id=None,
        status=None,
    )

    by_source = {item["source_id"]: item for item in availability}
    assert by_source["SRC_US_NNDSS"]["status"] == "not_reported_by_source"
    assert by_source["SRC_US_NHSS"]["status"] == "available"
    assert by_source["SRC_US_NHSS"]["series_id"] == "SER_US_NHSS_HIV_ANNUAL"


@pytest.mark.asyncio
async def test_ontology_api_returns_404_for_unknown_concept() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_disease_ontology_concept("D000")

    assert exc_info.value.status_code == 404
