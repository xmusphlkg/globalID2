# Pakistan NIH IDSR weekly bulletin

Canonical scope: `pk_nih_idsr_weekly`; registry source: `SRC_PK_NIH_IDSR`.
Updater: `PakistanNIHIDSRUpdater`; staged connector:
`src.services.ingestion.countries.pk_idsr`.

## Verified official source

On 2026-11-17 both the [NIH weekly directory](https://nih.org.pk/public/phb/weekly-bulletin)
and [PHB directory](https://phb.nih.org.pk/integratedisease-surveillance-and-response)
returned HTTP 200. The NIH directory listed 2025 weeks 1–52 and 2026 weeks 1–42,
plus older archives from 2021. Its latest linked PDF returned HTTP 200 and contains
25 pages. The PHB mirror's relative latest PDF returned 404; discovery therefore
uses the NIH directory and its NIH PDF URLs. Official links occasionally contain
literal `\\n` or newlines, removed before URL preparation.

The unmodified index, latest PDF and SHA-256 provenance are checked in under
`tests/fixtures/ingestion/PK/`. No authenticated interface or private API is used.
Public reuse terms have not been verified; public release remains disabled.

A resumable archive capture on the same date downloaded all 204 linked originals
listed for 2021–2026 (four cards in 2022 have no downloadable href and are omitted
by the official page itself). The immutable archive manifest is
`data/raw/pk/nih_idsr_history_20261117/manifest.json`; all 204 PDFs passed
completeness and SHA-256 checks with zero download failures.

## Numerical contract and omissions

The implemented parser extracts Table 1, "Province/Area wise distribution of most
frequently reported suspected cases", preserving 28 source categories and eight
columns: AJK, Balochistan, GB, ICT, KP, Punjab, Sindh, Total. In 2026 week 42 this
produces 224 source observations. Examples: AD (Non-Cholera) total 98,005; Malaria
in Sindh 68,531. These are suspected notifications, not laboratory confirmations.

Punjab is `NR`. `NR` and dashes remain unknown; explicit `0` remains zero. The
published total is retained under `source:PK:nih_idsr:reporting_areas`; it is never
assigned `country:PK:national`. Province/area columns use their own source
geography keys. Region rows and totals are not added together. Every numeric
published total must reconcile to its reported numeric component cells.

Table 1 does not publish deaths, so this adapter emits no death observations.
District tables, laboratory tests/positives, sentinel charts and outbreak
narratives remain raw captured evidence and documented extraction gaps. Different
or incomplete table layouts fail closed instead of guessing column meanings.

The report and directory establish year/week but not verified calendar dates.
The adapter follows the repository's source-week holding convention: January 1
is explicitly labeled a year storage anchor, with source year/week retained in
dimensions and `WeekCalendarVerified=false`. No ISO dates are inferred. These
facts must remain unprojectable holding until source calendar and disease mapping
contracts are reviewed. Latest source week is separately available in the audit;
the storage anchor is not a claim about the latest observation date.

## Capture, replay and revisions

Collection captures the official HTML, selected original PDFs and an immutable
plan/manifest before organization. Default selection is the newest 12 reports
from 2025 onward; callers can set `start_year`, `end_year`, `weeks`, and
`max_reports` (1–260, or `None` for the full discovered window). The audit exposes
reports omitted by the limit and failed downloads, so a limited run does not
claim full archive coverage. Routine runs replay recent reports for revisions.

Organization uses only captured artifacts, verifies discovery against the raw
index and verifies the PDF manifest. It rejects unlinked artifacts. Conflicting
same-week editions quarantine conflicting cells; distinct weeks retain distinct
dimensions even when storage anchors match. No partial run authoritatively
replaces or withdraws an existing source window. Persistence failures propagate.

## Validation and remaining work

`venv/bin/python -m pytest tests/unit/test_pk_idsr.py` checks actual source values,
zeros/NR, geographic scope, week identity, invalid layouts, conflicting editions,
bounded discovery, offline replay and manifest tampering. A source parser passing
these checks does not establish a completed database import or public snapshot.

Next work: verify official week boundaries, review case definitions/mappings and
reuse terms, expand historical layouts, independently contract laboratory and
death/outbreak tables, backfill selected years, and validate database holdings
before public publication.

The full archive captured 204 reports. The current conservative parser organized
4,088 source cells; 18 reports matched the supported Table 1 layout and 186 older
reports remain explicitly unparsed because their identity/table layouts differ.
No conflicting cells were accepted. The database currently retains 3,606 numeric
rows; 482 `NR` cells remain unknown and were not inserted or converted to zero.
This is an internal backfill result, not a public release.
