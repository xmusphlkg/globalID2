# Data release automatic recovery

## Independent surveillance channel (2026-11-07)

The redesigned data path is described in [STATIC_SITE_AND_DYNAMIC_DATA.md](../architecture/STATIC_SITE_AND_DYNAMIC_DATA.md).
After its one-time migration, surveillance updates publish versioned browser
JSON independently of Astro, Pages, Papers/Reports rendering and download
formats. A synthetic single-observation D038 revision on a prepared real
eight-region baseline took 3.903 seconds for local JSON export; this excludes network upload
and final database acknowledgement. The historical site-release measurements
below describe the previously coupled pipeline.

## Country update latency and renderer memory (2026-11-07)

The production task `440bd7d8-ec31-4d00-a4f8-8a34a0f17434` selected eight
Japanese subdivisions. Its site-data command took 474.4 seconds, including
287.4 seconds for database reads and projections and 101.4 seconds for
partitioned downloads. Astro then failed after 634.4 seconds: the Node log
shows a 6 GiB V8 heap exhaustion in `JSON.parse`. The task did not publish or
acknowledge those selected changes. These are timings for the code running on
November 6, before the later v0.17.5 commit, not a benchmark of current code.

The preceding successful task, `99074217-6728-45da-9617-65ed2bf27bd3`, took
2,763.3 seconds: site generation 462.3 seconds, Astro 1,009.1 seconds, Papers
and Reports splitting 127.4 and 40.0 seconds, Papers R2 publication 322.7
seconds, and Pages deployment 112.2 seconds. Those recorded stages do not sum
to the total: the workbook also has about 6 minutes between Reports splitting
and release identity, and 4.5 minutes between deployment and completion. Those
intervals include receipt, digest, snapshot and finalization work, but lack
individual timings; the exact contribution of each is not established.

Selected jurisdictions still read their historical observations, construct
country and disease projections, and reconcile affected cross-country download
partitions. The pipeline then renders and validates shared site routes and
reconciles independent destinations. Exact dirty scopes and reuse reduce work,
but do not yet provide time-window-level SQL projection or independent
country-only publication. Sequential bounded batches also add queue latency
when more than eight scopes are pending. Cold caches, global template changes,
research changes, and network transfer can increase elapsed time.

At 09:12 China time on November 7, `site-release` was enabled for 02:00 UTC
(10:00 China time) daily, with `auto_after_crawls` disabled; this configuration
does not automatically start a publication after each collection. There were
82 pending surveillance markers across 61 distinct scopes, including scopes
that may be disabled. The failed November 6 task was terminal because Astro
build failures are outside the external-transport automatic retry policy;
its continuation was therefore not completed. These observations describe the
configuration and queue at that time, not a recommended schedule change.

Collection has a separate serialization bottleneck. All live dual writes and
other disease mutations use the transaction-scoped advisory lock in
`src/core/disease_mutation_lock.py`. On November 6, a US NNDSS task ran from
21:15 to 22:27 China time. Its log shows 392,447 source observations passed to
the series write and a quality check covering 1,508,374 historical observations;
its series write ran from approximately 21:41 to 22:27. A Swedish ECDC task
started at 21:50 but its 942-row series write did not begin until 22:27, after
the US transaction. This timing is consistent with contention on the shared
lock, although the logs do not independently record its wait duration. Do not
remove the lock without replacing its cross-writer ordering and atomicity
guarantees; large imports and global ontology operations can overlap keys.

The research index was approximately 290 MiB on disk at investigation time;
its parsed representation is larger. Parallel Astro prerender workers have
independent module caches, and the sitemap formerly parsed a second copy in a
worker. The default now disables parallel prerender workers and shares the
generated-data reader with the sitemap. `GIDS_ASTRO_RENDER_WORKERS` remains an
explicit opt-in; increasing it duplicates input and cache memory and must be
benchmarked with the actual corpus and service memory budget. The existing
per-process Node heap setting is not a limit on the entire service cgroup.
Lower worker concurrency can increase cold render time. Verify production
latency and peak memory after rollout; a local build does not prove the
end-to-end release target is met.

An isolated cold build using a copy of the November 7 generated inputs and a
6 GiB Node old-space limit rendered all 27,780 pages in 659.8 seconds, including
the sitemap that failed previously. The renderer's observed peak RSS was about
12.5 GiB. The complete local build passed the performance, SEO and translation
checks and cache promotion in 835.5 seconds; the cold performance audit alone
took 93.6 seconds. These figures exclude database export, uploads and queue
time; they are not an incremental-release benchmark.

## Bounded publication and stage recovery (unreleased)

Migration `0026_publish_dirty_changes` adds nullable `publish_dirty.change_set`.
Apply this migration before restarting API, scheduler, and workers with the new
code. Existing rows remain valid and conservatively retain their original scope.

New ingestion writes a precise change set only when it can account for every
published observation change. Verified no-op imports create no publication marker.
Legacy-only imports, incomplete mappings, deletions, and supplemental legacy
metrics retain conservative invalidation. A precise country marker never expands
again into its children during export or publication acknowledgement.

`DATA_RELEASE__BATCH_MAX_COUNTRIES=8` limits normal incremental batches. All dirty
IDs for the same scope are coalesced, and only selected IDs are acknowledged.
An indivisible legacy parent scope may exceed this limit; the release plan records
that exception. Continuations carry the original deferred IDs, not newly arriving
changes. Completion hooks and scheduler reconciliation enqueue the remaining
batches. Disabled scopes remain pending without creating an endless continuation.

Downloads plan previously split child windows before creating formats, reusing
unchanged files and their modification times. Refreshes use lossless observations;
an old public-curve snapshot cannot replace provenance in the download source.
Missing files outside the selected refresh scope fail with the affected scope
instead of silently preserving a broken manifest.

Cross-country disease downloads replace only the selected countries' rows. They
retain every other public country's rows from checksum-verified local download
JSON; disabled countries are removed. Historical `published_series` rows remain
explicitly counted as legacy projections, not relabelled as recovered source
observations. Only freshly generated rows must pass the lossless-input gate.
Disease input fingerprints combine country input summaries and display metadata
instead of reserializing the entire cross-country history. Affected local JSON
partitions still need to be read for verification and merging.
If an interrupted generation replaced a partition before updating its manifest,
the merger can read that partition from the download repository's local Git
`HEAD`, but only when its size and SHA-256 exactly match the manifest. It neither
fetches remote files nor overwrites the working tree during recovery.

In-place stage receipts live in `exports/release-stages/`. A retry checks source,
configuration, destinations, selected IDs, the successful release baseline, and
output file inventories. Successful export survives a later template build failure;
successful rendering survives a later upload failure. An unrelated later deployment
invalidates the earlier receipt. The independent atomic
`exports/successful-site-release.json` marker does not depend on retention of the
large Pages snapshot. File inventory checks read metadata and only hash files
within the timestamp uncertainty window; they still enumerate local files.

Raw-source archival has a separate low-priority queued task, worker admission lane,
preflight, and retry record. Its failure cannot turn a successful site publication
into a failed one. Queued archive work coalesces; an active archive can have one
trailing job so newly collected files are not lost.
The enqueue trigger is still a successful data publication: new raw files from
semantic no-op collections wait for a later data release. An independent periodic
archive trigger is not part of this change.

The control panel's code-only action rebuilds templates using the last attested
published local data. It does not export observations, publish downloads, acknowledge
dirty markers, or start data notifications. A missing attestation or changed local
data requires a normal release first. This includes local data changed by a failed
generation. The first normal successful release after upgrading creates the
attestation. Files under the frozen data roots, including authored data modules,
must remain unchanged for code-only publication.

Limits: a selected country still reads its historical observations, and unchanged
download windows still compute their JSON content summaries. This is not yet
time-window-level SQL projection. Literature markers coalesce but do not yet drive
article-level database exports. Shared layout/style changes legitimately rebuild
their dependent routes. The new page fingerprint format requires one initial cache
rebuild. Fixture timings do not establish production end-to-end performance.

## Publication completion policy (2026-11-04)

Release tasks complete on successful upload/deploy commands, without probing
the public Papers, Reports or main-site URLs afterwards. Both full and
reports-only tasks log `Website Verification Skipped` and return
`website_verification: "skipped"`, `artifact_rollback: false`. A public-host
Cloudflare challenge therefore cannot turn a completed upload into a failed
release. Upload and build failures still fail the task.

Release tasks no longer create rollback copies or restore old generated files.
Current artifacts, Git history and successful channel receipts stay in place
after a later failure. The exporter and uploaders reuse their content hashes on
retry; a successful Pages deploy can also be reused by its recorded digest.
Legacy retained candidates can still be read, but new failures keep artifacts
in their working directories instead of moving them to rollback snapshots.

Download publication records Git's successful push acknowledgement without a
second public-file check. `--verify-remote` remains an explicit operator audit.
Database change-log acknowledgement follows successful publication commands,
not public website verification. These rules supersede the older verification
and rollback behavior described in historical operational notes below.

## Incremental site builds and R2 publication (2026-11-05)

The source repository remains the authority for templates; existing database
change-log IDs select incremental data exports and are acknowledged only after
publication succeeds. No nested Git repositories or file-save deployment watcher
are needed. These changes optimize existing release jobs; they do not enable a
new GitHub push trigger or change the configured release schedule.

- Shared source fingerprints use Git index object IDs for clean tracked files.
  Unstaged, untracked, ignored and non-Git inputs use a persistent local content
  cache. Existing template dependency invalidation remains conservative.
- Research and main-site page planning reuse cached file fingerprints. Independent
  report builds now use the same report/archive/disease dependency rules, with
  separate page and asset caches. Reports with unchanged dependencies reuse all
  three locale outputs; directory/control pages are still rendered. Missing
  assets trigger a full rebuild, and changed inputs during rendering fail the
  build instead of accepting a mixed snapshot.
- Papers/Reports bundle writers record SHA-256 and single-PUT MD5 digests while
  holding generated bytes. A local artifact index stores these with device,
  inode, size, nanosecond mtime and ctime. Metadata-only checks can reuse stable
  receipts; missing, malformed or changed receipts require reading content.
  Files still within a one-second timestamp window are revalidated to avoid
  same-size edits hidden by filesystem timestamp rounding.
- R2 publication reads the remote manifest and paginated object metadata, never
  all remote bodies. Matching local receipts and remote size/ETag skip local
  body reads and uploads. Changed objects are validated before upload. Actual
  object metadata still detects overwritten/missing objects and resumes partial
  uploads even when the previous manifest was not committed. Multipart ETags
  do not qualify for this shortcut.
- Assets precede HTML, public indexes follow shards, and the manifest is written
  after successful transfers and deletion handling. Per-target publication locks
  and the existing asset retention window remain in effect.

Local caches are under `data/cache/build-file-fingerprints.json`,
`data/cache/artifact-index/`, `data/cache/reports-build-manifest.json`,
`data/cache/reports-pages/`, and `data/cache/reports-assets/`. They are not uploaded.
A lost cache costs revalidation/rebuilding, not correctness. Set
`GIDS_REPORTS_FORCE_FULL=1` for an explicit full isolated report build. Main-site
Pages uploads continue through Wrangler; the custom manifest publisher applies
to Papers and Reports on R2.

This avoids repeated full **content reads**, not every directory walk/stat or
remote metadata request. It also does not remove required rendering after shared
template changes or data parsing needed to determine page dependencies.

Validation commands:

```bash
venv/bin/python -m pytest -q tests/unit/test_artifact_index.py tests/unit/test_papers_bundle.py tests/unit/test_reports_bundle.py tests/unit/test_papers_r2.py tests/unit/test_papers_r2_client.py
(cd astro-site && node --test scripts/*build-cache.test.mjs scripts/file-fingerprints.test.mjs)
```

### Validation recorded on 2026-11-05 (Asia/Shanghai)

- 89 targeted Python tests and 9 Node tests passed, including one-file changes,
  unchanged publication, lost receipts, same-size local tampering, interrupted
  uploads, remote drift, shared-template invalidation and independent report
  cache selection.
- A full isolated report build rendered 1,230 HTML pages in 50.82 seconds of
  Astro build time. The next build reused 409 three-locale bundles (1,227 pages)
  and rendered 3 directory pages in 4.20 seconds. All 1,230 resulting HTML files
  matched the full-build SHA-256 values exactly. These are rendering timings,
  not total export/build/publication times.
- A read-only production R2 publication replay checked 1,284 existing report
  objects. The cold local cache read 451,772,051 bytes and finished in 6.354
  seconds. The warm replay finished in 3.783 seconds with zero local artifact
  body reads, uploads or deletions. The test allowed only manifest GET and
  object-list requests; unexpected writes or page downloads would fail it.
- This verification did not deploy changed main-site templates, change release
  schedules, or dispatch subscriptions. The existing public-URL verification
  policy remains unchanged.

## Persistent download worktree and publication log

The default download publisher adopts `exports/site-downloads` as its Git
worktree. Later exports write directly into that worktree: there is no second
asset copy. Git metadata lives in `exports/.site-downloads.git`, outside artifact
backup/rollback, and the export tree contains only a `.git` pointer. The first
publication fetches the remote baseline and resets the index without replacing
generated files. Later runs retain local commits, including unpushed commits.
The previous `external-data/globalID2_data_download` checkout is left intact;
an explicit `--workdir` still supports the legacy separate-checkout workflow.

The download manifest records when its content changed. Re-running the exporter
does not update that timestamp or commit per-run generation counters. For
committed files, the exporter uses Git's index and checks that manifest hashes
still agree with the committed manifest, avoiding repeated SHA-256 reads of
unchanged CSV/JSON/XLSX files. New or modified files retain checksum validation.
The generated repository sets `core.trustctime=false`: creating hard-linked
artifact backups changes ctime without changing content. Git still checks
mtime, size and inode, including the generator's atomic file replacements.

After a successful push (and remote verification when explicitly requested), the publisher atomically writes
`exports/.site-downloads.git/download-publication.json`, containing the remote,
branch, commit and UTC `verified_at` (the legacy name for the publication timestamp). A clean Git worktree at that exact published
commit skips validation, upload and remote verification. Preflight also reuses
this record instead of repeating remote read/write probes. A clean tree without
a matching success record still attempts publication: a prior push or remote
verification may have failed. Publication is locked per worktree; remote branch
advancement is rejected before staging upload batches, preserving concurrent
remote commits.

Each publication success also appends its timestamp and commit to
`exports/.site-downloads.git/download-publications.jsonl`. The latest receipt is
the no-op checkpoint; the JSONL file is the audit history. A crash between the
append and checkpoint replacement can produce a repeated event for the same
commit on retry, without advancing any database acknowledgements.

This no-op path assumes the publication branch is owned by this publisher. It
does not continuously detect out-of-band remote deletion or force pushes. Use
`scripts/publish_download_repo.py --push --verify-remote` for an explicit remote
check, or add `--verify-checksums` for a full local checksum audit. Destination
changes require a separate export directory. These audits are operator actions,
not repeated work in every unchanged release.

## Database change-log boundaries

The existing `publish_dirty` table is the durable database update log. Formal
data writes and their log entries share a transaction. Entries include the
affected line/scope, source task and `marked_at`; successful production release
adds `published_at` and `published_task_uuid`. Pending entries are selected by
`published_at IS NULL`, so an old reporting period corrected today still
refreshes its affected country/disease exports.

Both incremental and full tasks now freeze exact `publish_dirty_ids`, plus
`publish_selected_at` and `publish_change_count` in task input. The final release
transaction acknowledges only those selected IDs and eligible scopes. A
transaction committed later remains pending even if its timestamp is older
than the selection time. A failed download publication does not advance its
success receipt; a failed website release does not acknowledge database changes.
Download publication can succeed before
the website release; its own receipt then avoids re-uploading on retry while
database markers remain pending until the website deployment succeeds. Older queued
full tasks retain support for their legacy `publish_dirty_before` boundary.

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
artifacts share directories. When a Cloudflare deploy command completes, a
checkpoint is committed before final database acknowledgement. If a later step
fails, the next attempt reuses that deployment instead of deploying a duplicate,
provided the build digest still matches. Public verification is skipped.

Earlier releases retained failed candidates under
`exports/release-candidates/<task_uuid>/` before restoring prior local artifacts.
New failures keep generated files in place without rollback. A retry can still
reuse a legacy candidate of that same task only when the
source tree, configuration, selected dirty rows, base release and build digest
still match. Missing, incomplete or incompatible candidates are discarded and
rebuilt. A new successful base release invalidates older candidates.

Generation freezes the public curve projection in the private
`astro-site/src/data/_release-projection/` tree. The final transaction verifies
its checksums, writes those exact curves and acknowledges only the selected
markers. It does not re-query all observations after deployment. Legacy
artifacts without this snapshot retain the original full projection behavior
and are not eligible for candidate reuse.

The download generator atomically replaces changed partitions. Previous rollback
snapshots could hard-link their immutable old bytes; the current release pipeline
does not create rollback copies for downloads or other artifact trees.
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
the complete execution time including cleanup, a zero backup time,
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


## Release admission and collection priority

The worker claims publication tasks ahead of every other priority, including
older `urgent` tasks. Managed collection tasks remain running but yield before
opening their next database session. The release waits for already-admitted
database scopes to commit or roll back and close, then holds admission through
publication and cleanup. Collection resumes in the same coroutine afterwards,
including when publication fails or is cancelled; no retry is consumed.

Nested database scopes can finish their admitted unit so draining cannot
deadlock on task progress or cancellation checks. A long existing database scope
still delays publication until it closes. Network and model work outside those
scopes no longer has to finish before a release starts. Reports-only publication
keeps its existing ability to run alongside collection.

Admission is local to the singleton worker. Running writer tasks outside its
cooperative execution registry retain the conservative task-level exclusion;
independent CLI/API mutations are not covered by this in-process gate.

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


## Exported read snapshots

发布使用 PostgreSQL 的导出快照，不再把整个任务当作数据库互斥区间。普通采集、解析、原始文件登记和正式数据提交继续运行；每个正式数据事务仍同时记录自己的 `publish_dirty` 变更。

### 执行边界

1. worker 领取发布和采集任务到各自的并发通道。发布之间仍串行，防止多个生成器同时修改站点数据、下载文件和构建目录；原始归档保留独立通道。
2. 发布导出阶段开启一个 `REPEATABLE READ, READ ONLY` 事务并调用 `pg_export_snapshot()`。只有报告、Situation 和站点数据导出子进程收到 `GLOBALID_RELEASE_DB_SNAPSHOT`。
3. 子进程的 `get_db()` 在每个新事务的第一条业务查询之前导入该快照，包括显式提交后重新开始的事务。数据库拒绝写入；无效或过期快照直接报错，不能回退到最新数据。
4. 导出得到的曲线、水位和校验值保存在 `_release-projection`。导出结束就关闭数据库快照，构建和上传只使用文件，不持有数据库快照或采集互斥锁。
5. 部署成功后，公开曲线从已冻结的文件写入，只确认任务中明确选中的变更 ID。同一国家在导出、构建或上传期间新提交的变更仍待发布。

快照不会复制整个数据库，也不要求读副本。快照事务只覆盖有超时限制的导出阶段；它仍会保留旧行版本，因此不能把其生命周期扩展到网络上传。现有网站构建耗时、外部源 TLS 故障和同一疾病写事务锁不会被此改造自动消除。

### 重试与保护

- 导出未完成：关闭快照，重试取得新快照。
- 已完成导出或构建：通过已有文件收据校验后复用原文件，不混入重试时的新数据。
- 缺少快照标识、曲线文件损坏或使用旧的时间戳确认规则：拒绝发布或确认，不能重新读取实时数据冒充旧版本。
- 不要将 `GLOBALID_RELEASE_DB_SNAPSHOT` 写进 `.env`。它只属于当次导出子进程，owner 事务关闭后便失效。
- 手工修改站点生成目录或并行运行其他生成器仍需要遵守发布串行约束；数据库快照不能保护文件系统上的并发修改。

### 验证

`tests/integration/test_postgres_release_snapshot.py` 在指定 PostgreSQL 中创建并清理唯一测试 schema，验证并发写入、跨进程一致读取、只读拒写、失效快照和取消清理。设置 `TEST_SNAPSHOT_DATABASE_URL` 为测试连接串后运行该文件；默认跳过，不会隐式使用生产连接。

发布管线测试同时验证快照在构建/上传前关闭、重试复用文件，以及同一国家的新变更不会被旧发布确认。

参考：[PostgreSQL SET TRANSACTION](https://www.postgresql.org/docs/current/sql-set-transaction.html)。
