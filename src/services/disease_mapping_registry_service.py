"""Source-first disease mapping registry, review, and release orchestration."""

from __future__ import annotations

import hashlib
import html
import json
import unicodedata
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import func, inspect as sa_inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.domain import (
    DiseaseMappingAssertion,
    DiseaseMappingCandidate,
    DiseaseMappingRelease,
    DiseaseMappingReleaseItem,
    DiseaseSurveillanceSeries,
    MappingNotificationOutbox,
    SourceDiseaseCategory,
    SourceDiseaseCategoryAlias,
    StandardDisease,
)
from src.ontology import load_disease_ontology
from src.services.settings_service import system_settings_service

logger = get_logger(__name__)

PROMPT_VERSION = "disease-mapping-v3.5"
UNMAPPED_GROUP = "G_UNMAPPED_SOURCE_CATEGORIES"


def normalize_source_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "").replace("\ufeff", ""))
    return " ".join(normalized.split()).strip().casefold()


def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _category_key(source_id: str, source_code: str, definition_version: str) -> str:
    identity = f"{source_id}\x1f{source_code}\x1f{definition_version}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    safe_source = "".join(ch if ch.isalnum() else "_" for ch in source_id.upper()).strip("_")
    return f"CAT_{safe_source}_{digest}"


def _assertion_key(category_key: str, target_kind: str, target_code: str, relation: str) -> str:
    raw = f"{category_key}\x1f{target_kind}\x1f{target_code}\x1f{relation}"
    return f"MAP_{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def _candidate_key(category_key: str, method: str, target: str, rank: int) -> str:
    raw = f"{category_key}\x1f{method}\x1f{target}\x1f{rank}\x1f{PROMPT_VERSION}"
    return f"CAND_{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def _model_dict(instance: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for attribute in sa_inspect(instance).mapper.column_attrs:
        value = getattr(instance, attribute.key)
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        result["metadata" if attribute.key == "metadata_" else attribute.key] = value
    return result


def _source_id_for_row(row: Mapping[str, Any], source_id: str | Mapping[str, str] | None) -> str:
    if isinstance(source_id, Mapping):
        source_name = normalize_source_text(row.get("Source") or row.get("DataSource"))
        for label, resolved in source_id.items():
            if normalize_source_text(label) == source_name:
                return str(resolved).strip().upper()
        return ""
    return str(source_id or "").strip().upper()


class DiseaseMappingRegistryService:
    """Owns category discovery and immutable mapping releases."""

    _schema_ready = False

    async def ensure_schema(self, db: AsyncSession) -> None:
        if self._schema_ready:
            return
        tables = [
            SourceDiseaseCategory.__table__,
            SourceDiseaseCategoryAlias.__table__,
            DiseaseMappingCandidate.__table__,
            DiseaseMappingAssertion.__table__,
            DiseaseMappingRelease.__table__,
            DiseaseMappingReleaseItem.__table__,
            MappingNotificationOutbox.__table__,
        ]
        connection = await db.connection()
        await connection.run_sync(
            lambda sync_conn: SourceDiseaseCategory.metadata.create_all(
                sync_conn, tables=tables, checkfirst=True
            )
        )
        await db.execute(
            text(
                "ALTER TABLE source_disease_categories "
                "ADD COLUMN IF NOT EXISTS ai_next_attempt_at TIMESTAMPTZ"
            )
        )
        # Consumers query a release-pinned view instead of mutable crawler
        # labels or the legacy disease_id stored on a series definition.  A
        # release switch is therefore atomic and historical facts need no
        # destructive rewrite.
        await db.execute(
            text(
                """
                CREATE OR REPLACE VIEW effective_disease_series_observations_v3 AS
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
                JOIN source_disease_categories c
                  ON c.source_id = s.source_system
                 AND c.definition_version = s.definition_version
                 AND c.source_code = COALESCE(
                        NULLIF(o.dimensions->>'source_disease_code', ''),
                        NULLIF(o.metadata->>'local_code', ''),
                        s.source_series_code
                     )
                 AND c.is_active = true
                JOIN disease_mapping_releases_v3 r ON r.status = 'active'
                JOIN disease_mapping_release_items_v3 ri ON ri.release_id = r.id
                JOIN disease_mapping_assertions_v3 a
                  ON a.id = ri.assertion_id
                 AND a.category_id = c.id
                 AND a.assertion_status = 'approved'
                 AND (a.valid_from IS NULL OR o.time::date >= a.valid_from)
                 AND (a.valid_to IS NULL OR o.time::date <= a.valid_to)
                """
            )
        )
        self._schema_ready = True

    async def discover_rows(
        self,
        db: AsyncSession,
        *,
        country_code: str,
        source_id: str | Mapping[str, str] | None,
        rows: Iterable[Mapping[str, Any]],
        notify: bool = True,
        increment_occurrence: bool = True,
    ) -> dict[str, Any]:
        """Register every source category seen in a fact batch.

        The operation is transactionally coupled to fact ingestion.  Notification
        delivery is decoupled through the outbox and therefore cannot roll back
        source data after a mail-provider failure.
        """

        await self.ensure_schema(db)
        now = datetime.now(timezone.utc)
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for raw in rows:
            resolved_source = _source_id_for_row(raw, source_id)
            if not resolved_source:
                continue
            source_code = _first_text(
                raw, "SourceDiseaseCode", "source_disease_code", "DiseaseCode", "local_code", "Diseases"
            )
            label = _first_text(
                raw, "RawDiseaseLabel", "source_disease_label", "Disease", "DiseasesCN", "Diseases", "local_label"
            )
            if not source_code:
                source_code = f"label:{hashlib.sha256(normalize_source_text(label).encode()).hexdigest()[:20]}"
            if not label:
                label = source_code
            definition_version = _first_text(raw, "DefinitionVersion", "definition_version") or "source-current"
            key = (resolved_source, source_code, definition_version)
            item = grouped.setdefault(
                key,
                {
                    "label_counts": Counter(),
                    "count": 0,
                    "source_url": _first_text(raw, "SourceURL", "source_url"),
                    "definition": _first_text(raw, "CaseDefinition", "case_definition"),
                },
            )
            item["label_counts"][label] += 1
            item["count"] += 1

        new_categories: list[SourceDiseaseCategory] = []
        touched_ids: list[int] = []
        for (resolved_source, source_code, definition_version), item in sorted(grouped.items()):
            label = item["label_counts"].most_common(1)[0][0]
            category_key = _category_key(resolved_source, source_code, definition_version)
            existing = (
                await db.execute(
                    select(SourceDiseaseCategory).where(
                        SourceDiseaseCategory.source_id == resolved_source,
                        SourceDiseaseCategory.source_code == source_code,
                        SourceDiseaseCategory.definition_version == definition_version,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = SourceDiseaseCategory(
                    category_key=category_key,
                    country_code=country_code.upper(),
                    source_id=resolved_source,
                    source_code=source_code,
                    canonical_source_label=label,
                    normalized_label=normalize_source_text(label),
                    definition_version=definition_version,
                    source_definition=item["definition"] or None,
                    source_definition_uri=item["source_url"] or None,
                    status="discovered",
                    first_seen_at=now,
                    last_seen_at=now,
                    occurrence_count=int(item["count"]),
                    ai_status="pending",
                    metadata_={"discovered_by": "fact_ingestion_v3"},
                )
                db.add(existing)
                await db.flush()
                new_categories.append(existing)
            else:
                existing.last_seen_at = now
                if increment_occurrence:
                    existing.occurrence_count = int(existing.occurrence_count or 0) + int(item["count"])
                if not existing.canonical_source_label:
                    existing.canonical_source_label = label
                    existing.normalized_label = normalize_source_text(label)
            touched_ids.append(existing.id)

            for alias in sorted(item["label_counts"]):
                normalized_alias = normalize_source_text(alias)
                statement = pg_insert(SourceDiseaseCategoryAlias).values(
                    category_id=existing.id,
                    alias=alias,
                    normalized_alias=normalized_alias,
                    alias_type="observed",
                    metadata_={},
                    created_at=now,
                    updated_at=now.replace(tzinfo=None),
                ).on_conflict_do_nothing(
                    constraint="uq_source_disease_category_alias"
                )
                await db.execute(statement)

            if notify and existing in new_categories:
                await self._queue_new_category_notification(db, existing)

        return {
            "source_category_count": len(grouped),
            "new_category_count": len(new_categories),
            "new_category_ids": [item.id for item in new_categories],
            "touched_category_ids": touched_ids,
        }

    async def _queue_new_category_notification(
        self, db: AsyncSession, category: SourceDiseaseCategory
    ) -> None:
        recipients = list(system_settings_service.smtp_runtime().get("admin_emails_raw", "").split(","))
        recipients = [item.strip() for item in recipients if item.strip()]
        event_key = f"mapping-category-discovered:{category.category_key}"
        subject = f"[GIDS Mapping] New source disease category: {category.country_code}/{category.canonical_source_label}"
        body_text = (
            "A new source disease category was discovered and queued for AI mapping.\n\n"
            f"Country: {category.country_code}\nSource: {category.source_id}\n"
            f"Source code: {category.source_code}\nLabel: {category.canonical_source_label}\n"
            f"Category key: {category.category_key}\n"
        )
        body_html = (
            "<h2>New source disease category</h2><ul>"
            f"<li>Country: {html.escape(category.country_code)}</li>"
            f"<li>Source: {html.escape(category.source_id)}</li>"
            f"<li>Source code: {html.escape(category.source_code)}</li>"
            f"<li>Label: {html.escape(category.canonical_source_label)}</li>"
            f"<li>Category key: {html.escape(category.category_key)}</li></ul>"
            "<p>The category has been queued for AI-assisted semantic review.</p>"
        )
        await db.execute(
            pg_insert(MappingNotificationOutbox)
            .values(
                event_key=event_key,
                event_type="new_source_category",
                aggregate_key=f"mapping:{category.country_code}:new-category",
                recipients=recipients,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                provider="auto",
                status="pending" if recipients else "skipped",
                attempts=0,
                metadata_={"category_id": category.id, "category_key": category.category_key},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.utcnow(),
            )
            .on_conflict_do_nothing(index_elements=[MappingNotificationOutbox.event_key])
        )

    async def bootstrap_all_sources(self, db: AsyncSession) -> dict[str, Any]:
        """Import the complete current ontology and observed category inventory."""

        await self.ensure_schema(db)
        ontology = load_disease_ontology().to_dict()
        sources = {item["id"]: item for item in ontology["sources"]}
        imported_categories = imported_assertions = 0
        persisted_series = (
            await db.execute(select(DiseaseSurveillanceSeries))
        ).scalars().all()
        persisted_versions: dict[tuple[str, str], set[str]] = {}
        for persisted in persisted_series:
            for identity in {persisted.series_code, persisted.source_series_code}:
                persisted_versions.setdefault(
                    (persisted.source_system, identity), set()
                ).add(persisted.definition_version)

        for series in ontology["source_series"]:
            source = sources.get(series["source_id"], {})
            country = str(source.get("country_code") or "").upper()
            if not country:
                continue
            codes = list(series.get("local_codes") or []) or [series["id"]]
            labels = list(series.get("local_labels") or []) or codes
            for index, source_code in enumerate(codes):
                label = labels[min(index, len(labels) - 1)]
                # The runtime series definition version is authoritative.  The
                # old importer used the literal "ontology-current", producing
                # reviewed assertions that could not join to historical facts
                # whose series had a concrete ontology release version.
                definition_versions = set()
                for identity in {str(series["id"]), str(source_code)}:
                    definition_versions.update(
                        persisted_versions.get((series["source_id"], identity), set())
                    )
                if not definition_versions:
                    definition_versions.add(
                        str(series.get("definition_version") or "ontology-current")
                    )
                for definition_version in sorted(definition_versions):
                    result = await self.discover_rows(
                        db,
                        country_code=country,
                        source_id=series["source_id"],
                        rows=[{
                            "SourceDiseaseCode": source_code,
                            "RawDiseaseLabel": label,
                            "DefinitionVersion": definition_version,
                            "CaseDefinition": series.get("case_definition") or "",
                        }],
                        notify=False,
                        increment_occurrence=False,
                    )
                    imported_categories += result["new_category_count"]
                    category_id = result["touched_category_ids"][0]
                    target_code = series.get("concept_id") or series.get("group_id") or UNMAPPED_GROUP
                    target_kind = "concept" if series.get("concept_id") else "group"
                    relation = str(series.get("mapping_relation") or ("exact" if target_kind == "concept" else "unmapped"))
                    assertion = await self._upsert_imported_assertion(
                        db,
                        category_id=category_id,
                        target_kind=target_kind,
                        target_code=target_code,
                        relation=relation,
                        comparability=str(series.get("comparability") or "unknown"),
                        projection_policy=(
                            "canonical" if target_kind == "concept" and relation in {"exact", "narrower"}
                            else "no_projection"
                        ),
                        aggregation_policy=str(series.get("aggregation_policy") or "non_additive"),
                        evidence=[{
                            "type": "ontology_series",
                            "series_id": series["id"],
                            "runtime_definition_version": definition_version,
                        }],
                    )
                    if assertion:
                        imported_assertions += 1

        observed_rows = (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT
                        s.country_code,
                        s.source_system,
                        COALESCE(NULLIF(o.dimensions->>'source_disease_code',''),
                                 NULLIF(o.metadata->>'local_code',''),
                                 s.source_series_code) AS source_code,
                        COALESCE(NULLIF(o.dimensions->>'source_disease_label',''),
                                 NULLIF(o.metadata->>'local_label',''),
                                 s.source_label) AS source_label,
                        s.definition_version
                    FROM disease_series_observations o
                    JOIN disease_surveillance_series s ON s.series_code=o.series_code
                    """
                )
            )
        ).mappings().all()
        observed_new = 0
        for row in observed_rows:
            result = await self.discover_rows(
                db,
                country_code=row["country_code"],
                source_id=row["source_system"],
                rows=[{
                    "SourceDiseaseCode": row["source_code"],
                    "RawDiseaseLabel": row["source_label"],
                    "DefinitionVersion": row["definition_version"],
                }],
                notify=False,
                increment_occurrence=False,
            )
            observed_new += result["new_category_count"]

        inherited_assertions, inheritance_conflicts = await self._inherit_unambiguous_series_mappings(db)
        outdated_ai_requeued = await self._requeue_outdated_ai_candidates(db)

        return {
            "ontology_categories_created": imported_categories,
            "observed_categories_created": observed_new,
            "approved_assertions_imported": imported_assertions,
            "series_assertions_inherited": inherited_assertions,
            "series_inheritance_conflicts": inheritance_conflicts,
            "outdated_ai_requeued": outdated_ai_requeued,
            **(await self.stats(db)),
        }

    async def _requeue_outdated_ai_candidates(self, db: AsyncSession) -> int:
        category_ids = list(
            (
                await db.execute(
                    select(DiseaseMappingCandidate.category_id)
                    .join(
                        SourceDiseaseCategory,
                        SourceDiseaseCategory.id == DiseaseMappingCandidate.category_id,
                    )
                    .where(
                        SourceDiseaseCategory.ai_status == "completed",
                        DiseaseMappingCandidate.status == "proposed",
                        DiseaseMappingCandidate.prompt_version != PROMPT_VERSION,
                    )
                    .distinct()
                )
            ).scalars().all()
        )
        if not category_ids:
            return 0
        await db.execute(
            update(DiseaseMappingCandidate)
            .where(
                DiseaseMappingCandidate.category_id.in_(category_ids),
                DiseaseMappingCandidate.status == "proposed",
            )
            .values(status="stale", updated_at=datetime.utcnow())
        )
        await db.execute(
            update(SourceDiseaseCategory)
            .where(SourceDiseaseCategory.id.in_(category_ids))
            .values(
                ai_status="pending",
                ai_attempts=0,
                ai_next_attempt_at=None,
                ai_last_error=f"Requeued after mapping prompt upgrade to {PROMPT_VERSION}",
                updated_at=datetime.utcnow(),
            )
        )
        return len(category_ids)

    async def _inherit_unambiguous_series_mappings(
        self, db: AsyncSession
    ) -> tuple[int, list[dict[str, Any]]]:
        """Bridge reviewed series mappings to their observed source categories.

        Several older integrations use a stable internal series code while the
        observation carries a different official category code.  Copying the
        target blindly would recreate the old mapper's ambiguity.  Inheritance
        is therefore allowed only when every contributing series is an exact
        mapping and all of them point to one identical disease concept.
        """

        rows = (
            await db.execute(
                text(
                    """
                    SELECT
                        c.id AS category_id,
                        MIN(s.disease_id) AS target_code,
                        ARRAY_AGG(DISTINCT s.series_code ORDER BY s.series_code) AS series_codes,
                        ARRAY_AGG(DISTINCT s.comparability ORDER BY s.comparability) AS comparability_values,
                        ARRAY_AGG(DISTINCT s.aggregation_policy ORDER BY s.aggregation_policy) AS aggregation_values,
                        COUNT(*) AS observation_count
                    FROM disease_series_observations o
                    JOIN disease_surveillance_series s ON s.series_code=o.series_code
                    JOIN source_disease_categories c
                      ON c.source_id=s.source_system
                     AND c.definition_version=s.definition_version
                     AND c.source_code=COALESCE(
                           NULLIF(o.dimensions->>'source_disease_code',''),
                           NULLIF(o.metadata->>'local_code',''),
                           s.source_series_code
                         )
                    WHERE s.disease_id IS NOT NULL
                    GROUP BY c.id
                    HAVING COUNT(DISTINCT s.disease_id)=1
                       AND BOOL_AND(s.mapping_relation='exact')
                    ORDER BY c.id
                    """
                )
            )
        ).mappings().all()
        inherited = 0
        conflicts: list[dict[str, Any]] = []
        for row in rows:
            existing = (
                await db.execute(
                    select(DiseaseMappingAssertion).where(
                        DiseaseMappingAssertion.category_id == row["category_id"],
                        DiseaseMappingAssertion.assertion_status == "approved",
                    )
                )
            ).scalars().all()
            existing_targets = {
                item.target_code for item in existing if item.projection_policy == "canonical"
            }
            if existing_targets:
                if existing_targets != {row["target_code"]}:
                    conflicts.append(
                        {
                            "category_id": row["category_id"],
                            "existing_targets": sorted(existing_targets),
                            "series_target": row["target_code"],
                        }
                    )
                continue
            # A reviewed non-projectable assertion represents an intentional
            # semantic decision and must not be silently overridden.
            if existing:
                continue
            comparability_values = list(row["comparability_values"] or [])
            aggregation_values = list(row["aggregation_values"] or [])
            assertion = await self._upsert_imported_assertion(
                db,
                category_id=int(row["category_id"]),
                target_kind="concept",
                target_code=str(row["target_code"]),
                relation="exact",
                comparability=(
                    comparability_values[0] if len(comparability_values) == 1 else "unknown"
                ),
                projection_policy="canonical",
                aggregation_policy=(
                    aggregation_values[0] if len(aggregation_values) == 1 else "non_additive"
                ),
                evidence=[
                    {
                        "type": "unambiguous_series_inheritance",
                        "series_codes": list(row["series_codes"] or []),
                        "observation_count": int(row["observation_count"] or 0),
                    }
                ],
            )
            if assertion:
                inherited += 1
        return inherited, conflicts

    async def _upsert_imported_assertion(
        self,
        db: AsyncSession,
        *,
        category_id: int,
        target_kind: str,
        target_code: str,
        relation: str,
        comparability: str,
        projection_policy: str,
        aggregation_policy: str,
        evidence: list[dict[str, Any]],
    ) -> Optional[DiseaseMappingAssertion]:
        category = await db.get(SourceDiseaseCategory, category_id)
        if category is None:
            return None
        key = _assertion_key(category.category_key, target_kind, target_code, relation)
        existing = (
            await db.execute(
                select(DiseaseMappingAssertion).where(DiseaseMappingAssertion.assertion_key == key)
            )
        ).scalar_one_or_none()
        if existing:
            return None
        assertion = DiseaseMappingAssertion(
            assertion_key=key,
            category_id=category_id,
            target_kind=target_kind,
            target_code=target_code,
            mapping_relation=relation,
            comparability=comparability if comparability in {"direct", "conditional", "not_comparable", "unknown"} else "unknown",
            projection_policy=projection_policy,
            aggregation_policy=(
                aggregation_policy if aggregation_policy in {"direct_only", "reported_total", "sum_disjoint", "non_additive", "no_rollup"}
                else "non_additive"
            ),
            assertion_status="approved",
            confidence_score=1.0,
            suggestion_method="ontology_import",
            reasoning="Imported from the reviewed disease surveillance ontology.",
            evidence=evidence,
            reviewed_by="ontology_registry",
            reviewed_at=datetime.now(timezone.utc),
            metadata_={"bootstrap": True},
        )
        db.add(assertion)
        category.status = "active"
        category.ai_status = "not_required"
        category.ai_next_attempt_at = None
        await db.execute(
            update(DiseaseMappingCandidate)
            .where(
                DiseaseMappingCandidate.category_id == category_id,
                DiseaseMappingCandidate.status == "proposed",
            )
            .values(status="stale", updated_at=datetime.utcnow())
        )
        await db.flush()
        return assertion

    async def list_categories(
        self,
        db: AsyncSession,
        *,
        country_code: Optional[str] = None,
        status: Optional[str] = None,
        ai_status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        await self.ensure_schema(db)
        query = select(SourceDiseaseCategory)
        if country_code:
            query = query.where(SourceDiseaseCategory.country_code == country_code.upper())
        if status:
            query = query.where(SourceDiseaseCategory.status == status)
        if ai_status:
            query = query.where(SourceDiseaseCategory.ai_status == ai_status)
        rows = (
            await db.execute(
                query.order_by(SourceDiseaseCategory.country_code, SourceDiseaseCategory.source_id, SourceDiseaseCategory.canonical_source_label)
                .offset(offset).limit(limit)
            )
        ).scalars().all()
        output = []
        for row in rows:
            candidates = (
                await db.execute(
                    select(DiseaseMappingCandidate)
                    .where(DiseaseMappingCandidate.category_id == row.id, DiseaseMappingCandidate.status == "proposed")
                    .order_by(DiseaseMappingCandidate.rank).limit(5)
                )
            ).scalars().all()
            assertions = (
                await db.execute(
                    select(DiseaseMappingAssertion)
                    .where(DiseaseMappingAssertion.category_id == row.id)
                    .order_by(DiseaseMappingAssertion.created_at.desc()).limit(5)
                )
            ).scalars().all()
            item = _model_dict(row)
            item["candidates"] = [_model_dict(candidate) for candidate in candidates]
            item["assertions"] = [_model_dict(assertion) for assertion in assertions]
            output.append(item)
        return output

    async def accept_candidate(
        self,
        db: AsyncSession,
        *,
        candidate_id: int,
        reviewer: str,
        notes: str = "",
    ) -> DiseaseMappingAssertion:
        await self.ensure_schema(db)
        candidate = await db.get(DiseaseMappingCandidate, candidate_id)
        if candidate is None or candidate.status != "proposed":
            raise ValueError("Mapping candidate is missing or no longer reviewable")
        if candidate.candidate_kind == "new_concept":
            raise ValueError("New-concept candidates require concept creation before acceptance")
        target_kind = "group" if candidate.candidate_kind in {"group", "unmapped"} else "concept"
        target_code = candidate.target_code or UNMAPPED_GROUP
        category = await db.get(SourceDiseaseCategory, candidate.category_id)
        assert category is not None
        assertion = DiseaseMappingAssertion(
            assertion_key=_assertion_key(category.category_key, target_kind, target_code, candidate.mapping_relation),
            category_id=category.id,
            target_kind=target_kind,
            target_code=target_code,
            mapping_relation=candidate.mapping_relation,
            comparability=candidate.comparability,
            projection_policy=(
                "canonical"
                if candidate.candidate_kind == "existing_concept"
                and candidate.mapping_relation == "exact"
                and candidate.comparability in {"direct", "conditional"}
                else ("discovery_only" if candidate.candidate_kind == "existing_concept" else "no_projection")
            ),
            aggregation_policy=("direct_only" if candidate.mapping_relation == "exact" else "non_additive"),
            assertion_status="approved",
            confidence_score=candidate.confidence_score,
            suggestion_method=candidate.method,
            model_key=candidate.model_key,
            model_version=candidate.prompt_version,
            reasoning=candidate.reasoning,
            evidence=candidate.evidence or [],
            reviewed_by=reviewer,
            reviewed_at=datetime.now(timezone.utc),
            review_notes=notes or None,
            metadata_={"accepted_candidate_id": candidate.id},
        )
        db.add(assertion)
        candidate.status = "accepted"
        await db.execute(
            update(DiseaseMappingCandidate)
            .where(
                DiseaseMappingCandidate.category_id == category.id,
                DiseaseMappingCandidate.id != candidate.id,
                DiseaseMappingCandidate.status == "proposed",
            )
            .values(status="stale", updated_at=datetime.utcnow())
        )
        category.status = "active"
        await db.flush()
        return assertion

    async def reject_candidate(
        self, db: AsyncSession, *, candidate_id: int, reviewer: str, notes: str = ""
    ) -> DiseaseMappingCandidate:
        await self.ensure_schema(db)
        candidate = await db.get(DiseaseMappingCandidate, candidate_id)
        if candidate is None or candidate.status != "proposed":
            raise ValueError("Mapping candidate is missing or no longer reviewable")
        candidate.status = "rejected"
        candidate.metadata_ = {
            **(candidate.metadata_ or {}),
            "rejected_by": reviewer,
            "rejection_notes": notes or None,
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.flush()
        return candidate

    async def create_release(
        self, db: AsyncSession, *, release_code: str, created_by: str, description: str = ""
    ) -> DiseaseMappingRelease:
        await self.ensure_schema(db)
        assertions = (
            await db.execute(
                select(DiseaseMappingAssertion)
                .where(DiseaseMappingAssertion.assertion_status == "approved")
                .order_by(DiseaseMappingAssertion.assertion_key)
            )
        ).scalars().all()
        payload = [
            {
                "key": item.assertion_key,
                "category_id": item.category_id,
                "target": [item.target_kind, item.target_code],
                "relation": item.mapping_relation,
                "valid": [item.valid_from.isoformat() if item.valid_from else None, item.valid_to.isoformat() if item.valid_to else None],
            }
            for item in assertions
        ]
        checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        release = DiseaseMappingRelease(
            release_code=release_code,
            status="draft",
            checksum=checksum,
            description=description or None,
            created_by=created_by,
            metadata_={"assertion_count": len(assertions), "prompt_version": PROMPT_VERSION},
        )
        db.add(release)
        await db.flush()
        for assertion in assertions:
            db.add(DiseaseMappingReleaseItem(release_id=release.id, assertion_id=assertion.id))
        await db.flush()
        return release

    async def activate_release(self, db: AsyncSession, release_id: int) -> DiseaseMappingRelease:
        await self.ensure_schema(db)
        release = await db.get(DiseaseMappingRelease, release_id)
        if release is None or release.status != "draft":
            raise ValueError("Only an existing draft mapping release can be activated")
        conflicts = (
            await db.execute(
                text(
                    """
                    SELECT a.category_id, COUNT(*)
                    FROM disease_mapping_release_items_v3 i
                    JOIN disease_mapping_assertions_v3 a ON a.id=i.assertion_id
                    WHERE i.release_id=:release_id
                      AND (a.valid_from IS NULL AND a.valid_to IS NULL)
                    GROUP BY a.category_id HAVING COUNT(*) > 1
                    """
                ),
                {"release_id": release_id},
            )
        ).all()
        if conflicts:
            raise ValueError(f"Mapping release has categories with multiple open-ended assertions: {conflicts[:10]}")
        current = (
            await db.execute(
                select(DiseaseMappingRelease).where(DiseaseMappingRelease.status == "active")
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for item in current:
            item.status = "superseded"
        if current:
            release.supersedes_release_id = current[0].id
        release.status = "active"
        release.activated_at = now
        await db.flush()
        return release

    async def stats(self, db: AsyncSession) -> dict[str, Any]:
        await self.ensure_schema(db)
        category_rows = (
            await db.execute(
                select(
                    SourceDiseaseCategory.country_code,
                    func.count(SourceDiseaseCategory.id),
                    func.count(SourceDiseaseCategory.id).filter(SourceDiseaseCategory.ai_status == "pending"),
                    func.count(SourceDiseaseCategory.id).filter(SourceDiseaseCategory.status == "active"),
                ).group_by(SourceDiseaseCategory.country_code)
            )
        ).all()
        assertion_counts = dict(
            (
                await db.execute(
                    select(DiseaseMappingAssertion.assertion_status, func.count(DiseaseMappingAssertion.id))
                    .group_by(DiseaseMappingAssertion.assertion_status)
                )
            ).all()
        )
        candidate_counts = dict(
            (
                await db.execute(
                    select(DiseaseMappingCandidate.status, func.count(DiseaseMappingCandidate.id))
                    .group_by(DiseaseMappingCandidate.status)
                )
            ).all()
        )
        active_release = (
            await db.execute(
                select(DiseaseMappingRelease).where(DiseaseMappingRelease.status == "active")
            )
        ).scalar_one_or_none()
        return {
            "countries": [
                {"country_code": code, "categories": total, "ai_pending": pending, "active": active}
                for code, total, pending, active in category_rows
            ],
            "category_total": sum(row[1] for row in category_rows),
            "ai_pending_total": sum(row[2] for row in category_rows),
            "assertions": assertion_counts,
            "candidates": candidate_counts,
            "active_release": _model_dict(active_release) if active_release else None,
        }

    async def effective_coverage(self, db: AsyncSession) -> dict[str, Any]:
        """Report release-pinned adoption without mutating historical facts."""

        await self.ensure_schema(db)
        rows = (
            await db.execute(
                text(
                    """
                    WITH totals AS (
                        SELECT s.country_code, COUNT(*) AS observation_count
                        FROM disease_series_observations o
                        JOIN disease_surveillance_series s ON s.series_code=o.series_code
                        GROUP BY s.country_code
                    ), mapped AS (
                        SELECT country_code,
                               COUNT(DISTINCT observation_id) AS mapped_count,
                               COUNT(DISTINCT observation_id) FILTER (
                                   WHERE is_canonical_projection
                               ) AS canonical_count
                        FROM effective_disease_series_observations_v3
                        GROUP BY country_code
                    )
                    SELECT t.country_code, t.observation_count,
                           COALESCE(m.mapped_count, 0) AS mapped_count,
                           COALESCE(m.canonical_count, 0) AS canonical_count
                    FROM totals t LEFT JOIN mapped m USING (country_code)
                    ORDER BY t.country_code
                    """
                )
            )
        ).mappings().all()
        countries = []
        for row in rows:
            total = int(row["observation_count"] or 0)
            canonical = int(row["canonical_count"] or 0)
            countries.append(
                {
                    **dict(row),
                    "canonical_coverage": round(canonical / total, 6) if total else 0.0,
                }
            )
        total = sum(int(item["observation_count"]) for item in countries)
        canonical = sum(int(item["canonical_count"]) for item in countries)
        return {
            "observation_total": total,
            "canonical_total": canonical,
            "canonical_coverage": round(canonical / total, 6) if total else 0.0,
            "countries": countries,
        }


disease_mapping_registry_service = DiseaseMappingRegistryService()


__all__ = [
    "DiseaseMappingRegistryService",
    "disease_mapping_registry_service",
    "normalize_source_text",
    "PROMPT_VERSION",
]
