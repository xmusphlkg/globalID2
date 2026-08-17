"""Disease ontology, faceted taxonomy, and surveillance-series models.

These tables form an additive compatibility layer around the existing
``standard_diseases`` catalogue.  A standard D-code remains the stable concept
identifier while taxonomy nodes, typed concept relations, and source-specific
series definitions carry semantics that do not fit in a flat disease row.

Taxonomy edges deliberately model a directed, multi-parent graph.  The local
check constraint prevents self-loops; migration/service code must additionally
reject longer cycles before inserting an edge because portable SQL constraints
cannot enforce full DAG reachability.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


MAPPING_RELATION_VALUES = (
    "exact",
    "narrower",
    "broader",
    "related",
    "aggregate",
    "ambiguous",
    "unmapped",
)

COMPARABILITY_VALUES = (
    "direct",
    "conditional",
    "not_comparable",
    "unknown",
)

AGGREGATION_POLICY_VALUES = (
    "none",
    "direct_only",
    "reported_aggregate",
    "sum_disjoint",
    "non_additive",
)

ASSERTION_STATUS_VALUES = (
    "proposed",
    "approved",
    "rejected",
    "deprecated",
)

SERIES_AVAILABILITY_VALUES = (
    "active",
    "historical",
    "discontinued",
    "not_available",
    "unknown",
)

MISSING_VALUE_POLICY_VALUES = (
    "missing_is_unknown",
    "explicit_zero_only",
    "silence_means_zero",
    "suppressed",
    "not_applicable",
)

OBSERVATION_QUALITY_STATUS_VALUES = (
    "raw",
    "validated",
    "provisional",
    "revised",
    "final",
    "rejected",
)

SOURCE_AVAILABILITY_TARGET_KIND_VALUES = (
    "concept",
    "group",
)

SOURCE_AVAILABILITY_STATUS_VALUES = (
    "available",
    "upstream_available_ingestion_pending",
    "not_reported_by_source",
    "planned",
    "not_assessed",
    "parser_blocked",
    "mapping_missing",
)


def _quoted_values(values: tuple[str, ...]) -> str:
    """Return a SQL literal list for a portable ``CHECK ... IN`` clause."""

    return ", ".join(f"'{value}'" for value in values)


class DiseaseTaxonomyNode(BaseModel):
    """One node in a named, faceted disease taxonomy.

    ``node_code`` is globally stable and is used by edges and assignments as a
    string foreign key.  ``facet`` keeps independent dimensions (for example
    aetiology, clinical course, organ system, or surveillance family) separate
    without forcing a disease into one exclusive category.
    """

    __tablename__ = "disease_taxonomy_nodes"

    node_code: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    taxonomy_code: Mapped[str] = mapped_column(String(80), nullable=False)
    facet: Mapped[str] = mapped_column(String(80), nullable=False)
    node_type: Mapped[str] = mapped_column(String(40), nullable=False, default="value")
    label_en: Mapped[str] = mapped_column(String(240), nullable=False)
    label_zh: Mapped[Optional[str]] = mapped_column(String(240))
    description: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "taxonomy_code",
            "facet",
            "label_en",
            name="uq_disease_taxonomy_node_label",
        ),
        Index("idx_disease_taxonomy_node_taxonomy_facet", "taxonomy_code", "facet"),
        Index("idx_disease_taxonomy_node_active", "is_active"),
    )


class DiseaseTaxonomyEdge(BaseModel):
    """Directed parent-to-child edge supporting a multi-parent taxonomy DAG."""

    __tablename__ = "disease_taxonomy_edges"

    parent_node_code: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("disease_taxonomy_nodes.node_code", ondelete="CASCADE"),
        nullable=False,
    )
    child_node_code: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("disease_taxonomy_nodes.node_code", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="broader_narrower"
    )
    aggregation_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "parent_node_code",
            "child_node_code",
            "relation_type",
            name="uq_disease_taxonomy_edge",
        ),
        CheckConstraint(
            "parent_node_code <> child_node_code",
            name="ck_disease_taxonomy_edge_no_self_loop",
        ),
        CheckConstraint(
            f"aggregation_policy IN ({_quoted_values(AGGREGATION_POLICY_VALUES)})",
            name="ck_disease_taxonomy_edge_aggregation_policy",
        ),
        Index("idx_disease_taxonomy_edge_parent", "parent_node_code"),
        Index("idx_disease_taxonomy_edge_child", "child_node_code"),
        Index("idx_disease_taxonomy_edge_active", "is_active"),
    )


class DiseaseConceptAssignment(BaseModel):
    """Evidence-bearing assignment of a standard disease concept to a facet."""

    __tablename__ = "disease_concept_assignments"

    disease_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"),
        nullable=False,
    )
    node_code: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("disease_taxonomy_nodes.node_code", ondelete="CASCADE"),
        nullable=False,
    )
    mapping_relation: Mapped[str] = mapped_column(
        String(30), nullable=False, default="exact"
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    assertion_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="proposed"
    )
    asserted_by: Mapped[Optional[str]] = mapped_column(String(120))
    source_name: Mapped[Optional[str]] = mapped_column(String(200))
    source_uri: Mapped[Optional[str]] = mapped_column(String(1000))
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "disease_id",
            "node_code",
            name="uq_disease_concept_assignment",
        ),
        CheckConstraint(
            f"mapping_relation IN ({_quoted_values(MAPPING_RELATION_VALUES)})",
            name="ck_disease_concept_assignment_mapping_relation",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_disease_concept_assignment_confidence",
        ),
        CheckConstraint(
            f"assertion_status IN ({_quoted_values(ASSERTION_STATUS_VALUES)})",
            name="ck_disease_concept_assignment_status",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_disease_concept_assignment_valid_range",
        ),
        Index("idx_disease_concept_assignment_disease", "disease_id"),
        Index("idx_disease_concept_assignment_node", "node_code"),
        Index("idx_disease_concept_assignment_status", "assertion_status"),
    )


class DiseaseConceptRelation(BaseModel):
    """Typed relationship between two standard disease concepts.

    Examples include ``is_a``, ``stage_of``, ``caused_by``, ``replaced_by``,
    and ``overlaps_with``.  Comparability and aggregation are explicit because
    a clinically related pair is not necessarily safe to merge or sum.
    """

    __tablename__ = "disease_concept_relations"

    subject_disease_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"),
        nullable=False,
    )
    object_disease_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(60), nullable=False)
    comparability: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown"
    )
    aggregation_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="non_additive"
    )
    is_hierarchical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    assertion_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="proposed"
    )
    asserted_by: Mapped[Optional[str]] = mapped_column(String(120))
    source_name: Mapped[Optional[str]] = mapped_column(String(200))
    source_uri: Mapped[Optional[str]] = mapped_column(String(1000))
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "subject_disease_id",
            "relation_type",
            "object_disease_id",
            name="uq_disease_concept_relation",
        ),
        CheckConstraint(
            "subject_disease_id <> object_disease_id",
            name="ck_disease_concept_relation_no_self_loop",
        ),
        CheckConstraint(
            f"comparability IN ({_quoted_values(COMPARABILITY_VALUES)})",
            name="ck_disease_concept_relation_comparability",
        ),
        CheckConstraint(
            f"aggregation_policy IN ({_quoted_values(AGGREGATION_POLICY_VALUES)})",
            name="ck_disease_concept_relation_aggregation_policy",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_disease_concept_relation_confidence",
        ),
        CheckConstraint(
            f"assertion_status IN ({_quoted_values(ASSERTION_STATUS_VALUES)})",
            name="ck_disease_concept_relation_status",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_disease_concept_relation_valid_range",
        ),
        Index("idx_disease_concept_relation_subject", "subject_disease_id"),
        Index("idx_disease_concept_relation_object", "object_disease_id"),
        Index("idx_disease_concept_relation_type", "relation_type"),
    )


class DiseaseSurveillanceSeries(BaseModel):
    """Versioned definition of one source-specific surveillance series.

    The row records what a source actually reports, separately from the target
    disease concept.  This allows an aggregate, narrower, or otherwise
    non-comparable series to remain visible without pretending it is an exact
    alias.  Future observation tables can reference ``series_code`` while the
    existing ``disease_records`` table continues to use its current keys.
    """

    __tablename__ = "disease_surveillance_series"

    series_code: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    disease_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        ForeignKey("standard_diseases.disease_id", ondelete="RESTRICT"),
    )
    target_group_code: Mapped[Optional[str]] = mapped_column(String(120))
    country_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("countries.code", ondelete="RESTRICT"),
        nullable=False,
    )
    scope_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        ForeignKey("country_scopes.scope_code", ondelete="SET NULL"),
    )
    source_system: Mapped[str] = mapped_column(String(160), nullable=False)
    source_series_code: Mapped[str] = mapped_column(String(240), nullable=False)
    source_label: Mapped[str] = mapped_column(String(500), nullable=False)
    definition_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default="1"
    )
    case_definition: Mapped[Optional[str]] = mapped_column(Text)
    case_definition_uri: Mapped[Optional[str]] = mapped_column(String(1000))
    metric_type: Mapped[str] = mapped_column(
        String(60), nullable=False, default="cases"
    )
    reporting_basis: Mapped[str] = mapped_column(
        String(60), nullable=False, default="notification"
    )
    temporal_granularity: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unknown"
    )
    unit: Mapped[str] = mapped_column(String(60), nullable=False, default="count")
    mapping_relation: Mapped[str] = mapped_column(
        String(30), nullable=False, default="exact"
    )
    comparability: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown"
    )
    aggregation_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="direct_only"
    )
    availability_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )
    missing_value_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="missing_is_unknown"
    )
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "country_code",
            "source_series_code",
            "metric_type",
            "definition_version",
            name="uq_disease_surveillance_series_source_version",
        ),
        CheckConstraint(
            "(disease_id IS NOT NULL AND target_group_code IS NULL) OR "
            "(disease_id IS NULL AND target_group_code IS NOT NULL)",
            name="ck_disease_surveillance_series_exactly_one_target",
        ),
        CheckConstraint(
            f"mapping_relation IN ({_quoted_values(MAPPING_RELATION_VALUES)})",
            name="ck_disease_surveillance_series_mapping_relation",
        ),
        CheckConstraint(
            f"comparability IN ({_quoted_values(COMPARABILITY_VALUES)})",
            name="ck_disease_surveillance_series_comparability",
        ),
        CheckConstraint(
            f"aggregation_policy IN ({_quoted_values(AGGREGATION_POLICY_VALUES)})",
            name="ck_disease_surveillance_series_aggregation_policy",
        ),
        CheckConstraint(
            f"availability_status IN ({_quoted_values(SERIES_AVAILABILITY_VALUES)})",
            name="ck_disease_surveillance_series_availability",
        ),
        CheckConstraint(
            f"missing_value_policy IN ({_quoted_values(MISSING_VALUE_POLICY_VALUES)})",
            name="ck_disease_surveillance_series_missing_value_policy",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_disease_surveillance_series_valid_range",
        ),
        Index("idx_disease_surveillance_series_disease", "disease_id"),
        Index("idx_disease_surveillance_series_group", "target_group_code"),
        Index("idx_disease_surveillance_series_country", "country_code"),
        Index("idx_disease_surveillance_series_scope", "scope_code"),
        Index("idx_disease_surveillance_series_source", "source_system"),
        Index("idx_disease_surveillance_series_active", "is_active"),
    )


class DiseaseSourceAvailability(BaseModel):
    """Versioned availability assertion for a source and ontology target.

    Availability is modeled independently from surveillance series so a
    negative fact such as "US NNDSS does not report HIV" remains queryable
    even though there is intentionally no series row to reference.

    ``target_kind`` and ``target_code`` form a deliberately lightweight
    polymorphic reference to either a standard disease concept or an ontology
    group.  Validation/synchronization code owns target existence checks;
    avoiding a conditional foreign key keeps the database model portable.
    """

    __tablename__ = "disease_source_availability"

    availability_code: Mapped[str] = mapped_column(
        String(180), nullable=False, unique=True
    )
    source_system: Mapped[str] = mapped_column(String(160), nullable=False)
    country_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("countries.code", ondelete="RESTRICT"),
        nullable=False,
    )
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_code: Mapped[str] = mapped_column(String(120), nullable=False)
    series_code: Mapped[Optional[str]] = mapped_column(
        String(180),
        ForeignKey("disease_surveillance_series.series_code", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(
        String(60), nullable=False, default="not_assessed"
    )
    reason_code: Mapped[Optional[str]] = mapped_column(String(160))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    missing_value_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="missing_is_unknown"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            f"target_kind IN ({_quoted_values(SOURCE_AVAILABILITY_TARGET_KIND_VALUES)})",
            name="ck_disease_source_availability_target_kind",
        ),
        CheckConstraint(
            f"status IN ({_quoted_values(SOURCE_AVAILABILITY_STATUS_VALUES)})",
            name="ck_disease_source_availability_status",
        ),
        CheckConstraint(
            f"missing_value_policy IN ({_quoted_values(MISSING_VALUE_POLICY_VALUES)})",
            name="ck_disease_source_availability_missing_policy",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_disease_source_availability_valid_range",
        ),
        CheckConstraint(
            "status NOT IN ('available', "
            "'upstream_available_ingestion_pending') OR series_code IS NOT NULL",
            name="ck_disease_source_availability_series_required",
        ),
        Index(
            "idx_disease_source_availability_source_country",
            "source_system",
            "country_code",
        ),
        Index(
            "idx_disease_source_availability_target",
            "target_kind",
            "target_code",
        ),
        Index("idx_disease_source_availability_series", "series_code"),
        Index(
            "idx_disease_source_availability_status",
            "status",
            "is_active",
        ),
    )


class DiseaseSeriesObservation(BaseModel):
    """One source-series value at a time, place, and stratification.

    Unlike the legacy ``disease_records`` identity, this table retains the
    source series in its natural key.  Confirmed, probable, aggregate, and
    other separately reported source series therefore cannot overwrite each
    other merely because they map to the same standard disease concept.

    ``geography_key`` is a stable source-aware geography identifier (for
    example ``national`` or ``state:CA``).  ``dimension_key`` is the canonical
    identity for any additional strata, while ``dimensions`` keeps their
    structured values for querying and display.
    """

    __tablename__ = "disease_series_observations"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    series_code: Mapped[str] = mapped_column(
        String(180),
        ForeignKey("disease_surveillance_series.series_code", ondelete="RESTRICT"),
        nullable=False,
    )
    geography_key: Mapped[str] = mapped_column(String(240), nullable=False)
    dimension_key: Mapped[str] = mapped_column(
        String(500), nullable=False, default="all"
    )
    dimensions = Column(JSON, nullable=False, default=dict)
    value: Mapped[Optional[float]] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(80), nullable=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suppression_reason: Mapped[Optional[str]] = mapped_column(String(240))
    quality_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="raw"
    )
    raw_data = Column(JSON, nullable=False, default=dict)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "time",
            "series_code",
            "geography_key",
            "dimension_key",
            name="uq_disease_series_observation_identity",
        ),
        CheckConstraint(
            "geography_key <> ''",
            name="ck_disease_series_observation_geography_key",
        ),
        CheckConstraint(
            "dimension_key <> ''",
            name="ck_disease_series_observation_dimension_key",
        ),
        CheckConstraint(
            "unit <> ''",
            name="ck_disease_series_observation_unit",
        ),
        CheckConstraint(
            "suppressed OR value IS NOT NULL",
            name="ck_disease_series_observation_value_or_suppressed",
        ),
        CheckConstraint(
            f"quality_status IN ({_quoted_values(OBSERVATION_QUALITY_STATUS_VALUES)})",
            name="ck_disease_series_observation_quality_status",
        ),
        Index(
            "idx_disease_series_observation_series_time",
            "series_code",
            "time",
        ),
        Index(
            "idx_disease_series_observation_geography_time",
            "geography_key",
            "time",
        ),
        Index(
            "idx_disease_series_observation_identity_time",
            "series_code",
            "geography_key",
            "dimension_key",
            "time",
        ),
        Index(
            "idx_disease_series_observation_quality",
            "quality_status",
            "suppressed",
        ),
    )


__all__ = [
    "AGGREGATION_POLICY_VALUES",
    "ASSERTION_STATUS_VALUES",
    "COMPARABILITY_VALUES",
    "MAPPING_RELATION_VALUES",
    "MISSING_VALUE_POLICY_VALUES",
    "OBSERVATION_QUALITY_STATUS_VALUES",
    "SERIES_AVAILABILITY_VALUES",
    "SOURCE_AVAILABILITY_STATUS_VALUES",
    "SOURCE_AVAILABILITY_TARGET_KIND_VALUES",
    "DiseaseTaxonomyNode",
    "DiseaseTaxonomyEdge",
    "DiseaseConceptAssignment",
    "DiseaseConceptRelation",
    "DiseaseSurveillanceSeries",
    "DiseaseSourceAvailability",
    "DiseaseSeriesObservation",
]
