# Runtime repairs — 26 November 2026

The 24-hour audit covered 25 November 11:08 through 26 November 11:08 in
Asia/Shanghai. Collection continued, but Literature synchronization stopped
advancing, publication attempts timed out, and several sources repeatedly
requested unavailable files.

## Changes

- Schedule-state reads and writes tolerate SQLAlchemy connection-pool timeouts.
  Literature retries transient database failures on its normal polling cadence.
  The scheduler supervises its required child loops; an unexpected child exit
  stops the process after cleanup so systemd can recover it. Normal shutdown
  does not trigger that failure path.
- Literature publication monitoring and scheduling share the same completion
  time plus interval calculation. A long-running publication no longer makes
  the monitor claim that the next publication was due before the scheduler
  would allow it.
- R2 publications serialize within each channel. Different channels can upload
  concurrently under a shared bucket fence. Garbage collection still requires
  an exclusive bucket fence and protects staged references. Deferred garbage
  is retried before the next capacity preflight. A remotely committed pointer
  with an incomplete local ledger is reconciled before cleanup.
- Publishers report channel-lock acquisition, object-upload progress and
  transfers waiting without progress. These logs distinguish time waiting for
  another publisher from actual upload time.
- AI upstream and channel failures preserve Retry-After. An explicit provider
  deadline starts the route cooldown on the first failure and survives an
  already-running successful request. Ordinary transport failures retain the
  existing circuit threshold and bounded attempt policy.
- Thailand DOE506 report-file 404 responses receive a per-URL 24-hour negative
  cache. Discovery remains live, new URLs are eligible immediately, force can
  bypass the cache, and expiry permits recovery probes. Missing reports remain
  explicit partial-collection evidence.
- New Zealand's DHB CSV uses the existing mirrors with one attempt per mirror
  and a 10-second connect / 30-second read timeout. Temporary failures receive
  a 15-minute per-URL cooldown. Both source cooldowns persist atomically across
  processes and fail open if the local cache cannot be read or written.

## Validation

Focused regressions passed for scheduling and task cleanup (54), source
collection and capture (163), AI routing and durable activities (157), and
publication, object retention and recovery (234). These are test executions in
the respective suites, not a claim about every repository check. Modified AI
modules and their tests passed Ruff; new source-cache modules and tests also
passed Ruff. The final source diff passed whitespace checks.

Two independent publication reviews checked same-channel exclusion,
cross-channel object-index safety, staged-reference protection, legacy bucket
maintenance, deferred-GC capacity recovery, and the remote-pointer/local-ledger
crash window.

## Runtime maintenance

The existing worker stop deadline is 15 minutes. CRAWL_DATA and EXPORT_DATA
are not automatically resumable after a forced worker stop, so maintenance
waits for them to finish instead of letting the service manager kill them.

- The scheduler was stopped and automatic JSON publication was temporarily
  paused, with the original channel controls saved locally for restoration.
- The old scheduler's shutdown exposed its previously failed Literature child
  task: SQLAlchemy QueuePool timeout in schedule_state.load. This confirms the
  specific exception behind the synchronization outage.
- Literature task `016b9daf-96fa-4fc3-affc-a8a2263fe8cb` was verified to be in
  its local build phase, with no ready/published receipt. It was cooperatively
  cancelled so its owned build process could exit before worker maintenance.
  The frozen snapshot and unacknowledged dirty markers remain. This original
  task will be retried after reloading the worker.
- Existing Surveillance publication and country collections are allowed to
  finish naturally before the standard runtime restart command is used.

Maintenance completed using
`venv/bin/python scripts/restart_task_runtime.py --include-api`:

- Thailand task `459bf49f-8f37-4495-a55b-8f3bc5008bca` completed naturally at
  11:34:18. The existing Surveillance task
  `7868fac9-6a3a-450a-af9e-19069f379278` completed naturally at 11:29:31.
- Both workers stopped with systemd result `success`. Three resumable AI
  tasks were requeued by the existing graceful-restart mechanism. No
  non-resumable collection or upload was killed by the worker stop deadline.
- API, general worker, AI worker, scheduler and all 12 auxiliary model
  dispatchers restarted. The maintenance command emitted `runtime_ready` with
  status `ok`; the independent readiness check at 11:37 also returned `ok` for
  PostgreSQL, Redis and all required runtime roles.
- Literature synchronization `4bbf4974-c7c8-4b88-8902-e6507a11d8d0` started at
  11:36:18 with no error. The next scheduled run advanced to 11:51:17.
  Enrichment and evidence-gap discovery also received new scheduled tasks.
- The original Literature publication was retried with its original UUID,
  frozen inputs and selected dirty markers. All three automatic-publication
  controls were restored to their original unpaused values at 11:36:48.

Live recovery proves that scheduling resumed. It does not establish that the
Literature index backlog has caught up or that the multi-stage publication has
completed. Those continue through their normal durable tasks. Temporary
maintenance receipts and post-restart evidence are retained in
`/tmp/globalid-repair-20261126-*.json` on this host.

## Remaining source and service limitations

Upstream 503/429 responses, exhausted provider credit, missing China CDC Weekly
responses, Taiwan source exports, and the raw Papers upload throughput require
continued observation or source-specific work. This repair does not claim to
make unavailable upstream content available. It does not change disease
mapping assertions, fill absent observations with zero, or enable the eight
countries whose publication is explicitly restricted pending source review.

## Follow-up inspection and preparation repair

The 14:54–15:11 Asia/Shanghai inspection found that process readiness alone
had concealed a remaining publication blocker. The original Literature task
had been claimed after the morning retry, but at 11:37:10 its prepare-build
command rejected the old renderer receipt with `Research renderer inputs
changed after preparation`. It then stayed QUEUED with a `contract_blocked`
wait. The earlier verification had stopped at requeueing and missed this
subsequent failure.

The explicit prepare-build subprocess now archives unfinished local build
metadata before recapturing changed renderer inputs. It preserves the frozen
data snapshot, public context, captured remote baseline and selected dirty
markers. Ready, prepared, completed-build and committed candidates cannot use
this path. Overlapping stage/run paths, including using the snapshot as the
stage directory, are rejected before moving files. Ordinary frozen-input
validation remains strict. The supporting six test files passed 129 tests.

At 15:09:24, the original task was actually claimed again. The new subprocess
archived its old renderer receipt, template baseline and projection baseline
under `build-recovery/prepare-ugqgr2av/recovery.json`; a fresh projection baseline
was written at 15:10. This recovery loads in the existing subprocess boundary
and did not require another runtime restart. Publication completion and dirty
acknowledgement remain later stages and must not be inferred from build startup.

Other findings from the 11:36–14:56 inspection window:

- Literature synchronization completed 13 runs with no final failures, added
  719 articles and made 4,045 updates. Its completed checkpoint advanced by
  6 hours 7 minutes, but still lagged the inspection time by about 16 hours.
  The roughly 89,000-article review backlog still prevents accelerated catch-up.
- Surveillance completed 22 publications and Situation completed 6; there were
  no further publication timeouts in this window. All four primary services
  remained active with zero automatic restarts and no new scheduler pool errors.
- Thailand's initial 72 missing URLs were followed by 432 cooldown deferrals,
  with no repeated 404 requests for those URLs. Seven refreshes completed with
  864 rows each. New Zealand completed 133 DHB collections; 61 used the existing
  mirror, and mirror/canonical raw CSV hashes agreed. A temporary NZ-SCT failure
  recovered on retry at 12:54:45.
- Taiwan had another HANTA source timeout and entered automatic retry. The
  official source availability is still a limitation.
- AI failures continued. Model 180 `hy4flash` exhausted its monthly quota and
  retried in clusters after its normal 15-minute cooldown. Model 176
  `hy4flash-free` remained available (92 successes and 1 failure over the
  30-minute sample ending 15:02:51). There was no evidence of a success or
  health probe prematurely clearing the exhausted route's cooldown. No model
  configuration or paid quota was changed.

Follow-up evidence is stored locally in `/tmp/globalid-recheck-20261126-*.json`
and the corresponding journal capture. These are bounded observations, not a
claim that the Literature publication or backlog has completed.
