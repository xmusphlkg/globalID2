# Data versioning and repository boundaries

GlobalID separates source-code history from mutable surveillance data. A data
refresh must never create a commit on the code repository's development or
release branches.

## Ownership boundaries

| Asset | System of record | Code repository |
| --- | --- | --- |
| Application code, schemas, migrations, mappings | Code repository | Tracked |
| Small deterministic test fixtures | Code repository | Tracked |
| Raw source responses (`data/raw`) | Versioned object storage | Ignored |
| Current normalized exports (`data/current`) | PostgreSQL / local cache | Ignored |
| Generated Astro JSON (`astro-site/src/data/**/*.json`) | Release workspace | Ignored |
| Hand-authored Astro data modules (`*.ts`) | Code repository | Tracked |
| Public canonical snapshots | Dedicated data repository, `snapshot-v2` | Not tracked |
| Astro build output | Deployment artifact / Cloudflare Pages | Ignored |

The generated working files remain available locally. Removing them from the
Git index does not delete their contents. Hand-authored TypeScript modules in
`astro-site/src/data` remain tracked. A fresh checkout creates generated files
when the crawler or `scripts/generate_site_data.py` runs.

## Release flow

1. Crawlers store source payloads outside Git and upsert normalized facts into
   PostgreSQL.
2. Data Release generates the site JSON and canonical sharded package in its
   working directory.
3. The package is validated and published to the dedicated data repository's
   bounded `snapshot-v2` orphan branch.
4. Astro builds from the generated JSON and Cloudflare receives only the build
   artifact.
5. The code repository HEAD is unchanged throughout the release.

Every canonical release contains a manifest and content hashes. The
`snapshot-v2` tree is a bounded distribution channel, not the permanent raw
archive; long-term source retention belongs in versioned object storage.

## Enforcement

Run the boundary check directly or as part of the normal project checks:

```bash
scripts/check_repository_boundaries.sh
make check-repository-boundaries
```

The check fails if a generated path becomes tracked or loses its ignore rule.
The Data Release preflight performs the same tracked-file check and refuses to
publish until the boundary is restored.

## Git history migration

This policy prevents new data commits but does not rewrite old commits. Clean
the existing multi-gigabyte history only in a coordinated maintenance window:

1. stop writers and create an offline mirror/bundle backup;
2. audit the generated paths for any curated files that must be preserved;
3. use `git filter-repo` to remove approved generated paths from all refs;
4. force-push the rewritten refs and require every clone to be recreated;
5. run the boundary and release tests before re-enabling automation.

Do not mix this history rewrite with normal feature development.
