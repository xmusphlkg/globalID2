# Historical maintenance tools

These tools repair specific older database states. They are not scheduled
runtime services and must not be run just because a file exists here. Check
the target database, applicability and retained backup/audit evidence first.
Run commands from the repository root with `PYTHONPATH=.` when needed.

| Tool | Purpose | Execution mode |
| --- | --- | --- |
| [backfill_task_country_ids.py](backfill_task_country_ids.py) | Fill missing task geography from stored input | Preview by default; `--apply` persists |
| [migrate_chfl_geography.py](migrate_chfl_geography.py) | Correct combined CH+LI geography while preserving observations and revisions | Audit by default; `--apply` repairs; `--restore RUN_ID` reverses an audited run |
| [migrate_knowledge_automation.py](migrate_knowledge_automation.py) | Reclassify old knowledge-review states and optionally rebuild a bounded queue | Preview by default; `--apply` persists; follow its queue-rebuild conditions |
| [migrate_country_scopes.py](migrate_country_scopes.py) | Adopt canonical countries and country scopes | Writes on execution; no preview or `--help` parser |
| [migrate_add_revision_count.py](migrate_add_revision_count.py) | Add the revision counter to an older report-section-run table | Writes on execution; no preview or `--help` parser; existing column is skipped |
| [rebuild_database_schema.sh](rebuild_database_schema.sh) | Drop and recreate schema from `schema.sql` | Destructive recovery; asks for confirmation unless `--yes` is supplied |

Safe parameter inspection for the audit-first CHFL tool:

```bash
PYTHONPATH=. venv/bin/python scripts/maintenance/migrate_chfl_geography.py --help
```

Do not use `--help` on tools that have no argument parser: an ignored argument
would still execute their migration. The two direct migration tools retain
their existing behavior and remain available for old-database recovery.

Current schema evolution belongs in [migrations](../../migrations/README.md).
Legacy dataset rebuilding is documented in the
[rebuild guide](../../docs/operations/DATABASE_REBUILD.md). Operational recovery
commands still used by the control plane remain at their existing root paths;
see the [script index](../README.md).
