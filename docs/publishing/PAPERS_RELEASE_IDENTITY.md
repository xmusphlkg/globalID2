# Papers article identity and subscription publication boundary

Article detail pages carry the stable article ID and SHA-256 of the exact
article JSON bytes rendered by the en/zh/fr variants. The marker enables a
future verifier to compare versions; it does not prove that a page is
currently public, that an old brief used the same summary, or that its content
has not subsequently been retracted or corrected.

The Papers manifest already contains per-page SHA-256 values, but Pages
release.json does not bind that manifest. Today the final manifest cannot be
predicted from the local split artifact alone: when article social cards are
disabled, the R2 publisher folds retained remote cards into it. Papers objects
are overwritten one at a time, the completion manifest is uploaded last, and
Pages deployment follows. A release can therefore temporarily expose a mixture
of generations; a failed or concurrent publish can leave Pages, R2 objects,
and the manifest at different versions. Some release configurations explicitly
skip public HTTP verification. Do not infer publication identity from a
successful upload command.

A future subscription verifier needs all of the following before relaxing its
current fail-closed research gate:

1. Make the exact canonical Papers manifest a prepared, immutable artifact
   before Pages identity is written, including any retained objects. Publish
   article pages and matching JSON under a release-specific immutable prefix.
2. Embed the manifest digest and Papers origin in the Pages release artifact,
   then verify the public Pages release and the exact manifest bytes before
   accepting a release. Coordinate old-release rollback and retain its
   immutable Papers generation.
3. Bind each weekly finding to the current published article ID and exact
   bilingual summary version. Fetch each page named by the verified manifest,
   verify its bytes against the manifest, check both language markers, and
   require current published and non-retracted integrity state. Treat missing,
   withdrawn, corrected, stale, or inconsistent records as review-required.
4. Perform the same checks immediately before queueing and upon recovery; never
   automatically resend an already accepted or unknown delivery. An unrelated
   new release can only reuse a prior staged payload after source-by-source
   equivalence and current integrity have been proven.
5. Add adversarial integration tests for partial uploads, publication during
   verification, unchanged data across unrelated releases, mismatched language
   markers, stale or forged manifest hashes, corrections, withdrawals,
   retractions, interrupted queueing and rollback. Test identity using real
   public artifact fixtures, not a callback that simply returns success.

The markers do not change the existing publishing flow, migrate R2 keys, deploy
the site, or enable subscription sending.

## Read-only verifier and remaining publication work

`research_publication_verifier.py` is a standalone, fail-closed prototype. It
requires a frozen candidate identity containing `papers_origin`,
`papers_generation_sha256`, `papers_manifest_url`, and
`papers_manifest_sha256`, verifies those fields against the public Pages
release, and hashes the public Papers manifest. For every finding it verifies
both detail-page bytes and article-version markers, then checks the corresponding
article JSON, current publication and integrity state, and both finding texts.
It also reconstructs the generation from every manifest entry using the same
canonical format as the publisher and rejects extra fields, reordered entries,
out-of-generation keys, or a mismatched generation hash.
It re-reads the release, manifest, and pages and provides a full recheck for a
future sender. The empty-finding case verifies the binding only; a separate
verified inventory must establish that an empty set is complete. This helper
is not connected to any live subscription producer or sender.

`papers_immutable.py` prepares an experimental content-addressed generation at
`releases/sha256/{generation}/`, where the immutable manifest is written last.
Objects are conditionally created, checked through R2 and the public origin,
and never overwritten by this publisher. Its result is a proposed binding only:
it does not change Pages release files, publish a mutable pointer, deploy,
delete older objects, or send mail. A bucket principal with unconditional PUT
or delete permission can still mutate these keys; this prototype alone is not
an immutability guarantee. Nor does an immutable historic article demonstrate
that its current public integrity status has not changed.

Reading the final manifest after the existing R2 publish and adding its hash to
`release.json` is insufficient. `_built_site_digest` excludes `release.json`;
the ready receipt and retained artifact candidate are created before Papers
publication. Deploy checkpoints, current-publication markers, retained bound
candidates, retained snapshots and public release comparisons now reject a
different, absent or incomplete Papers binding when either side has one.
Legacy releases without bindings still compare as before; they cannot authorize
research delivery. These comparisons alone do not publish or verify a Papers
generation. The per-bucket publisher
lock is released when the upload command exits and does not cover subsequent
manifest verification, Pages deployment, observation, or email delivery.
An in-place Papers update can replace an article before its manifest changes.

To close the boundary, prepare and publish the exact Papers identity before the
durable ready candidate is written, and incorporate it into the actual release
and rollback flow. Hold a cross-process publication
lease through final Papers verification and Pages cutover, with appropriate
recovery if any step fails. Use immutable generation-addressed article objects
or an equivalent transactional publication scheme: repeated HTTP checks alone
cannot prevent an overwrite between the final check and SMTP acceptance.
Only then may a future sender use a verified proof and recheck it immediately
before queueing or delivery. Existing releases without a binding remain
ineligible.

## Prepared integration components

`papers_release_preflight.py` can freeze the exact generation and Pages
binding before a ready receipt is written. It refuses to replace an existing
different binding and provides a read-only Cloudflare API check for an enabled,
indefinite bucket lock covering the entire `releases/sha256/` prefix. Cloudflare
documents atomic conditional PUTs and prefix bucket locks that prevent overwrite
and deletion; see the [R2 conditional operation reference](https://developers.cloudflare.com/r2/api/s3/extensions/)
and [bucket lock documentation](https://developers.cloudflare.com/r2/buckets/bucket-locks/).
An integration test exercises the existing signed `R2S3Client` conditional PUT,
readback and retry behavior, including the locked-object HTTP 409 case, using a
local transport. It does not test the live Cloudflare account.

A successful lock lookup is only a point-in-time observation: privileged
administrators can remove a lock, so credentials and configuration ownership
must protect it for the lifetime of every referenced generation. Nor does it
provide the cross-publisher fencing required for a Pages cutover. These helpers
are deliberately not connected to the live pipeline until those guarantees,
including rollback and older publishers, are enforceable. No configuration
flag currently enables the proposed publisher.

## Reviewable production lock rule and rollout dependencies

The current production read-only inspection did not find an enabled indefinite
rule covering the entire version prefix. The following is a **rule fragment**
for review, not a complete configuration to apply:

```json
{
  "id": "papers-generations-indefinite",
  "enabled": true,
  "prefix": "releases/sha256/",
  "condition": {"type": "Indefinite"}
}
```

The bucket lock update API replaces the rule collection. Preserve every existing
rule when preparing an update, review the exact account, bucket and jurisdiction,
and verify the read-back. This prefix does not lock the existing stable Papers
paths. It permanently retains both successful generations and orphaned objects
from interrupted attempts until an authorized administrator removes or changes
the rule, so storage and request costs continue to grow. A rollback must deploy
the prior Pages artifact with its original bound manifest and retain the whole
corresponding generation; rolling back to a legacy release without a binding
cannot authorize research delivery. Do not remove a lock or generation while any
release or queued delivery can still reference it.

`publication_fence.py` adds a **cooperative** PostgreSQL session advisory lock
and persistent monotonic epochs. The required table is not installed or added
to the automatic migration chain. Its proposed schema is:

```sql
CREATE TABLE publication_fences (
  name VARCHAR(128) PRIMARY KEY,
  epoch BIGINT NOT NULL CHECK (epoch > 0)
);
```

Create a migration only after coordinating with the existing uncommitted `0027`
migration and the actual Alembic head; do not create a second head or reset
epochs during downgrade. Database backup/restore must also preserve monotonic
fencing across all publishers; a stale restored counter cannot provide it.
All participants must use the same primary and a session-pinned connection,
not transaction pooling, and must discard the session on acquisition ambiguity
or loss. `verify_bound_candidate` recomputes both artifacts and rejects missing,
changed or legacy identity on recovery. Neither is wired into live releases.

The database epoch does not itself fence Pages: the Pages deploy endpoint does
not atomically reject a stale epoch. A deploy already in flight after a database
disconnect may finish after a newer deploy, and the existing compatibility
deployment, normal deploy, standalone Papers script, external CI, direct API and
dashboard actors can bypass the cooperative lock. Restrict production deploy and
bucket-configuration credentials to an enforceable single publication authority,
make every entry point use it, and design recovery for unknown external deploy
outcomes before enabling this path. A point-in-time bucket-lock check and an
advisory lock alone do not provide these guarantees. Historical Pages artifacts,
the immutable objects they reference, and permission to observe public release
identity are also required for rollback and recovery. No remote rules, schema,
permissions, site or emails were changed by preparing these components.
