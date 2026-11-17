# Brazil SINAN incremental refresh

The national/state ingestion connector and `scripts/update_br_subdivisions.py`
share persistent DBC downloads in
`data/cache/br/sinan_monthly_aggregates/downloads/`. Each official URL and
file signature has its own directory. The signature includes filename,
final/preliminary status, byte size and modification time. File locks prevent
concurrent processes from downloading the same unchanged file repeatedly.

On each scheduled run:

1. Read the final and preliminary FTP directory listings, subject to the index
   TTL (24 hours by default).
2. Reuse a downloaded file when its source URL, signature, local size and local
   modification time match the download receipt. Missing metadata, unknown
   source size/time, local damage or an expired receipt triggers a download.
3. Download each file again after 30 days, on its next use. This checks for
   upstream replacements that preserve both size and modification time.
4. Preserve per-run source artifacts and their integrity checks. National and
   state tasks can reuse the download while retaining independent audit trails.
5. Reuse derived aggregates keyed by source SHA-256, source metadata and parser
   version. National caches cover all parsed months. State connector caches
   cover the requested months and all 27 states, so another state requesting
   the same months can reuse the result and its audit totals. The standalone
   state script caches its full-file result, including excluded files.

Only changed files need decompression and aggregation. Cache hits still pass
through the existing merge and import logic, allowing missing database rows to
be restored. A failed directory refresh does not renew the old index TTL.
Existing history recovery remains available; a forced directory refresh fails
if either official listing cannot be fetched.

The `BR.crawler_config` settings in `configs/country_bootstrap.json` are:

| Setting | Default | Meaning |
| --- | --- | --- |
| `file_index_cache_ttl_seconds` | `86400` | Directory listing TTL. |
| `download_revalidate_seconds` | `2592000` | Maximum download receipt age; `0` redownloads on every use. |

Normal state refresh, without database import:

```sh
venv/bin/python scripts/update_br_subdivisions.py --recent-years 2
```

`--recent-years 2` selects only the latest two annual source years as well as
limiting output dates. `--start-year` controls historical runs without that flag.

Force fresh listings, downloads and parsing:

```sh
venv/bin/python scripts/update_br_subdivisions.py --recent-years 2 --force
```

`--apply` imports results; `--export` requires `--apply` and queues publication.
Dashboard force mode also refreshes listings, downloads and parsing. Normal
offline replay can reuse verified content caches; it never contacts DATASUS.

The first run builds the new caches. Existing artifacts and older aggregate
caches remain intact. When changing parsing or aggregation semantics, bump
`NATIONAL_PARSER_VERSION`, `STATE_PARSER_VERSION`, or the standalone script's
`CACHE_VERSION`, as applicable.

## Reviewed residence corrections

A normal refresh still rejects an unexplained positive-to-zero replacement.
`SourceAuthoritative` and `closed_revisable` do not disable that check. The
standalone refresh now uses the connector's shared residence parser; a month
containing only unknown residence cannot become 27 invented state zeroes.
The parser cache version is advanced so old derived results are not reused.

Use `--output-dir /path/to/candidate` to keep a dry-run CSV and source audit
outside the shared processed directory. Import quality failures retain
`applied=false` and their full quality report in `import_report.json`; they
still roll back the complete import and never queue a release.

An inspected upstream correction can be supplied with
`--apply --reviewed-revisions /path/to/reviewed-revisions.json`. This accepts
only individual cells with all of the following verified in the BR write
transaction: retained old/new `source_artifacts` for the same official SINAN
file, their exact bytes and SHA-256 hashes, chronological artifact capture,
old/new values recomputed with the shared residence parser, matching candidate
source contributions, and an unchanged stored source-series value. Each entry
names `country_code`, `disease_code`, `date`, `series_code`, `stored_value`,
`incoming_value`, and `previous_artifact` / `current_artifact` objects containing
`relative_path` and `sha256`. The top-level object has `schema_version: 1` and
an `entries` array. A changed artifact, database value, missing observation or
mixed annual-source contribution fails closed. Exact proof and receipt digest
are retained in each corrected observation's raw provenance; other rows keep
the normal quality policy.

A recovery import should omit `--export`. Its committed dirty scopes can then
follow the normal supervised publication workflow. A reviewed receipt is a
one-time reconciliation, not a reusable permission to reduce later values.
