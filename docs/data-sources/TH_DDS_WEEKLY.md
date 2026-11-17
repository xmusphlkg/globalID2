# Thailand DDS public national weekly situation reports

Source scope: `th_dds_weekly`; ontology source: `SRC_TH_DDS_WEEKLY`.
Public entry: https://ddsdoe.ddc.moph.go.th/dds-portal/ ; public report form:
https://doe.moph.go.th/dds-report/situation/create . Reviewed 2026-11-15.

The public **situation** report supports the complete current form inventory,
unlike the priority report's selected diseases. Enumerating all 11 real
`f_dz_gr` groups yields **62 distinct source disease codes**. The `all` option
returns an empty placeholder; it must never be interpreted as a complete
inventory. Empty groups remain captured as inventory evidence.

This current inventory differs from the historical Report 506 inventory of
72 categories. In particular DDS codes 12/13 describe acute hepatitis B/C;
historical definitions must not be reused merely because the codes match.
Diarrhea, COVID-19, congenital rubella, Legionella and RSV are represented by
their DDS source categories. This connector does not claim all old categories
are still reported by DDS.

## Collection and interpretation

The collector uses the normal public CSRF session and form submission.
It archives sanitized public forms, group/year/interval selectors, each PDF,
and a collection plan before interpretation. Session cookies and CSRF values
are never archived. Artifact SHA-256 and size are verified on offline replay.

Only explicitly offered `N-N` intervals whose official end date has ended in
Bangkok are accepted. Automatic tasks read actual stored coverage separately
for each disease code and onset/report basis. Each task selects at most four
weeks per category across all source years: the latest published closed week,
one due revision in `revision_window_months` (default three months), then
missing offered reports. Recovery retains the oldest missing report and rotates
other missing reports by a UTC clock slot with a day offset. Permanently failing
old reports therefore cannot starve later gaps or due revisions. Free remaining
slots refresh additional stale revisions. Older revision weeks rotate
by stored update time with a 24-hour freshness interval; latest weeks are
checked on each scheduled task. Missing weeks are retried until stored, even
when the outage exceeds the revision window. Partial disease coverage cannot
advance a global MAX-date watermark past another category's missing report.

The recovery anchor is the earlier of the first stored source period and the
revision-window start. A new installation starts inside that bounded window;
full historical onboarding remains separate. Required official calendars and
explicit selectors are captured per year, including unfinished previous-year
weeks after New Year. Sparse annual collection plans replay into a single
store transaction. An automatic year with no valid reports retains its partial
failure audit without preventing valid years from being stored; all years
failing still fails the task. Explicit manual `year` or `weeks` bypasses this automatic
plan; a manual year without weeks keeps the latest offered closed week.

Cumulative `1-N` and multi-week windows are excluded. Older years can expose
only annual cumulative options: this does not authorize inventing weekly
requests, differencing cumulative values or claiming a weekly backfill.
Polling cannot reconstruct weeks that the source no longer offers. Existing
observations remain intact, and a missing official calendar fails closed.

The collector discovers the requested year's calendar link on the official
DDS portal and captures both original RAR and extracted XLSX. It requires the
`unrar-free` system package. The organizer selects the explicitly titled annual
worksheet and validates complete contiguous coverage of that year; stale helper
worksheets cannot certify a different year. A missing calendar, missing
decompressor or unreadable source calendar stops collection with a durable
`calendar_failure_audit`; no ISO calendar or guessed year boundary is used.

The official 2026 calendar has short edge weeks: week 1 is 1–3 January and
week 53 is 27–31 December. Week 46 covers Sunday 8 November through Saturday
14 November. Positive PDFs must explicitly match the requested source week,
Buddhist year, Gregorian boundaries, national geography and date/place basis.
Source epidemiological week is retained as `EpiWeek`, not labelled MMWR.
`onset` and `gen` have separate definition versions; their facts must never
overwrite one another. A calendar mismatch fails closed rather than shifting
the source dates silently.

National cases and deaths come from the first-page national summary for the
explicit single-week request. Its Thai word “สะสม” alone is not evidence of
an annual total: the captured offered interval and matching PDF heading prove
the reporting window. Provinces, age graphs and rolling charts are not summed.
Explicit no-case narratives yield cases zero; absent death metrics and absent
data-as-of timestamps remain unknown. Retrieval time is retained separately.

The source's code 20 no-case PDF says “รหัส 20” instead of the form's polio
label. The parser accepts this exact explicit code only, records both original
labels and `ReportIdentityEvidence=explicit_source_code`, and rejects a
different code or geography.

## Storage, automation and review

`ThailandDDSWeeklyUpdater` retains source-native observations in Mapping
Registry v3 holding series, with publication disabled and mapping notification
emails disabled. No concept is guessed from a translated disease name.
Ordinary source revisions may update the same source definition/week;
collection failures never authorize source-wide replacement or deletion.

The organizer reports category coverage and every failed or unrepresented
category. A partial collection is visibly partial, never “complete”. The
manual replay script additionally requires a fully validated captured
inventory before creating a foreground `RUNNING` task, then uses the common
collect → organize → store runner and completes or fails that same task.
It invokes no task lifecycle email hooks.

```bash
PYTHONPATH=. venv/bin/pytest -q tests/unit/test_th_dds.py
venv/bin/python scripts/import_th_dds_weekly.py \
  --manifest data/raw/th_dds/manual_20261115/manifest.json \
  --result-json data/current/th/dds_import_result.json
```

The initial live collection contains 62 validated observations for week 46,
including 19 official explicit no-case reports. Raw bytes and replay manifests
remain under `data/raw/`; inspection CSV/audit under `data/current/th/`.
Public release still requires independent mapping and publication review.

Implementation: `src/data/crawlers/th_dds.py`,
`src/services/ingestion/countries/th_dds.py`,
`src/data/processors/th_dds.py`.
Real official positive/zero/code-only PDFs and public selector fixtures live
under `tests/fixtures/ingestion/TH/dds/`.
