# Papers generation retention

Papers v3 publishing uploads immutable catalogues, shards, article records,
templates and supporting assets, then conditionally advances the research
publication pointer. Publishing does not automatically delete old objects.
Use `scripts/cleanup_papers_generations.py` for a separate, reviewed maintenance
operation after the current release has completed and public verification passed.

The collector always preserves the current generation. It also preserves every
catalogue created or modified within the last 48 hours and the complete dependency
closure of those catalogues, including shared article records, templates and
assets referenced inside template asset manifests. A longer grace period can be
selected; a shorter one is rejected. Unknown object ages are retained.

Only exact, hashed Papers object keys and expired catalogue roots can be removed.
`site-data/**`, source observations, reports and user content are outside the
collector. An old object still referenced by any retained catalogue is preserved.
A browser tab using supporting assets from a generation older than the grace
period may need a reload after that generation is retired; normal public page
links continue to resolve the current publication.

## Prepare and review

```bash
venv/bin/python scripts/cleanup_papers_generations.py \
  --plan exports/papers-gc/plan.json \
  --cache-dir exports/papers-gc/read-cache
```

The plan records exact keys, stored sizes, ETags and modification times, the
research pointer digest, every catalogue root, and the complete retained closure.
Review the candidate count and bytes, retired roots, and current publication
identity before applying. Missing current dependencies, missing dependencies in
the collected namespaces, corrupt metadata or changing roots block planning.
Historical v2 references outside the collected namespaces are reported separately;
they do not authorize deletion in those namespaces.

## Fence publishers and apply

The operator must prevent all publishers from writing during the entire operation.
The current v3 publisher does not share the legacy R2 file lock, so that lock and a
stable pointer alone are insufficient. A controlled maintenance window must stop
the scheduler, workers, direct publisher processes and any other publication
writers. Inspect queued and recoverable prepared releases as well as running
releases: a paused prepared candidate can still own dependencies. Either protect
its exact closure or refuse cleanup until it is resolved. The collector's supplied
fence checker should fail closed for unresolved prepared candidates.

`--fence-check` is an executable file accepting no arguments. It must return zero
only while the operator's publication fence is still held. It is invoked before
validation and every deletion batch, with a 30-second timeout. A local service and
process check establishes only a local fence; external writers must be separately
excluded. Do not claim a cross-host guarantee from local checks.

```bash
venv/bin/python scripts/cleanup_papers_generations.py \
  --apply \
  --plan exports/papers-gc/plan.json \
  --publication-sha256 REVIEWED_RESEARCH_POINTER_SHA256 \
  --output exports/papers-gc/receipts \
  --cache-dir exports/papers-gc/read-cache \
  --fence-check /absolute/path/to/assert-publication-fenced
```

Apply refreshes all object metadata and recomputes the retained closure under the
fence. The result must exactly match the reviewed plan. It retires old catalogue
roots before deleting their unshared dependencies and rechecks the pointer,
catalogue root set and external fence before each exact-key batch. It never changes
or rolls back a publication pointer.

Prepared and completed batches are fsynced to the receipt journal. An interrupted
run can resume using the identical plan and receipt directory; unexplained missing
roots or changed object metadata stop it. Completion requires another inventory
confirming candidate absence and preservation of every current dependency.
`receipt.json` has `completed: true` and `verified: true` only after this final check.
If cleanup fails, preserve its journal and restore normal services after the
maintenance controller has recorded the failure. Investigate and resume the same
reviewed operation; do not silently widen its deletion set.
