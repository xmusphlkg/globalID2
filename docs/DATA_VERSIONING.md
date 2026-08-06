# Data versioning and repository boundaries

GlobalID separates source-code history from mutable surveillance data. A data
refresh must never create a commit on the code repository's development or
release branches.

## Repository and branch policy

| Repository / workspace | Persistent branch | Purpose |
| --- | --- | --- |
| `globalID2` source code | `development` | Tested integration work |
| `globalID2` source code | `master` | Stable releases and production |
| Public download-data repository | `main` | Stable time-partitioned CSV/JSON/XLSX files |
| `globalID-data-archive` | `main` | Append-only raw source archive |
| Local cache and generated workspaces | None | Ignored runtime files; never a Git repository unless a publisher creates an isolated ignored working clone |

Only `development` and `master` are persistent branches in the source-code
repository. Temporary pull-request branches, when unavoidable, must be deleted
after merge. Recovery branches are local, short-lived safety tools and must not
be left on the remote.

The fixed data branches are intentional and do not belong to the source-code
branch model. The public download repository keeps generated time partitions
on `main`; the raw archive also keeps `main` history because traceability is its
purpose. `external-data/globalID2_data_download/.git` and
`exports/raw-git-archive/.git` may exist locally after publication, but both
working trees are ignored by `globalID2` and cannot add branches to the parent
repository.

## Ownership boundaries

| Asset | System of record | Code repository |
| --- | --- | --- |
| Application code, schemas, migrations, mappings | Code repository | Tracked |
| Small deterministic test fixtures | Code repository | Tracked |
| Raw source responses (`data/raw`) | Dedicated raw archive / approved versioned object storage | Ignored |
| Current normalized exports (`data/current`) | PostgreSQL / local cache | Ignored |
| Generated Astro JSON (`astro-site/src/data/**/*.json`) | Release workspace | Ignored |
| Hand-authored Astro data modules (`*.ts`) | Code repository | Tracked |
| Public CSV/JSON/XLSX partitions | Dedicated data repository, `main` | Not tracked |
| Public download working clone (`external-data/globalID2_data_download`) | Dedicated data repository, `main` | Ignored |
| Raw archive working clone (`exports/raw-git-archive`) | Dedicated archive repository, `main` | Ignored |
| Astro build output | Deployment artifact / Cloudflare Pages | Ignored |

The generated working files remain available locally. Removing them from the
Git index does not delete their contents. Hand-authored TypeScript modules in
`astro-site/src/data` remain tracked. A fresh checkout creates generated files
when the crawler or `scripts/generate_site_data.py` runs.

## Release flow

1. Crawlers store source payloads outside Git and upsert normalized facts into
   PostgreSQL.
2. Data Release generates site JSON and partitioned CSV/JSON/XLSX files under
   `exports/site-downloads`.
3. Paths, hashes, record counts, and GitHub file-size limits are validated. The
   persistent local data checkout copies only changed partitions, then commits
   and pushes them to `main`.
4. Raw source payloads are independently synchronized to the dedicated raw
   archive repository's `main` branch when raw archiving is enabled.
5. Astro builds from the generated JSON and Cloudflare receives only the build
   artifact.
6. The code repository HEAD is unchanged throughout the release.

Every public export contains a manifest with explicit partition and format paths. The
download repository is a distribution channel, not the permanent raw archive;
it has no `releases/` directory or GitHub Release dependency.
Long-term source retention belongs in the dedicated raw archive or other
approved versioned object storage.

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
