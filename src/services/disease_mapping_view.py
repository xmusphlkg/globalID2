"""Fixed definition of the effective v3 mapping view.

A holding series is created from SourceDiseaseCode. The optional disease code
inside its dimensions may only be a component of that source identity. Existing
non-holding observation overrides keep their historical precedence.

Changing this definition does not implicitly replace a live view. Use the
explicit hash-checked installation command during a controlled transaction.
"""
from __future__ import annotations


EFFECTIVE_MAPPING_VIEW_NAME = "effective_disease_series_observations_v3"


def effective_mapping_view_sql() -> str:
    return """CREATE OR REPLACE VIEW effective_disease_series_observations_v3 AS
SELECT
    o.id AS observation_id,
    o.time,
    o.series_code,
    o.geography_key,
    o.dimension_key,
    o.dimensions,
    o.value,
    o.unit,
    o.suppressed,
    o.suppression_reason,
    o.quality_status,
    o.raw_data,
    o.metadata AS observation_metadata,
    s.country_code,
    s.source_system,
    c.id AS source_category_id,
    c.category_key,
    c.source_code,
    c.canonical_source_label,
    r.id AS mapping_release_id,
    r.release_code AS mapping_release_code,
    a.id AS mapping_assertion_id,
    a.target_kind,
    a.target_code,
    a.mapping_relation,
    a.comparability,
    a.projection_policy,
    a.aggregation_policy,
    (
        a.target_kind = 'concept'
        AND a.projection_policy = 'canonical'
        AND a.assertion_status = 'approved'
    ) AS is_canonical_projection
FROM disease_series_observations o
JOIN disease_surveillance_series s ON s.series_code = o.series_code
JOIN LATERAL (
    SELECT category.*
    FROM source_disease_categories category
    WHERE category.source_id = s.source_system
      AND category.is_active = true
      AND (
            (
                category.definition_version = s.definition_version
                AND category.source_code = COALESCE(
                      CASE WHEN s.metadata->>'dynamic_holding' = 'true'
                           THEN NULLIF(o.metadata->>'local_code', '') END,
                      NULLIF(o.dimensions->>'source_disease_code', ''),
                      NULLIF(o.metadata->>'local_code', ''),
                      s.source_series_code
                    )
            )
            OR (
                category.definition_version = 'source-current'
                AND category.source_code LIKE 'label:%'
                AND lower(btrim(category.canonical_source_label)) = lower(btrim(
                      COALESCE(
                          NULLIF(o.dimensions->>'source_disease_label', ''),
                          NULLIF(o.metadata->>'local_label', ''),
                          s.source_label
                      )
                    ))
            )
          )
    ORDER BY CASE
        WHEN category.definition_version = s.definition_version
         AND category.source_code = COALESCE(
               CASE WHEN s.metadata->>'dynamic_holding' = 'true'
                    THEN NULLIF(o.metadata->>'local_code', '') END,
               NULLIF(o.dimensions->>'source_disease_code', ''),
               NULLIF(o.metadata->>'local_code', ''),
               s.source_series_code
             ) THEN 0
        ELSE 1
    END,
    category.id DESC
    LIMIT 1
) c ON true
JOIN disease_mapping_releases_v3 r ON r.status = 'active'
JOIN disease_mapping_release_items_v3 ri ON ri.release_id = r.id
JOIN disease_mapping_assertions_v3 a
  ON a.id = ri.assertion_id
 AND a.category_id = c.id
 AND a.assertion_status = 'approved'
 AND (a.valid_from IS NULL OR o.time::date >= a.valid_from)
 AND (a.valid_to IS NULL OR o.time::date <= a.valid_to)
"""
