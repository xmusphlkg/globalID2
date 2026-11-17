# National notification history and ongoing refresh

TH, MY and NL source adapters retain published source categories, geography
and measure under each source identity. Bulk releases refresh their published
window; the current TH DDS sources use bounded per-category recovery and a
configured recent-revision window. Publication checks detect new releases and revised older counts;
their polling interval does not change the source's observation frequency.

## Persistent jobs

[`scripts/configure_national_notifications.py`](../../scripts/configure_national_notifications.py)
manages exactly these source-specific jobs:

| Job ID | Source | Schedule | Timezone |
| --- | --- | --- | --- |
| `th-dds-tableau-daily` | `th_dds_tableau` | Every 30 minutes | Asia/Bangkok |
| `th-dds-weekly-daily` | `th_dds_weekly` | Every 30 minutes | Asia/Bangkok |
| `th-doe506-daily` | `th_doe506_monthly` | Every 30 minutes | Asia/Bangkok |
| `th-id-th-history-daily` | `th_id_th_history` | Every 30 minutes | Asia/Bangkok |
| `my-moh-std-daily` | `moh_std_annual` | Every 30 minutes | Asia/Kuala_Lumpur |
| `nl-rivm-notifiable-daily` | `rivm_notifiable_annual` | Every 30 minutes | Europe/Amsterdam |
| `my-idengue-daily` | `my_idengue_daily` | Every 30 minutes | Asia/Kuala_Lumpur |
| `my-who-flunet-daily` | `who_flunet_weekly` | Every 30 minutes | Asia/Kuala_Lumpur |
| `my-who-gho-daily` | `who_gho_annual` | Every 30 minutes | Asia/Kuala_Lumpur |
| `nl-rivm-virological-daily` | `rivm_virological_weekly` | Every 30 minutes | Europe/Amsterdam |
| `nl-rivm-respiratory-daily` | `rivm_respiratory_weekly` | Every 30 minutes | Europe/Amsterdam |

All eleven jobs use `interval_minutes=30` and `daily_time=null`; their display names identify
the 30-minute checks. Existing job IDs retain the historical `-daily` suffix
so task and run history stays attached to the same control-panel jobs.
Intervals use the existing task-management scheduler, queue, worker leases,
country concurrency guard and retry behavior. They do not add a separate timer
or bypass control-panel pause/resume controls.

The 2026-11-16 cadence change was applied with the dashboard ingestion-job
GET/PUT API. Only names, `interval_minutes` and `daily_time` were updated;
enabled/paused state, priority, retries, source identity and task history were
preserved. The configuration and runtime evidence is
[`my_nl_halfhour_control_plane_20261116.json`](../../reports/onboarding/my_nl_halfhour_control_plane_20261116.json).
Every scheduler tick now refreshes persisted schedule state, so a control-panel
change is observed by the separate scheduler process without retaining an old
cached next-run time. Rescheduling also retains the previous task state.
An in-flight enqueue records its task result without overwriting a later
control-panel change to the next run or a pause.
Pausing stops future automatic scheduling; running tasks continue to use the
existing task cancellation and completion controls. A busy country can still
produce an `already_running` skip under the normal concurrency guard; the
30-minute interval is a check cadence, not a guarantee of a completed import
every 30 minutes.

```bash
# Preview changes without writing jobs or queueing ingestion.
venv/bin/python scripts/configure_national_notifications.py

# Persist managed jobs. Existing paused jobs remain paused.
venv/bin/python scripts/configure_national_notifications.py --apply

# Explicitly resume paused managed jobs as well as applying configuration.
venv/bin/python scripts/configure_national_notifications.py --apply --enable
```

After committing changed jobs, the script reschedules only those jobs through
the scheduling service so their next run is persisted immediately. Dry runs
and unchanged jobs do not reschedule anything.

The script preserves all other jobs and does not bootstrap country metadata,
run historical imports, send messages, or change public release policy.
Country rows must exist before the scheduler can enqueue their ingestion.
`process=true` and `save_raw=true` ensure source bytes and source-series facts
are persisted on a successful run. `fill_missing=false` avoids inventing
unpublished cells. The adapters reread source history with `force=false`;
the scheduler's three-month field does not truncate annual rows to January.

The service's environment seed only runs when `automation_jobs` is entirely
empty. Adding environment JSON to an existing deployment does not install a
new persistent job. The setup command or ingestion job API is required.

## Job API and runtime

The existing API exposes:

- `GET /api/v1/schedules/ingestion/jobs`: persisted jobs and scheduler state.
- `POST /api/v1/schedules/ingestion/jobs`: create one job.
- `PUT /api/v1/schedules/ingestion/jobs/{job_id}`: update one job.
- `POST /api/v1/schedules/ingestion/jobs/{job_id}/runs`: enqueue a manual run.
- `GET /api/v1/schedules/ingestion/config`: scheduler health and last results.

Use `desired_jobs()` in the setup script for the exact creation payloads;
include each dictionary's job ID as `job_id`. Creation/update API handlers
commit job configuration and reschedule it. A manual run returns a task UUID,
which must be followed to a completed store stage before claiming an import.

The independent `src.control_plane.scheduler` process owns automation loops.
The dashboard API does not start that scheduler. Its loop reloads persistent
jobs every tick, so new enabled jobs are discovered without reseeding. The
scheduler also requires healthy control workers and its singleton lease;
creating rows alone does not prove ongoing collection. Check scheduler runtime,
`next_run_at`, and a completed scheduler-created task before reporting it live.

On 2026-11-16, all five new MY/NL jobs completed a real scheduler-created
collection, organization and database-storage run. The first simultaneous
commissioning attempt hit the country-level `already_running` guard for three
jobs; those skipped jobs were made due in separate batches after the preceding
country task completed. At initial commissioning these five jobs still used
daily times; that historical evidence predates the later change to 30-minute
intervals. The later user-authorized cadence change covers all eleven jobs. Commissioning
evidence is recorded in
[`my_nl_commissioning_20261116.json`](../../reports/onboarding/my_nl_commissioning_20261116.json)
and the independent
[`my_nl_current_runtime_20261116.json`](../../reports/onboarding/my_nl_current_runtime_20261116.json).

## Revision and coverage behavior

MY refreshes the full official annual STD release, selecting its explicit
Malaysia national rows. All published categories and new source categories
enter registry discovery; this is not coverage of every Malaysian notifiable
disease. Null counts remain unknown, explicit zeroes remain observations,
and source categories keep their original codes.

The independent iDengue job captures explicit daily national and source-region
counts from the published page. It cannot reconstruct missing days from
ambiguous cumulative headings. WHO FluNet supplies separate laboratory weekly
observations with reporting-origin and denominator identities; WHO GHO supplies
annual reported indicators. Neither replaces the STD contract or proves full
current coverage of all Malaysian notifiable diseases.

RIVM's virological and respiratory weekly jobs have independent source IDs.
The laboratory rolling window is refreshed in full while older stored history
is retained. Respiratory resources keep separate source metrics, denominators,
season/week identities and definition changes. Files without verified calendar
or season context remain captured evidence rather than invented dated facts.

NL refreshes its complete annual table, preserving revisions and additions of
closed calendar years. If RIVM advances the displayed historical window,
available years are still imported and existing older database history remains
intact. Unreviewed category, reporting-definition or footnote changes fail
closed for review. Caribbean Netherlands and outbreak notifications retain
their own geography/measure identities; the ECDC baseline has a separate job.

TH has separate jobs for the public DDS weekly situation tables and the
reviewed historical Report 506 publication contract. The earlier DDS check
collects weekly reports and revisions under its own source identity. Both DDS
sources use actual per-category/basis stored coverage and official calendars
to recover missing offered weeks across year boundaries. The default per-task
budget is four weeks per category across all years: latest closed week, one due
revision inside `revision_window_months` (default three months), then missing
reports. Recovery retains the oldest missing report and rotates other gaps by a
UTC slot with a day offset, so permanent old failures cannot monopolize catchup.
Additional free slots handle more revisions whose stored refresh is at least
24 hours old. An automatic failed year keeps a partial audit while valid years
can still be stored; all years failing fails the task. This
avoids full-history report downloads on every 30-minute check. Successfully
stored periods advance coverage; a failed category/week remains eligible.
New installations begin inside the revision window, and source-withdrawn
weeks remain unavailable. Explicit manual year/week selections take priority.
The Report 506 check keeps historical monthly coverage separate. All reported
disease categories and source reporting periods remain traceable. Neither job
silently converts a cumulative snapshot into weekly incident observations.

These jobs use normal observation upserts. Revised values replace matching
source facts; an entirely withdrawn source row is not currently treated as an
authoritative database deletion. The generated exports remain subject to
their existing source publication policies.

The separate ID_TH archive job checks GitHub HEAD and the recursive tree, then
reuses content-verified cached blobs and downloads only new or changed blobs.
Its explicit national weekly case/death facts retain source year/week dimensions.
Historical calendar boundaries and date basis are unverified: January 1 timestamps
are storage anchors, never true weekly dates. This source remains internal and
requires an explicit period migration before use in a weekly time plot.
