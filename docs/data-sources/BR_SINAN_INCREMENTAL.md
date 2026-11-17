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
