# Remove replaced static paper pages

Run this only after both publication paths use the template catalogue and the
new Worker is serving the public article routes. Fence publication workers for
the cleanup window. The user has explicitly requested removal of replaced R2
files; this document adds verification, not another approval requirement.

`scripts/cleanup_legacy_papers.py` plans exact keys from a saved bucket inventory
and canonical `papers-manifest.json`. Every planned article must have a separate
local original with matching SHA-256 and byte count. The plan includes only
canonical static article keys and inactive `paper-template-v1` prototype objects.
It never deletes assets, research JSON, reports, surveillance, v2 objects, or
historical `releases/sha256` objects used by versioned links and subscriptions.

At apply time it verifies the expected research pointer, authenticates the
catalogue and all its shards, requires the reconstructed HTML SHA-256 and byte
count to match every verified original, and probes English, Chinese and French
public pages on the main, www and Papers hosts. Worker responses must have
the expected release, template mode and verified pre-rewrite HTML hash. It then
conditionally removes the obsolete article entries from the canonical manifest
before deleting exact-key batches. The pointer is checked before every batch;
an interrupted run can safely resume with the same plan and publication. A
durable checkpoint written before the manifest CAS identifies that plan and
publication; retries accept the exact already-pruned manifest without reusing
the stale original ETag, and reject an unrelated or missing checkpoint.

```bash
venv/bin/python scripts/cleanup_legacy_papers.py \
  --manifest /path/to/original-papers-manifest.json \
  --inventory /path/to/inventory.json --source /path/to/frozen-article-backup \
  --plan /path/to/deletion-plan.json

venv/bin/python scripts/cleanup_legacy_papers.py \
  --manifest /path/to/original-papers-manifest.json \
  --plan /path/to/deletion-plan.json --apply --publication-fenced \
  --publication-sha256 <verified-current-pointer-sha256> \
  --output /path/to/cleanup-receipts \
  --lock-dir /path/to/live-repository/data/cache/r2-publish
```

The apply command uses the existing R2 environment configuration. If publication
or the canonical manifest changes after planning, stop and regenerate the plan;
do not replace its recorded digest or ETag merely to force it through.

For rollback, keep the frozen article originals and saved legacy manifest until
the new release has been accepted. The page files can be uploaded to their exact
`key` values using `R2S3Client.put_object`, `text/html; charset=utf-8` and
`Cache-Control: no-cache`; verify each local SHA-256 first. All old referenced
assets are retained. Restore the saved canonical manifest only after every page
has been restored and the route/publisher rollback has been coordinated. Inactive
v1 prototypes are not required for rollback.

The 2026-11-12 reviewed inventory is stored outside the repository under
`/tmp/globalid-release-20261112/cleanup-audit/`. Its initial plan contains 24,579
static article pages and 2,146 v1 objects, totaling 238,943,659 stored bytes. This
is a deletion plan, not evidence that deletion has already happened; the apply
receipt records the actual completed count.
