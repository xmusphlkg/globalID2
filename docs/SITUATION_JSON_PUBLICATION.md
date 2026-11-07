# Situation JSON publication

Situation facts, Surveillance observations, and static HTML have separate publication contracts. With `DATA_RELEASE__SITUATION_JSON_ENABLED=true`, Situation changes publish under `site-data/situation/` without Astro, CSV generation, or observation projection. The default is false; existing static publication remains available until migration.

Collection writes its public metadata/source-health changes and a `PublishDirty(line='situation', scope_key='*')` row in the same transaction. Each accepted extraction also records a marker in its result transaction when public facts or review metadata changed. Failed or rejected extractions retain earlier accepted alerts. Identical collection input, identical accepted facts, and unchanged outbreak reconstruction produce no extra markers. Multiple result transactions in one long AI task retain separate IDs, so publishing an earlier result cannot consume a later result.

Successful `refresh_situation_sources` completion invokes reconciliation immediately. The release scheduler also checks committed markers every poll, including when static releases, daily timers, and `auto_after_crawls` are disabled. Therefore a missed completion hook, partial refresh failure, or process restart does not lose committed changes. Automatic retry deadlines and failure cooldowns remain durable. Cooldowns are scoped by JSON line. Both JSON lines share one worker slot, and alternate when both have publishable work; disabled/blocked Surveillance scopes do not hold back Situation.

The queue freezes `data_only=true`, `data_line='situation'`, and exact selected `publish_dirty_ids`. Missing `data_line` on an older JSON task continues to mean Surveillance. New static tasks freeze `situation_detached=true` when the gate is enabled and exclude Situation IDs. Old static tasks without this contract cannot overlap Situation publication; execution also rejects incompatible old tasks after the gate is enabled. Static content, collection, and AI work can run alongside a compatible JSON task.

Before opening its read snapshot, a managed task captures the current remote pointer into its private run's `.publication/starting-baseline.json`. Repeated capture validates the same receipt without adopting a newer pointer. Managed upload requires this receipt; an old frozen candidate without it fails closed and needs a fresh task. A changed remote baseline rejects an old candidate with HTTP 412. This also protects a task interrupted between generation and its first upload, and a task whose upload succeeded before DB acknowledgement failed.

The export reads all Situation tables in one PostgreSQL `REPEATABLE READ, READ ONLY` transaction. Managed execution imports the caller's exported snapshot. Standalone export establishes the same isolation inside its single DB session. Outbreak grouping is derived from the accepted alerts in that snapshot rather than depending on a long refresh reaching its final materialized-table rebuild. No report full text is exported.

The task holds its output file lock through generation and freezing. It freezes an immutable manifest and selected-ID receipt under `exports/situation-runs/<task_uuid>/`, while shared content-addressed objects live under `exports/situation-data/objects/`. Retries reuse the candidate. Every selected ID is checked against actual DB rows before generation, before publication, and before acknowledgement; wrong lines, non-global scopes, missing IDs, or IDs consumed by another task are rejected.

The manifest uses schema version 1 and logical files `map-data.json`, `map.json`, `monitoring.json`, `sources.json`, `outbreaks/<id>.json`, `by-country/<code>.json`, `by-disease/<id>.json`, and weekly files. Entity shards retain `gids.situation_alerts.v1`. Every registered country/jurisdiction and current disease receives a shard, including explicit empty shards, so a missing manifest entry remains an error. Upload completes immutable objects before atomically replacing `site-data/situation/current.json`; historical objects remain valid. Only after success are the exact selected Situation IDs acknowledged. Surveillance IDs and published curves are untouched.

Read-only local initialization:

```sh
venv/bin/python scripts/export_situation_data.py --release-id situation-initial-YYYYMMDD
```

Root deployment owns upload, service activation, and production monitoring. Bootstrap upload uses `scripts/publish_surveillance_data.py --source-dir exports/situation-data --prefix site-data/situation`. Managed task output includes `data_line`, `release_id`, `manifest_sha256`, selected IDs, and `release_timing` (`export_seconds`, `publish_and_ack_seconds`, `elapsed_seconds`); `pages_deployed` is false.
