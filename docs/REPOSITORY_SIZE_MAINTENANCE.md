# Repository size maintenance

The application repository contains legacy database backups and historical raw
data. They are deployment data, not source code. New copies must go to the
project's versioned data archive or approved object storage, with checksums and
retention rules, rather than this repository.

## Guardrail

Run the same check used by CI:

```bash
./scripts/check_repository_boundaries.sh
```

`scripts/check_repository_size.py` examines Git's index, not merely files in the
working tree. It rejects:

- anything under `backups/` or `data/backups/`;
- `.dump`, `.db`, `.sqlite`, `.sqlite3`, and backup-named SQL files;
- any individual blob larger than 5 MiB.

CI additionally scans every object introduced by the PR/push revision range, so
adding and then deleting a large file in later commits does not bypass the rule.

`configs/repository_size_baseline.json` is an exact inventory of existing debt.
Each exception is pinned to its path, blob object ID, and byte size, so replacing
an old file at the same path still fails. Do not add entries to make CI green.
The baseline should only shrink after an authorized cleanup.

## Move data before rewriting history

For every retained backup or raw-data collection:

1. Copy it to approved versioned object storage or the dedicated raw-data
   archive documented in `docs/DATA_VERSIONING.md`.
2. Record owner, retention period, source path, byte size, and SHA-256 checksum.
3. Restore a sample into an isolated environment and verify it is usable.
4. Freeze repository writes and announce that all clones will need replacement.

Example inventory commands are read-only:

```bash
git count-objects -vH
git verify-pack -v .git/objects/pack/*.idx | sort -k3nr | head -50
git rev-list --objects --all > /tmp/globalid-all-objects.txt
git filter-repo --analyze
```

`git filter-repo --analyze` writes reports under `.git/filter-repo/analysis/` but
does not rewrite commits. Review its path and blob reports before selecting the
filters.

## Rehearse in a disposable mirror

History rewriting requires explicit repository-owner approval. Do not run these
commands in the active working copy. Replace the URL and dated paths first.

```bash
git clone --mirror <canonical-repository-url> globalid-rewrite-YYYYMMDD.git
cd globalid-rewrite-YYYYMMDD.git
git bundle create ../globalid-before-rewrite-YYYYMMDD.bundle --all
git fsck --full
git filter-repo --analyze
cp -a . ../globalid-rehearsal-YYYYMMDD.git
cd ../globalid-rehearsal-YYYYMMDD.git
git filter-repo --force --invert-paths \
  --path backups \
  --path data/backups \
  --path data/raw \
  --path-glob '*.dump' \
  --path-glob '*.db' \
  --path-glob '*.sqlite' \
  --path-glob '*.sqlite3' \
  --path-glob '*backup*.sql' \
  --path-glob '*Backup*.sql'
git fsck --full
git count-objects -vH
```

This disposable copy is the dry run: inspect branches, tags, releases and a
fresh non-mirror clone from it. Compare expected source trees and run the full
test suite. Raw historical objects may need additional exact `--path` filters
identified by the analysis report; review each path rather than using a broad
`data/**` deletion.

When rehearsing from a local clone, inspect all refs before measuring the
result. Local-only `refs/codex/*`, `refs/original/*`, reflogs and stash refs can
keep removed blobs reachable even when every branch and tag was rewritten.
They are not expected in a canonical GitHub mirror and must never be pushed.
After filtering a disposable local rehearsal, delete only those local-tool refs,
expire its reflogs, run `git gc --prune=now`, and verify targeted paths against
`git rev-list --objects --branches --tags`.

### Verified rehearsal (2026-08-05)

The documented filters were rehearsed against a local mirror containing 278
commits. After removing local-only Codex/original refs and garbage-collecting
the disposable copy:

- the packed repository fell from 2.86 GiB to 35.22 MiB;
- `git fsck --full --no-dangling` passed;
- a fresh non-mirror clone succeeded;
- no `data/raw/`, `backups/`, or `data/backups/` path remained reachable from
  any branch or tag;
- the dedicated `globalID-data-archive` local and remote `main` refs both
  resolved to `82b55701aff0daf167d366abb65176710f2d8f7c`;
- archive verification matched 2,638 files and 3,010,324,906 bytes, including
  one split file reconstructed from its SHA-256-verified parts.

The 18 legacy database/mapping backups are inventoried with SHA-256 values in
`configs/legacy_backup_inventory.json`. They still require approved external
retention before the coordinated remote rewrite; a local bundle alone is not
an external backup.

## Coordinated cutover

Only after backup restoration and rehearsal succeed:

1. Schedule a maintenance window and block merges and pushes.
2. Record all protected branches, tags, open PRs, release refs and deployment
   commit IDs; retain the bundle in protected storage.
3. Repeat the rewrite from a fresh mirror of the canonical remote.
4. Have a repository administrator temporarily permit the coordinated force
   update, then push rewritten branches and tags with explicit refspecs. Avoid a
   blind `--mirror` push until non-branch refs have been reviewed.
5. Restore branch protection immediately and run CI plus deployment smoke tests.
6. Require collaborators to archive/delete old clones and clone afresh. A pull
   or merge from an old clone can reintroduce removed objects.
7. Expire server-side caches according to the hosting provider's process, then
   verify the remote size after its garbage-collection window.
8. Remove only baseline entries whose objects no longer exist, and rerun the
   repository-boundary check.

Rollback means restoring the protected bundle/remote mirror during the
maintenance window. Keep it until the agreed retention period expires.
