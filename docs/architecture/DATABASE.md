# Database architecture

The SQLAlchemy models in [src/domain](../../src/domain/) define application
entities. [schema.sql](../../schema.sql) is a generated snapshot;
[migrations](../../migrations/README.md) records the managed upgrade chain.
The live database's migration version must be checked separately from these files.

## Data ownership

| Area | Principal tables | Contract |
| --- | --- | --- |
| Geography and concepts | `countries`, `country_scopes`, `standard_diseases`, `diseases`, taxonomy and concept tables | Stable identities with explicit geographic scope |
| Surveillance facts | `disease_surveillance_series`, `disease_series_observations`, `disease_series_observation_revisions`, `disease_source_availability` | Preserve source identity, time grain, geography, dimensions and revisions |
| Mapping registry | `source_disease_categories`, `disease_mapping_assertions_v3`, `disease_mapping_releases_v3` | Reviewed, versioned source-to-concept assertions |
| Legacy compatibility | `disease_records`, `disease_mappings` | Compatibility projections and bounded migration tools, not the semantic authority for new sources |
| Collection and publication | `tasks`, `task_workbook`, `run_stages`, `source_artifacts`, `publish_dirty` | Durable work, reproducible source artifacts and exact publication acknowledgements |
| Scheduling and models | `automation_jobs`, `data_release_jobs`, `scheduled_job_states`, `ai_work_items` | Separate scheduling, business-task ownership and model leases |
| Situation | `situation_notices`, `situation_documents`, `situation_extractions`, `situation_alerts`, `situation_outbreaks` | Internal documents and reviewed public alert facts are distinct |
| Research | `literature_articles`, link tables, `literature_summaries`, `literature_status_events`, `literature_ingest_runs` | Source metadata, review history and public projections remain separate |
| Reports and knowledge | Report, section, section-run and knowledge models | Evidence, generation, review and publication retain their own states |

See [Disease ontology v3](DISEASE_ONTOLOGY_V3.md) for natural keys and aggregation
rules, and [data-line/control-plane architecture](DATA_LINE_AND_CONTROL_PLANE_REFACTOR.md)
for task and stage contracts. Missing, suppressed and explicit zero observations
must remain distinguishable. A `CHFL` source aggregate must not become a Swiss
national total merely because the source is registered under `CH`.

## Transactions and publication

Storage commits facts and their dirty markers in the same transaction. A release
selects explicit dirty IDs and exports a consistent read snapshot. The snapshot
ends before build/upload; retries reuse verified frozen artifacts. A successful
release acknowledges only its selected changes, leaving later collection writes
pending. See [release recovery and snapshots](../publishing/DATA_RELEASE_RESILIENCE.md).

Public pages consume published JSON or generated files. They do not use live
operational SQL as their public data source. Public series are versioned
projections, distinct from raw source observations.

## Initialization and schema changes

For a new development database, `venv/bin/python main.py init-database` creates
missing model tables and initializes CN metadata. It does not upgrade existing
columns or replace the Alembic migration procedure. The removed ad hoc task-table
creation scripts are superseded by this entrypoint and managed migrations.

For existing installations, follow [migration preflight](../../migrations/README.md)
and [deployment/rollback](../control-center/deployment-and-rollback.md). Stamp a
baseline only when adopting Alembic for a verified existing schema. Never remove
older Alembic revisions merely because the current database is at head.

Historical repair commands live in [scripts/maintenance](../../scripts/maintenance/README.md).
Their presence is not evidence that a repair remains necessary or has run on a
particular database. Inspect each tool's applicability and execution mode first.

## Schema and legacy checks

```bash
PYTHONPATH=. venv/bin/python scripts/control_plane_migrate.py preflight
venv/bin/alembic current
venv/bin/alembic heads
venv/bin/python scripts/check_legacy_disease_access.py
```

The legacy-access baseline allows existing compatibility readers and migration
tools while rejecting new or increased legacy dependencies. Moving an existing
tool must preserve its reviewed counts; deleting a retired tool removes its
baseline entry. Database backups, raw archives and generated exports remain
outside the source repository; see [data versioning](../operations/DATA_VERSIONING.md).
