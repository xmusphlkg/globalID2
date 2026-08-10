"""Reproducible old-versus-release-pinned disease mapping quality audit."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.disease_mapping_registry_service import disease_mapping_registry_service


class DiseaseMappingAuditService:
    async def run(self, db: AsyncSession) -> dict[str, Any]:
        await disease_mapping_registry_service.ensure_schema(db)
        country_rows = (
            await db.execute(
                text(
                    """
                    WITH newmap AS (
                        SELECT observation_id, target_code
                        FROM effective_disease_series_observations_v3
                        WHERE is_canonical_projection
                    ), comparison AS (
                        SELECT o.id, s.country_code, s.disease_id AS old_target,
                               n.target_code AS new_target, s.mapping_relation,
                               s.comparability
                        FROM disease_series_observations o
                        JOIN disease_surveillance_series s ON s.series_code=o.series_code
                        LEFT JOIN newmap n ON n.observation_id=o.id
                    )
                    SELECT country_code,
                           COUNT(*) AS observations,
                           COUNT(*) FILTER (WHERE old_target IS NOT NULL) AS old_mapped,
                           COUNT(*) FILTER (WHERE new_target IS NOT NULL) AS v3_mapped,
                           COUNT(*) FILTER (WHERE old_target=new_target) AS same_target,
                           COUNT(*) FILTER (
                               WHERE old_target IS NOT NULL AND new_target IS NULL
                           ) AS old_only,
                           COUNT(*) FILTER (
                               WHERE old_target IS NULL AND new_target IS NOT NULL
                           ) AS v3_only,
                           COUNT(*) FILTER (
                               WHERE old_target IS NOT NULL AND new_target IS NOT NULL
                                 AND old_target<>new_target
                           ) AS changed_target,
                           COUNT(*) FILTER (
                               WHERE old_target IS NOT NULL AND new_target IS NULL
                                 AND mapping_relation='exact'
                           ) AS exact_migration_gap,
                           COUNT(*) FILTER (
                               WHERE old_target IS NOT NULL AND new_target IS NULL
                                 AND mapping_relation<>'exact'
                           ) AS semantic_safety_exclusion,
                           COUNT(*) FILTER (
                               WHERE old_target IS NULL AND new_target IS NULL
                           ) AS source_native_unreviewed
                    FROM comparison
                    GROUP BY country_code ORDER BY country_code
                    """
                )
            )
        ).mappings().all()
        countries = [dict(row) for row in country_rows]
        totals = {
            key: sum(int(row[key] or 0) for row in countries)
            for key in (
                "observations", "old_mapped", "v3_mapped", "same_target", "old_only",
                "v3_only", "changed_target", "exact_migration_gap",
                "semantic_safety_exclusion", "source_native_unreviewed",
            )
        }
        totals["old_coverage"] = round(totals["old_mapped"] / totals["observations"], 6) if totals["observations"] else 0.0
        totals["v3_coverage"] = round(totals["v3_mapped"] / totals["observations"], 6) if totals["observations"] else 0.0

        gap_rows = (
            await db.execute(
                text(
                    """
                    WITH newmap AS (
                        SELECT DISTINCT observation_id
                        FROM effective_disease_series_observations_v3
                        WHERE is_canonical_projection
                    )
                    SELECT s.country_code, s.series_code, s.source_label,
                           s.disease_id AS old_target, s.mapping_relation,
                           s.comparability, s.aggregation_policy,
                           COUNT(*) AS observations,
                           CASE WHEN s.mapping_relation='exact'
                                THEN 'exact_migration_gap'
                                ELSE 'semantic_safety_exclusion' END AS root_cause
                    FROM disease_series_observations o
                    JOIN disease_surveillance_series s ON s.series_code=o.series_code
                    LEFT JOIN newmap n ON n.observation_id=o.id
                    WHERE s.disease_id IS NOT NULL AND n.observation_id IS NULL
                    GROUP BY s.country_code, s.series_code, s.source_label,
                             s.disease_id, s.mapping_relation, s.comparability,
                             s.aggregation_policy
                    ORDER BY observations DESC, s.country_code, s.series_code
                    LIMIT 100
                    """
                )
            )
        ).mappings().all()

        gates = (
            await db.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM disease_series_observations o
                       JOIN disease_surveillance_series s ON s.series_code=o.series_code
                       LEFT JOIN source_disease_categories c
                         ON c.source_id=s.source_system
                        AND c.definition_version=s.definition_version
                        AND c.source_code=COALESCE(
                            NULLIF(o.dimensions->>'source_disease_code',''),
                            NULLIF(o.metadata->>'local_code',''),s.source_series_code)
                        AND c.is_active=true
                       WHERE c.id IS NULL) AS orphan_observations,
                      (SELECT COUNT(*) FROM (
                         SELECT a.category_id
                         FROM disease_mapping_release_items_v3 i
                         JOIN disease_mapping_releases_v3 r
                           ON r.id=i.release_id AND r.status='active'
                         JOIN disease_mapping_assertions_v3 a ON a.id=i.assertion_id
                         WHERE a.valid_from IS NULL AND a.valid_to IS NULL
                         GROUP BY a.category_id HAVING COUNT(*)>1
                       ) conflicts) AS active_release_conflicts,
                      (SELECT release_code FROM disease_mapping_releases_v3
                       WHERE status='active' LIMIT 1) AS active_release
                    """
                )
            )
        ).mappings().one()
        quality_gates = {
            **dict(gates),
            "no_unreviewed_target_changes": totals["changed_target"] == 0,
            "no_orphan_observations": int(gates["orphan_observations"] or 0) == 0,
            "single_mapping_per_category": int(gates["active_release_conflicts"] or 0) == 0,
        }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "totals": totals,
            "countries": countries,
            "top_gaps": [dict(row) for row in gap_rows],
            "quality_gates": quality_gates,
            "root_causes": [
                {
                    "code": "exact_migration_gap",
                    "observations": totals["exact_migration_gap"],
                    "meaning": "Reviewed exact series target was not attached to the observed source category identity.",
                },
                {
                    "code": "semantic_safety_exclusion",
                    "observations": totals["semantic_safety_exclusion"],
                    "meaning": "Legacy projection used an aggregate, broader, related, or otherwise non-exact series mapping.",
                },
                {
                    "code": "source_native_unreviewed",
                    "observations": totals["source_native_unreviewed"],
                    "meaning": "The source category has no legacy concept and still requires AI-assisted review.",
                },
            ],
        }


disease_mapping_audit_service = DiseaseMappingAuditService()


__all__ = ["DiseaseMappingAuditService", "disease_mapping_audit_service"]
