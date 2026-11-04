# Data release automatic recovery

Raw archive publication uses eight workers for independent atomic file copies.
Set `RAW_ARCHIVE_COPY_WORKERS` or pass `--copy-workers` to
`scripts/publish_raw_git_archive.py` to select 1–16 workers. At most twice the
worker count is queued. Every file retains checksum validation, fsync and atomic
replacement; the repository lock covers all running copies. All copies must
finish successfully before stale-file cleanup, metadata writes or sequential
Git commits/pushes. An interrupted partial copy remains reusable on retry.

On the production archive filesystem, a repeated benchmark of 128 files totaling
8 MiB took 15.4–19.6 seconds at one worker and 3.2–3.8 seconds at eight, with
identical checksums. This measures verified copying and disk synchronization;
source inventory, large-file splitting and Git publication are additional work.
An already-running publisher keeps its initial implementation.

Scheduled releases and releases triggered by upstream completion recover from
recognized transient external failures without operator action. The failed task
keeps the same UUID and moves to the durable `retrying` state. Its task metadata
contains `automatic_retry.next_attempt_at`; the release scheduler atomically
moves it back to `queued` when the exponential delay expires. A process restart
therefore does not lose the retry timer.

The default delays are 5, 10, and 20 minutes, capped at one hour. Configure the
policy with:

```dotenv
DATA_RELEASE__AUTO_RETRY_MAX_ATTEMPTS=3
DATA_RELEASE__AUTO_RETRY_BASE_DELAY_SECONDS=300
DATA_RELEASE__AUTO_RETRY_MAX_DELAY_SECONDS=3600
```

`retrying` is also a release reservation. A scheduled tick, an upstream task,
or another release job cannot enqueue a concurrent pipeline while that
reservation exists. Task workers do not claim a delayed `retrying` row; only the
release scheduler can make it runnable at the persisted deadline.

## Retry boundary

The classifier is deliberately allow-list based. It retries recognized timeout,
connection, DNS, rate-limit/HTTP 429, and upstream HTTP 5xx signatures only when
they occur in an external release stage: Situation source refresh, Git archive
or download publication, strict subscription sync, Cloudflare deployment, or
Cloudflare production verification. Post-deployment reviewed-alert dispatch is
also eligible for recognized Worker transport, HTTP 429, and HTTP 5xx failures;
its report/signal idempotency key prevents duplicate mail. A transient Cloudflare integration
preflight is also eligible when its retained diagnostic has a matching
signature.

The following remain terminal and alert normally:

- manual releases;
- syntax/import/type and other code failures;
- contract, schema, validation, and release-gate failures;
- missing/invalid configuration or credentials, including HTTP 401/403;
- unknown failures without a positive transient signature;
- failures after the configured retry cap.

The legacy automatic-failure cooldown applies after a terminal or exhausted
upstream-triggered failure. It does not replace the short retry sequence.

## Idempotency and audit

Retries reuse the original task UUID and persisted release identity. Only one
release pipeline can be active across all release jobs because generated
artifacts share directories. When a Cloudflare deploy command has completed but
production verification fails transiently, a checkpoint is committed before
verification; the next attempt reuses that deployment and retries verification
instead of deploying a duplicate, provided the build digest still matches.

After local validation, failures retain the generated candidate under
`exports/release-candidates/<task_uuid>/` before restoring the prior local
artifacts. A retry of that same task skips generation/rendering only when the
source tree, configuration, selected dirty rows, base release and build digest
still match. Missing, incomplete or incompatible candidates are discarded and
rebuilt. A new successful base release invalidates older candidates.

Generation freezes the public curve projection in the private
`astro-site/src/data/_release-projection/` tree. The final transaction verifies
its checksums, writes those exact curves and acknowledges only the selected
markers. It does not re-query all observations after deployment. Legacy
artifacts without this snapshot retain the original full projection behavior
and are not eligible for candidate reuse.

The download generator atomically replaces changed partitions. Rollback
snapshots can therefore hard-link their immutable old bytes, avoiding a full
copy of the download archive. Other artifact trees retain independent copies.
Download publication stages at most 1 GiB of uncompressed Git blobs per push on
a content-addressed temporary branch. Once all objects are present, the public
branch advances in a normal fast-forward push and the temporary branch is
removed. An interrupted upload of the same snapshot resumes from that branch;
the public branch keeps its previous complete snapshot until the final push.
The download Git stage has a three-hour minimum timeout for large backfills;
the independent R2 upload timeouts are unchanged.
Only the bounded staging pushes disable Git's delta search. On 15 real changed
CSV/JSON/XLSX files (128.1 MiB), a local pack benchmark took 8.46 seconds with
Git defaults and 2.32 seconds without delta search, producing 24.46 and 25.84
MiB respectively. Transfer times vary; ordinary small pushes keep Git defaults.
Build validation caches gzip sizes by content SHA-256 and zlib version, while
re-evaluating current budgets and dependency graphs on every run. SEO target
lookups are memoized only within one audit; subsequent audits observe file
changes. French placeholder checks skip text extraction only when neither
marker's initial word is present. Literature-only releases reuse published
surveillance coverage without re-reading unchanged curve payloads.
Related-paper recommendations cache only their public projection, keyed by the
ordered catalogue's ranking/display inputs, result limit, and implementation.
Fresh surveillance links remain outside that cache. A checksum mismatch or
input/code change recomputes the recommendations.

XLSX core-property and ZIP timestamps are fixed, including the modification
timestamp overwritten by openpyxl during save.

Command completion events include `elapsed_seconds`. `Release Timing` records
the complete execution time including backups and cleanup, the backup time,
and whether the 600-second target was met. Exceeding the target is measured
rather than interrupting a valid release halfway through publication. Cold
builds, initial uploads and remote outages require separate measurements from
ordinary incremental releases.

Task workbook events expose `release_auto_retry_scheduled` and
`release_auto_retry_queued`, including classification, stage, attempt count,
delay, and deadline. Inspect `tasks.metadata.automatic_retry` for the durable
state and `tasks.metadata.release_checkpoints` for completed external effects.

The clean-worktree gate has one deliberate, narrow exception:
`configs/literature/weekly_ai_reviews.json` is an atomically-written runtime
quality registry maintained by the background Research Radar review job. It is
included in the generated release snapshot and is therefore reported under
`git.dirty_runtime_mutable_paths` without blocking an unattended release.
The release manifest still marks such a snapshot as dirty for provenance.
All other tracked changes remain blocking, and the preflight payload retains
both the complete `git.dirty_paths` list and the effective
`git.dirty_blocking_paths` list for auditability.

If a task is terminal, fix the reported code/configuration/credential/gate issue
and use the normal manual retry action. Do not broaden the transient pattern list
to mask a deterministic failure.


## Measured rollout on 2026-11-02

Production verification passed for recovery and both subsequent incremental runs.
Execution times include rollback snapshots and cleanup, and exclude queue wait.

| Release | Scope | Total | Generation | Build and checks | R2 publication |
| --- | --- | --- | --- | --- | --- |
| `20261102T062533Z-a5495c3d9420` | Recover 152 jurisdictions after an interrupted upload | 26m 22s | 483.7s | 434.9s | 133.2s; 15 changed / 23,871 reused objects |
| `20261102T070207Z-a5495c3d9420` | Iceland plus 1,078 changed research article bundles | 18m 14s | 158.9s | 417.6s | 246.6s; 3,162 changed / 20,724 reused objects |
| `20261102T072144Z-a5495c3d9420` | No country changes; one withdrawn article, 14 article bundles rebuilt | 15m 01s | 117.3s | 372.2s | 193.0s; 69 changed / 23,813 reused objects |

The recovery reused objects from the earlier interrupted upload; its R2 time is
not the cost of uploading an empty bucket. The second release transferred
307,963,237 R2 bytes, and Pages uploaded 3,305 files while reusing 6,837. Downloads
changed only their manifest. These are real incremental transfers, but none of the three
runs met the 600-second execution target.

On the same 5,616-article input, recommendation computation took 42.559 seconds;
checksummed reuse took 0.710 seconds and matched the full result exactly. This
cache was introduced after the two releases above and must not be credited to
their measurements. In the third run, the removal of one article correctly
invalidated the catalogue cache and forced recomputation; it was not a warm-cache
hit. That run reused 5,601 article bundles and 16,803 rewritten HTML files,
yet transmitted 173,368,039 R2 bytes and uploaded 2,103 Pages files. It skipped
unchanged download and raw-archive channels. The 15m 01s total includes 74.0s
of backups; this is not a claim that the ten-minute target has been achieved.


The next optimization batch adds two changes:

- Research country/disease/topic/week collections and surveillance country and
  disease pages reuse three-locale bundles when their data and shared templates
  match. All cached output checksums and asset references are checked, and all
  validation gates still run. Missing/corrupt bundles rebuild; removed routes
  are not restored. Country/disease navigation depends on global country data,
  which is deliberately included in their fingerprints. Authored data modules
  such as `changelog.ts` and international-map availability are shared inputs.
- Browser research arrays use record-derived boundaries and content-addressed
  shard names, with 48-hour retention for retired URLs and index-last uploads.
  In a matched simulation on 5,615 articles, deleting the same one article
  transferred 26,729,901 bytes with sequential shards versus 6,224,344 bytes
  with stable shards; generation took 17.328s versus 5.473s. These are local
  benchmark results, not a production release timing or the earlier 173 MB run.

The first production rollout of these changes, `20261102T080349Z-a5495c3d9420`,
completed and passed production verification in **34m 25s**. It rebuilt 5,622
article bundles and populated 1,933 other page bundles. A changed shared CSS hash
also changed every article's asset URL: R2 updated 17,048 objects (940,858,109
bytes), reused 7,034, and took 906.9s. Build/checks took 631.5s, generation 109.5s,
and final Pages deployment 230.8s. Total time includes 50.3s of backups. This is
a cache/schema rollout with global asset changes, not an ordinary warm release.

That rollout exposed a cross-host sequencing issue: R2 HTML can reference a new
main-site asset before the final Pages deployment. A compatibility deployment
published the prior site plus the new stylesheet (one uploaded file, 10,142
reused), and its public bytes/CORS were checked. The pipeline now performs this
step before R2 when new hashed assets exist, with no extra deploy for unchanged
assets. The final publication passed checks of 18 main-site locale pages, three
paper locale pages, three content-hashed shards, release identity and shared
asset CORS.

The subsequent release completed on 2026-11-02 at 17:01 China time in **17m 29s**
(1,048.832s execution, plus about 4m 57s queue wait). It included a collection
update and raw archiving, so it was not an unchanged-data benchmark. Generation
took 80.1s; build/checks 575.8s; R2 73.3s; raw archiving 204.5s in parallel with
R2/downloads; final Pages deployment 53.4s. R2 reused 24,080 objects and uploaded
106 (101,139,162 bytes); the completion manifest's encoded body was 2,309,663
bytes. Pages uploaded 553 files and reused 9,592. Only one article bundle and
639 other page bundles were reusable, despite far more final objects remaining
unchanged. Input fingerprint churn and the coupling of HTML/card rendering are
therefore still material build bottlenecks. **The ten-minute target remains
unmet**; reduced network transfer is not equivalent to meeting that target.

Further opportunities remain:

- The recommendation cache currently invalidates the entire catalogue when an
  article is added, changed or removed. A further dependency-aware cache can
  restrict recomputation to affected rankings.
- Card rendering shares an article-bundle fingerprint with HTML. A signal-only
  HTML change can therefore regenerate an identical card before upload detects
  that its bytes are unchanged.

These limitations are not covered up by changing the elapsed-time definition
or by disabling validation gates. Large updates and transient network delays
still exceed the target with the current implementation.
