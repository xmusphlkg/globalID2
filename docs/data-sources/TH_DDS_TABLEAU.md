# Thailand official DDS Tableau national weekly source

Reviewed 2026-11-15. The current official anonymous national dashboard is
<https://dvis3.ddc.moph.go.th/t/DDC_CENTER_DOE/views/DDS2/1_2>.
Its source scope is `th_dds_tableau`, source identity is `SRC_TH_DDS_TABLEAU`,
and updater is `ThailandDDSTableauUpdater`. It stays separate from Report 506
monthly history, DDS situation PDFs and the historical ID_TH repository.
Public release and task/disease mapping notifications remain disabled.

## Official discovery and access

The prior ID_TH script uses `DDS2/sheet127`. That view now returns only a generic
HTTP 200 HTML shell: its real bootstrap returns HTTP 404, and its `.csv`/`.png`
exports return `type="missing-view"`. It is not a functioning update source.

The official <https://ddsdoe.ddc.moph.go.th/dds-portal/> app 13 currently links to
`DDS2/sheet33`. This is a navigation dashboard. Its publicly returned
`visible_sheets` and `repository_urls` identify `DDS2/1_2` as the current
national situation dashboard. A fresh isolated browser and normal requests
session both successfully initialize it anonymously. The source configuration
explicitly says `is_guest=true`, `allow_summary=true`,
`allow_view_underlying=false`. The adapter uses the public summary models and
filter controls only, and never calls underlying-data or login APIs.

The server uses a newer startSession protocol, while TableauScraper 0.1.29
expects the older embedded `tsConfigContainer`. The connector explicitly uses
the browser's normal anonymous `startSession/viewing` then `bootstrapSession`,
and delegates summary dictionary decoding to the pinned library. Requests have
finite connection/read timeouts. No login, trusted ticket or private export is
used.

## Categories, geography and date/place basis

The live year parameter offers Buddhist years 2569–2564 (2026–2021). The default
year is the current Bangkok calendar year, which must be an actual offered
parameter value. There are **64 actual disease-selector categories** plus one
UI instruction sentinel, which is excluded. Every actual category is collected
by default, including explicit zero categories. Reordered labels are accepted;
new nonempty unique native selector labels are retained with `new_labels` audit
evidence, and removed reviewed labels appear under `missing_reviewed_labels`.
No English translation is used to invent a canonical disease mapping.

The live inventory includes `Fever of unknown origin (FUO)` and
`Viral conjunctivitis`, which are absent from the current 62-code public DDS
situation form. The two sources also have different label/grouping evidence:
`Coronavirus disease 2019 (COVID-19),MISC from COVID-19` and
`Syphilis, Congenital syphilis ` explicitly combine source categories. Keep
these native labels intact; do not split their totals or assume 64 independent
clinical diseases. Tableau does not expose numeric DDS disease codes here.
`SourceDiseaseCode` is explicitly a SHA256-derived technical label identity,
not an inferred official code.

The global date/place selector has two distinct values:

- `report`: `ตามวันและสถานที่รายงาน` (report date and place), the adapter default.
- `onset`: `ตามวันและสถานที่เริ่มป่วย` (onset date and place), the UI default.

These bases remain distinct in `TimeBasis`, `DefinitionVersion` and observation
dimensions. The adapter explicitly selects its requested basis and verifies
the source's selected tuples and displayed basis, year, disease and single
week. It also requires unrestricted province, health region, district, age,
disease group, nationality and care setting filters. The UI's default empty
disease selection produces zero cards and is never accepted as a disease row.

National observations come from the explicit `ผู้ป่วย` and `ผู้เสียชีวิต`
national cards. The province table is used only to check consistency, never
to substitute a calculated national observation. This table may contain
**77 provinces plus `ไม่ระบุ` (unknown location)**, or a sparse subset for a
particular category. Missing province rows are not synthesized as zero. Unknown locations remain in
the published national count. The reviewed 2026 report-basis influenza example
has 663,944 cases and 136 deaths for all selected weeks, including 14 cases
with unknown location. Its explicitly selected **week 46** has **32,529 cases,
4 deaths**, including 4 cases with unknown location.

The source's basis display worksheet is absent for some zero and nonzero
categories. This absence does not establish a basis. Every observation instead
requires the selected source tuples to connect to both national cards through
the exact same global field identity, `modified=true`, and explicit
`targetSheets` links for disease, week and date/place basis. A nonempty basis
display must agree as an additional check. Province sums must reconcile to
the explicit national card; blank province counts under an explicit zero
national card stay unknown and are not converted into zero. Unknown-location
fields remain blank when the source does not supply that subcount.

## Closed single-week contract

The connector reuses the official annually titled DDS epidemiological calendar,
discovered from the government portal and captured as raw RAR plus extracted
XLSX with parent hash provenance. It does not substitute ISO week dates. The
first and last source weeks can be shorter than seven days. A missing official
calendar for a new year fails the collection without inferring dates.

Automatic tasks use a bounded recovery/revision plan across the entire live
disease inventory. Actual stored observations are projected by source code,
source date/place basis, week end and update time. Each category receives at
most four source weeks per task across all years: the latest closed week,
one due revision inside `revision_window_months` (default three months), then
uncaptured offered weeks. Recovery retains the oldest missing report and rotates
other missing reports by a UTC clock slot with a day offset; permanently failing
old weeks cannot monopolize the recovery slots. Additional free slots refresh
the least recently refreshed weeks in the revision window (at most once per
24 hours for these older revisions). A category's missing week cannot be hidden
by another category's later successful observation. Repeated tasks drain long
outages beyond the revision window, starting at the earlier of the existing
coverage anchor and the revision window; a new installation starts with that
window. Existing older facts are retained.

The plan captures each required official annual calendar and replays sparse
per-category/year bundles before one shared store transaction. An automatic
year with no valid reports retains its partial failure audit while valid reports
from other years can still be stored; all years failing still fails the task. This covers
unfinished previous-year weeks after New Year without guessing ISO dates or
re-downloading the complete report history every 30 minutes. Weeks no longer
exposed by the source remain unavailable; the planner cannot reconstruct them.

Manual `year`, `weeks`, or `recent_weeks` takes precedence and bypasses the
automatic coverage query. `recent_weeks=3` selects the latest three closed
weeks in the requested/current source year; an explicit `year` with no week
selection retains its latest-week behavior. Direct connector calls without an
automatic refresh state also retain that behavior. There is no disease offset
that repeatedly omits later categories from scheduled updates.

The source week end must be strictly before the Bangkok `as_of` date. On
2026-11-15, week 46 is 2026-11-08 through 2026-11-14. Week 47 is still open and
is excluded even though its curve already contains cases. Future weeks 48–53
have chart zero placeholders and are excluded as well. The adapter explicitly
filters a single eligible week and reads its national card; it never differences
cumulative totals. The dashboard's five historical comparison curves and its
separate current-year AGG curve field are not imported as current observations.

## Durable capture, replay and execution

`src/data/crawlers/th_tableau.py` handles anonymous summaries and strict source
contracts; `src/services/ingestion/countries/th_tableau.py` captures and replays
them; `src/data/processors/th_tableau.py` retains source-native holdings.

One raw ZIP SourceArtifact contains the public access configuration, initial
bootstrap and each exact parameter/filter response in source-session order.
Its manifest records original public view URL, byte length, SHA256 and the
requested native controls for every member. A captured plan records the live
inventory, selected year/basis/weeks, retrieval date and independent failures.
Outer and member hashes are verified before offline replay; the organizer
makes no network calls. It validates the source's actual selected tuples rather
than treating a filter's available domain as its active selection. Raw session
controls stay in the internal artifact store and are never public exports.

Disease failures remain audited independently; valid reports may be retained
in a partial run. A run with no validated national rows fails. There is no
country-wide deletion or substitution of ID_TH/Report 506 data for a live error.
Normal source-series registry and quality checks run during store.

Run a foreground refresh without notification lifecycle hooks:

```bash
venv/bin/python scripts/import_thailand_tableau.py
```

The script creates an audited task/CrawlRun and executes normal collect,
organize and store stages. Its result ledger is
`data/current/th/tableau_import_result.json`. The shared daily scheduler owns
the recurring job; this script does not install a second scheduler.
Foreground execution maintains an independent lease and 30-second heartbeat.
`--replay-task-uuid UUID` reuses verified raw artifacts from a previous official
collection in a new audited execution without network access. The original
task and its cancellation/failure history stay intact.

The first complete production import was verified on 2026-11-15:
**64 observations in 64 source series**, report basis, source week 46 ending
2026-11-14. Task `ab3f3c37-349b-4174-9b99-a3053e0d8283` and CrawlRun **29413**
are completed; collect, organize and store all completed, source audit is
`validated` with no failed reports, and the normal fail-closed quality check
reported **0 issues**. All 64 observations remain source-native holdings.
The four retained raw artifacts total 23,725,417 bytes; the 22,347,483-byte
summary ZIP contains 69 manifest members, including all 64 report responses.

The initial network collection task
`db96afef-526f-418c-b950-6bdfe95bf1a1` was cancelled by the runtime's stale-task
recovery before foreground heartbeats were added. Its successful raw collection
and original recovery history remain audited. The completed production task
replayed those exact raw artifacts offline and retained links to the original
task/artifact IDs. No additional all-category network collection was performed.

## Verification

```bash
venv/bin/python -m pytest tests/unit/test_th_tableau.py -q
```

The real single-week and sparse/zero fixtures retain the official summary
frames, with transient guest session identifiers replaced by equal-length
placeholders for tests. Tests replay it offline, preserve the 64-category
inventory and combined labels, accept reordered/new native labels, verify
explicit national totals including unknown geography, reject restricted
dimensions and incorrect controls, preserve explicit zeros, reject missing
counts/open weeks/future zeros, and detect raw member tampering. Sparse province
tables, zero cards with an absent basis display, and nonzero cards with an
absent basis display have real captured regression cases. Both cards' source
filter links remain mandatory in every case. **24 unit tests pass.**
