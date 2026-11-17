# Colombia INS BES integration

The private `co_ins_bes_weekly` / `SRC_CO_INS_BES` channel uses the official INS
weekly bulletin archive. The existing SODA scopes retain their separate
2007–2024 observations. Status: `automated_managed_runtime`.

## Originals and network

The production Colombian WireGuard connection runs in the isolated
`gids-co-ins` network namespace. Only official INS HTTPS requests use the
INS-only proxy `http://169.254.231.2:18088`; other application traffic retains
the host route. The egress and proxy systemd services are enabled. See
[transport configuration](CO_INS_TRANSPORT.md) for operations and DNS details.
The WireGuard configuration is a local secret and never enters model requests.

All 97 published 2025–2026 PDFs were captured on 2026-11-16, including
2025 weeks 1–53 and 2026 weeks 1–44. The frozen manifest is
`data/raw/co/bes-history/20261116-production-backfill-v4/manifest.json`.
It contains the complete original bytes, SHA-256, size, actual official URL and
SharePoint listing evidence. Later 2026 weeks are not present in that listing.

## Numerical scope

The fixed territorial appendix reports **source-year cumulative counts at each
epidemiological week**, not weekly new cases. Only the explicit `Acumulado
<source year>` column is imported. Expected/observed comparison columns and
narrative tables have different definitions and are excluded from this scope.
The v5 deterministic parser also retains identifiable comparison cells as raw
evidence in `SourceComparisonCells`; they never supply the observation value.
`weekly_incidence_coverage_complete` is explicitly false even when the cumulative
annex is fully extracted. See the [multi-source design](CO_MULTI_SOURCE_DESIGN.md)
for the 2025 microdata aggregates and the remaining 2026 weekly-incidence gap.
The source epidemiological year/week remain explicit dimensions; 2025 week 53
ends on 2026-01-03 and belongs to source year 2025. Explicit blank cells remain
missing. Ambiguous repeated source event headings retain distinct group codes
and require separate definition review before standard disease mapping.

## Existing model center

`src/services/ingestion/co_bes_ai.py` reuses `ModelCenterCompleter`, as does the
existing provincial image-report workflow. It submits original PDF pages through
the durable model queue under a persisted parent task, with `native_file`
capability and distinct-model routing. The model center owns selection,
concurrency, leases and transport retries. No additional provider API or keys
are introduced.

Stable tables use the deterministic parser. Anomalous pages use independent
transcriptions of the native PDF through the existing model center. Both
readings are cached, while the original PDFs remain frozen under `data/raw/co/`
for audit and replay. Counts, ordered territories, event headings and
definitions must agree before canonical normalization accepts them.
A text fallback, wrong cumulative year or incomplete territory array cannot
create validated facts. Malformed counts printed in the original remain null
with `source_unreadable`, the exact printed string and an unresolved quality flag;
they are never repaired by guessing digits. Raw replies and actual model provenance
remain under `data/cache/co/ins_bes_ai/responses/`; successful page and report
results are cached by original PDF hash and prompt version. Disagreements
resolved by reading the original are retained with the source image hash, both
model replies and the exact reviewed correction. Partial-page repairs
must merge with the remaining original report pages rather than claim complete
report coverage.

Plan files under `data/current/co/` distinguish original appendix candidates
from the smaller anomaly queue. A planning hint is never itself a verified
report identity or numerical coverage claim.

## Historical backfill

Replay the frozen bytes through the ordinary ingestion pipeline:

```bash
venv/bin/python scripts/imports/co_bes_history.py \
  --start-year 2025 --end-year 2026 --parse-workers 24 \
  --replay-manifest data/raw/co/bes-history/20261116-production-backfill-v4/manifest.json \
  --apply
```

The source CSV, import receipt and state are under `data/current/co/`. The
2026-11-16 replay imported 159,393 records from all 97 reports (2025 weeks
1–53 and 2026 weeks 1–44) into the private source holding area. The import
receipt records task `b87679f2-bf8c-4053-b3ec-59c44e1f6bea`. The source CSV
contains 159,377 numeric values and 16 retained nulls: eight original blank
cells (`source_missing`) and eight malformed printed counts
(`source_unreadable`). Original blanks are faithfully represented missing values;
the malformed counts remain unresolved. This backfill is scoped to the fixed
cumulative annex and does not assert weekly incidence.

The final source-to-database row reconciliation in
`data/current/co/bes_database_acceptance.json` found 159,393 matching rows
across all 97 PDFs, with zero missing, unexpected, duplicate or mismatched
database rows. `facts_match` is true. Its `complete` and `coverage_verified`
flags remain false because eight malformed source cells are unreadable;
this is not a claim of complete numerical coverage. The accepted public release
does not include BES. Public release admission remains pending.

The backfill uses only the fixed appendix, including source typography and
source definitions. Eight malformed national totals were confirmed in the
original pages. `source_cell_retained` prevents repeated model calls for those
known source errors while preserving their quality flags. PDFium cover reading
uses page 1 for the date/week and page 2 only for actual publisher/title text,
which avoids confusing the second page number with epidemiological week 2.

## Managed unattended refresh

`scripts/refresh_co_bes.py --apply` is the dedicated private-source refresh
runner. `scripts/install_co_bes_automation.py --apply` prepares a fixed snapshot
of `src/`, `configs/` and `scripts/` at `data/runtime/co-bes/current` and installs
the three `configs/systemd/globalid-co-bes-*` units. This independent runtime
does not change the accepted website release. The model-center dispatcher runs
in standalone owner-fenced mode, without a general task-worker readiness
dependency. The existing 11:45 America/Bogota `AutomationJob` remains disabled
to prevent duplicate runs; the separate systemd timer is the active schedule.
The control-panel job notes identify the standalone timer as active.

```bash
venv/bin/python scripts/install_co_bes_automation.py --apply
```

The timer checks hourly at :45 America/Bogota and uses `Persistent=true` to
catch up after downtime. The refresh service retries failures after 15 minutes,
with at most four starts in two hours; each runner start makes at most three
bounded attempts. Discovery normally covers the latest two source years and
also revisits pending older reports. It imports only new URLs or revised PDF
hashes. The PDF cache rechecks originals after 24 hours so a revised bulletin
can be detected. A damaged cached PDF or receipt is quarantined and downloaded
again through the official INS transport. Original blank cells remain valid
missing values and do not block the refresh. Anomalous pages require two
independent native-PDF readings;
disagreements allow up to two fresh reread rounds, with rejected replies kept
separately so their cache cannot trap later attempts. Failed reports stay
pending for retry while other reports can progress. Each imported delta is
reconciled against the database before its report hashes are marked applied.
Progress, pending reports and the last import Task UUID are recorded in
`data/current/co/bes-refresh-state.json`; the standard import Task is visible
in Colombia's task history. Public release remains off.

After installation, operate the dedicated units with:

```bash
sudo systemctl status globalid-co-bes-model-dispatch.service \
  globalid-co-bes-refresh.timer globalid-co-bes-refresh.service
sudo systemctl stop globalid-co-bes-refresh.timer globalid-co-bes-refresh.service
sudo systemctl enable --now globalid-co-bes-refresh.timer
sudo systemctl start globalid-co-bes-refresh.service
journalctl -u globalid-co-bes-refresh.service -u globalid-co-bes-model-dispatch.service
```

The `stop` command pauses checks and an active refresh; `enable --now` resumes
the timer, and `start` triggers one manual refresh. The pinned runtime and
managed model dispatcher are installed and active; the hourly timer is enabled.
The first official check found all 97 known reports and no changed PDF hashes,
so it made no duplicate import. A subsequent managed, idempotent replay of
2026 week 44 imported 2,067 source rows under Task
`2d5bb599-1c5d-4399-b353-068108dbeb8d`. Its delta reconciliation matched
all 2,067 rows, with zero missing, unexpected, duplicate or mismatched rows.
The refresh state is `up_to_date`, with 97 applied reports and no pending
reports. This verifies the managed import path; BES remains outside the accepted
public release.

## 2026-11-17 typed refresh addendum

The typed replay supersedes the earlier cumulative-only count in this document.
It retains **159,393** cumulative source facts and adds **630** rows from the
reviewed body table whose text explicitly defines notifications in the analysed
week (628 numeric values and 2 source-missing cells). Those rows use
`co_bes_explicit_weekly_notifications_v1`, `interval_count`, and the INS week
boundaries; they are not derived by differencing `Acumulado`.

The active mapping release contains 102 reviewed CO categories. WHO FluNet is
stored in its separate `SRC_CO_WHO_FLUNET` channel (4,850 2025–2026 cells), and
the annual microdata channel remains private. The full typed verification report
is `data/current/co/bes-typed-verification-postrefresh.json`; its database facts
match is true, while completeness remains false for eight unreadable source
cells and the intentionally unsupported narrative/chart tables.

The three managed timers are enabled: BES hourly at minute 45, FluNet daily at
08:30, and completed-year microdata Sundays at 07:15, all in
`America/Bogota`. The control-panel duplicate jobs remain disabled. The current
refresh state can show a transient retry after a transport check; it does not
invalidate the already committed replay or its reconciliation.
