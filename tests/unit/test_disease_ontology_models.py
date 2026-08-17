"""Metadata-only tests for the additive disease ontology ORM layer."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain import (  # noqa: E402
    DiseaseConceptAssignment,
    DiseaseConceptRelation,
    DiseaseSeriesObservation,
    DiseaseSourceAvailability,
    DiseaseSurveillanceSeries,
    DiseaseTaxonomyEdge,
    DiseaseTaxonomyNode,
)


def _foreign_key_targets(model) -> set[str]:
    return {
        foreign_key.target_fullname
        for column in model.__table__.columns
        for foreign_key in column.foreign_keys
    }


def _constraint_names(model, constraint_type) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, constraint_type)
    }


def test_ontology_tables_and_string_foreign_keys_are_registered() -> None:
    assert DiseaseTaxonomyNode.__tablename__ == "disease_taxonomy_nodes"
    assert DiseaseTaxonomyEdge.__tablename__ == "disease_taxonomy_edges"
    assert DiseaseConceptAssignment.__tablename__ == "disease_concept_assignments"
    assert DiseaseConceptRelation.__tablename__ == "disease_concept_relations"
    assert DiseaseSurveillanceSeries.__tablename__ == "disease_surveillance_series"
    assert DiseaseSourceAvailability.__tablename__ == "disease_source_availability"
    assert DiseaseSeriesObservation.__tablename__ == "disease_series_observations"

    assert _foreign_key_targets(DiseaseTaxonomyEdge) == {
        "disease_taxonomy_nodes.node_code"
    }
    assert _foreign_key_targets(DiseaseConceptAssignment) == {
        "disease_taxonomy_nodes.node_code",
        "standard_diseases.disease_id",
    }
    assert _foreign_key_targets(DiseaseConceptRelation) == {
        "standard_diseases.disease_id"
    }
    assert _foreign_key_targets(DiseaseSurveillanceSeries) == {
        "countries.code",
        "country_scopes.scope_code",
        "standard_diseases.disease_id",
    }
    assert _foreign_key_targets(DiseaseSeriesObservation) == {
        "disease_surveillance_series.series_code"
    }
    assert _foreign_key_targets(DiseaseSourceAvailability) == {
        "countries.code",
        "disease_surveillance_series.series_code",
    }

    for model, columns in (
        (DiseaseTaxonomyEdge, ("parent_node_code", "child_node_code")),
        (DiseaseConceptAssignment, ("disease_id", "node_code")),
        (DiseaseConceptRelation, ("subject_disease_id", "object_disease_id")),
        (
            DiseaseSurveillanceSeries,
            (
                "disease_id",
                "target_group_code",
                "country_code",
                "scope_code",
                "series_code",
            ),
        ),
        (
            DiseaseSeriesObservation,
            ("series_code", "geography_key", "dimension_key"),
        ),
        (
            DiseaseSourceAvailability,
            (
                "availability_code",
                "source_system",
                "country_code",
                "target_kind",
                "target_code",
                "series_code",
            ),
        ),
    ):
        for column_name in columns:
            assert model.__table__.c[column_name].type.python_type is str


def test_taxonomy_is_multi_parent_and_has_local_dag_guards() -> None:
    unique_names = _constraint_names(DiseaseTaxonomyEdge, UniqueConstraint)
    check_names = _constraint_names(DiseaseTaxonomyEdge, CheckConstraint)

    assert "uq_disease_taxonomy_edge" in unique_names
    assert "ck_disease_taxonomy_edge_no_self_loop" in check_names
    assert "ck_disease_taxonomy_edge_aggregation_policy" in check_names

    edge_unique = next(
        constraint
        for constraint in DiseaseTaxonomyEdge.__table__.constraints
        if constraint.name == "uq_disease_taxonomy_edge"
    )
    assert tuple(column.name for column in edge_unique.columns) == (
        "parent_node_code",
        "child_node_code",
        "relation_type",
    )
    assert not any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == ("child_node_code",)
        for constraint in DiseaseTaxonomyEdge.__table__.constraints
    )


def test_semantic_policy_columns_have_database_checks() -> None:
    expected_checks = {
        DiseaseConceptAssignment: {
            "ck_disease_concept_assignment_mapping_relation",
            "ck_disease_concept_assignment_confidence",
            "ck_disease_concept_assignment_status",
            "ck_disease_concept_assignment_valid_range",
        },
        DiseaseConceptRelation: {
            "ck_disease_concept_relation_no_self_loop",
            "ck_disease_concept_relation_comparability",
            "ck_disease_concept_relation_aggregation_policy",
            "ck_disease_concept_relation_confidence",
            "ck_disease_concept_relation_status",
            "ck_disease_concept_relation_valid_range",
        },
        DiseaseSurveillanceSeries: {
            "ck_disease_surveillance_series_exactly_one_target",
            "ck_disease_surveillance_series_mapping_relation",
            "ck_disease_surveillance_series_comparability",
            "ck_disease_surveillance_series_aggregation_policy",
            "ck_disease_surveillance_series_availability",
            "ck_disease_surveillance_series_missing_value_policy",
            "ck_disease_surveillance_series_valid_range",
        },
        DiseaseSeriesObservation: {
            "ck_disease_series_observation_geography_key",
            "ck_disease_series_observation_dimension_key",
            "ck_disease_series_observation_unit",
            "ck_disease_series_observation_value_or_suppressed",
            "ck_disease_series_observation_quality_status",
        },
        DiseaseSourceAvailability: {
            "ck_disease_source_availability_target_kind",
            "ck_disease_source_availability_status",
            "ck_disease_source_availability_missing_policy",
            "ck_disease_source_availability_valid_range",
            "ck_disease_source_availability_series_required",
        },
    }

    for model, names in expected_checks.items():
        assert names <= _constraint_names(model, CheckConstraint)

    assert DiseaseTaxonomyNode.__table__.c.facet.nullable is False
    assert DiseaseConceptAssignment.__table__.c.mapping_relation.nullable is False
    assert DiseaseConceptRelation.__table__.c.comparability.nullable is False
    assert DiseaseConceptRelation.__table__.c.aggregation_policy.nullable is False
    assert DiseaseSurveillanceSeries.__table__.c.mapping_relation.nullable is False
    assert DiseaseSurveillanceSeries.__table__.c.comparability.nullable is False
    assert DiseaseSurveillanceSeries.__table__.c.aggregation_policy.nullable is False
    assert DiseaseSeriesObservation.__table__.c.quality_status.nullable is False
    assert DiseaseSeriesObservation.__table__.c.raw_data.nullable is False
    assert DiseaseSeriesObservation.__table__.c.metadata.nullable is False
    assert DiseaseSourceAvailability.__table__.c.status.nullable is False
    assert DiseaseSourceAvailability.__table__.c.metadata.nullable is False


def test_source_availability_preserves_positive_and_negative_assertions() -> None:
    table = DiseaseSourceAvailability.__table__

    assert table.c.availability_code.unique is True
    assert table.c.series_code.nullable is True
    assert table.c.status.default.arg == "not_assessed"
    assert table.c.missing_value_policy.default.arg == "missing_is_unknown"

    required_series = next(
        constraint
        for constraint in table.constraints
        if constraint.name == "ck_disease_source_availability_series_required"
    )
    required_series_sql = str(required_series.sqltext)
    assert "'available'" in required_series_sql
    assert "'upstream_available_ingestion_pending'" in required_series_sql
    assert "series_code IS NOT NULL" in required_series_sql
    assert "not_reported_by_source" not in required_series_sql

    status_constraint = next(
        constraint
        for constraint in table.constraints
        if constraint.name == "ck_disease_source_availability_status"
    )
    status_sql = str(status_constraint.sqltext)
    for status in (
        "available",
        "upstream_available_ingestion_pending",
        "not_reported_by_source",
        "planned",
        "not_assessed",
        "parser_blocked",
        "mapping_missing",
    ):
        assert f"'{status}'" in status_sql

    index_columns = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    assert index_columns["idx_disease_source_availability_source_country"] == (
        "source_system",
        "country_code",
    )
    assert index_columns["idx_disease_source_availability_target"] == (
        "target_kind",
        "target_code",
    )


def test_surveillance_series_identity_is_source_and_definition_version_aware() -> None:
    constraint = next(
        item
        for item in DiseaseSurveillanceSeries.__table__.constraints
        if item.name == "uq_disease_surveillance_series_source_version"
    )
    assert tuple(column.name for column in constraint.columns) == (
        "source_system",
        "country_code",
        "source_series_code",
        "metric_type",
        "definition_version",
    )

    assert DiseaseSurveillanceSeries.__table__.c.series_code.unique is True
    assert DiseaseSurveillanceSeries.__table__.c.disease_id.nullable is True
    assert DiseaseSurveillanceSeries.__table__.c.target_group_code.nullable is True
    assert DiseaseSurveillanceSeries.__table__.c.missing_value_policy.default.arg == (
        "missing_is_unknown"
    )


def test_series_observation_identity_preserves_series_and_strata() -> None:
    constraint = next(
        item
        for item in DiseaseSeriesObservation.__table__.constraints
        if item.name == "uq_disease_series_observation_identity"
    )
    assert tuple(column.name for column in constraint.columns) == (
        "time",
        "series_code",
        "geography_key",
        "dimension_key",
    )

    assert DiseaseSeriesObservation.__table__.c.time.nullable is False
    assert DiseaseSeriesObservation.__table__.c.series_code.nullable is False
    assert DiseaseSeriesObservation.__table__.c.geography_key.nullable is False
    assert DiseaseSeriesObservation.__table__.c.dimension_key.nullable is False
    assert DiseaseSeriesObservation.__table__.c.value.nullable is True
    assert DiseaseSeriesObservation.__table__.c.unit.nullable is False
    assert DiseaseSeriesObservation.__table__.c.suppressed.default.arg is False
    assert DiseaseSeriesObservation.__table__.c.quality_status.default.arg == "raw"

    index_columns = {
        index.name: tuple(column.name for column in index.columns)
        for index in DiseaseSeriesObservation.__table__.indexes
    }
    assert index_columns["idx_disease_series_observation_series_time"] == (
        "series_code",
        "time",
    )
    assert index_columns["idx_disease_series_observation_geography_time"] == (
        "geography_key",
        "time",
    )
    assert index_columns["idx_disease_series_observation_identity_time"] == (
        "series_code",
        "geography_key",
        "dimension_key",
        "time",
    )


def test_all_ontology_tables_compile_for_postgresql() -> None:
    for model in (
        DiseaseTaxonomyNode,
        DiseaseTaxonomyEdge,
        DiseaseConceptAssignment,
        DiseaseConceptRelation,
        DiseaseSurveillanceSeries,
        DiseaseSourceAvailability,
        DiseaseSeriesObservation,
    ):
        ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert f"CREATE TABLE {model.__tablename__}" in ddl
        assert "metadata JSON" in ddl
