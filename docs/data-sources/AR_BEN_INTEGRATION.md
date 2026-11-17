# Argentina national epidemiological bulletin archive

Alternative official machine-readable sources were separately investigated on
2026-11-16. The [alternative-source review](AR_ALTERNATIVE_SOURCES_20261116.md)
verifies direct national HIV annual-notification values for 2014–2023 and
downloadable dengue/respiratory records with explicit caveats. The independent
[Ministry HIV annual source](AR_HIV_ANNUAL_INTEGRATION.md) is now ingested and
locally accepted for public export; dengue/respiratory remain candidates.
The BEN cumulative source described here retains its own publication hold.

Verified against the official website on 2026-11-15. This is a source-native,
partially normalized historical import, not a claim that every number in every
bulletin is a comparable national infection count.

- Official hub: <https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional>.
- Scope: `ar_ben_weekly`; source ID: `SRC_AR_BEN`.
- Persisted label: `Argentina National Epidemiological Bulletin (BEN)`.
- Crawler: `src/data/crawlers/ar.py`, `ArgentinaBENCrawler`.
- Updater: `src/data/processors/ar.py`, `ArgentinaBENUpdater`.
- Durable collector/offline organizer: `src/services/ingestion/countries/ar.py`.
- Default historical request starts in 2009; actual available coverage is
  discovered from the official archives, never inferred from this default.

## Actual archive and coverage

The current official hub exposes 2023, 2024, 2025 and 2026 archives. All **199
linked PDFs** were downloaded and checksum-archived (about 857 MB). Links with
the literal CMS prefix `blank:#` are normalized while their original hrefs and
discovery index URLs are preserved. The index's label and upload directory do
not override the report's own year/week identity.

The older official `/salud/epidemiologia/boletines` endpoint redirects to
`bancos.salud.gob.ar/bancos/materiales-para-equipos-de-salud/soporte/boletines-epidemiologicos`.
That host failed TLS negotiation in this environment. This is an explicit
historical-archive coverage gap, not proof that older reports do not exist.
No credentials, private exports or access-control bypass are used.

The verified initial export contains **7,326 observations** from 83 reports: 7,198
explicit values and 128 source-missing cells. There are 286 source identities
that distinguish classifications and 165 raw event labels; these are not 286
canonical infectious diseases. Observation dates span 2019-01-26 through
2026-10-31. Measures comprise 6,146 cumulative notification snapshots, 707
clinical snapshots, 239 laboratory-study counts and 234 positive-test counts.
Thirteen rejected cells/merged rows remain listed in the audit. These totals
are a reproducible initial export, not a claim that every report is fully parsed.

Normalized observations include the explicitly identified national ENO tables
in the archived reports. Some reports contain retrospective 2019–2022 columns,
which remain attached to their original epidemiological year and cutoff week.
A bulletin's publication year is not the year of every fact inside it.

The current machine-readable audit is `data/current/ar/source_audit.json` and
contains the definitive report, row, category and date counts. It enumerates
unsupported table pages, rejected cells and inaccessible archive indexes.
Unparsed reports stay archived; they do not become zero-case observations.
The first implemented contracts cover four later ENO table families and the
older multi-year Notif./Confi. grid. Disease-specific seasonal tables,
provincial tables, international updates, narrative counts and chart-only
series remain in the original PDFs until their separate contracts are verified.

## Observation contracts

All published source event categories within the validated ENO tables are
retained, including uncommon events, animal rabies, poisoning and injury events.
No category is automatically mapped to a canonical human infectious disease.
Category labels, classification, definition version and raw source hash are
retained for Mapping Registry v3 review.

| Source table | Classification | Measure | Reporting basis |
| --- | --- | --- | --- |
| Nominal confirmed events | `confirmed` | `cumulative_case_notifications` | `notification` |
| Nominal notified/confirmed events | `notified`, `confirmed` separately | `cumulative_case_notifications` | `notification` |
| Grouped clinical events | `clinical_grouped` | `cumulative_clinical_notifications` | `clinical_surveillance` |
| Grouped laboratory events | `laboratory_studied`, `laboratory_positive` separately | `cumulative_laboratory_tests`, `cumulative_laboratory_positives` | `laboratory_surveillance` |
| Historical Notif./Confi. grid | `notified`, `confirmed` separately | `cumulative_case_notifications` | `notification`; separate historical definition version |

These are cumulative snapshots. `Frequency=weekly` describes the snapshot
cadence, not weekly incident counts. No differencing or summing of cumulative
snapshots is performed. The observation date is the Saturday ending the
explicit source cutoff week; a table's own cutoff takes precedence over the
bulletin's issue week. Source epi year/week, cumulative period start/end and
Sunday-to-Saturday calendar remain in `Dimensions`. The reviewed calendar's
first epidemiological week contains January 4. Cross-year dates retain the
source's epi year rather than being forced into the calendar date's year.

Historical medians, absolute differences, percentages and trends are excluded
from count observations. Only reviewed cumulative columns are admitted.
Explicit zeroes are retained. Blank/dash/asterisk source count cells are unknown,
never manufactured zeroes. The older PDF grid is read by text geometry so empty
confirmed cells do not shift the subsequent year's values. Older labels may be
clipped or abbreviated in the original report; they remain source-native and
unmapped rather than being expanded through guesswork.

Identical source/category/definition/period facts from later issues revise
older snapshots without adding the reports together. Conflicting values in the
same issue fail visibly. Missing reports or rejected table rows do not withdraw
previously stored data; no authoritative full-window deletion is claimed.

## Capture, replay and operational use

Download and organize every currently linked report:

```bash
venv/bin/python scripts/imports/collect_ar_ben.py
```

An offline reorganization verifies cached bytes and does not access upstream:

```bash
venv/bin/python scripts/imports/collect_ar_ben.py --offline-organize
```

Outputs:

- `data/current/ar/archive/`: original PDF/index bytes with URL/hash receipts.
- `data/current/ar/argentina_ben_weekly.csv`: validated source-native facts.
- `data/current/ar/source_audit.json`: actual coverage and failures.
- `data/raw/ar/ben_full_history/artifacts_manifest.json`: checksum manifest
  compatible with the shared staged archive importer. The original bytes are
  hard-linked into the country raw directory before database registration.

Normal online collection re-downloads cached PDFs after seven days, so edits
under unchanged official URLs can be discovered. `--force` re-downloads sooner.
Offline reorganization verifies existing archives without renewing their
retrieval dates or claiming a live source refresh.

Normal country collection captures every PDF and the source discovery plan
through `ArtifactWriter` before parsing. Organization reads only verified
artifacts and performs no source access or database writes. Raw persistence and
hash failures propagate; they are never recorded merely as upstream gaps.

The shared archive importer can register the captured originals and import the
organized observations through the normal ingestion runner. Enqueue success,
CSV export and a manifest alone do not prove database import. The separate
`database_import_receipt.json`, task/run state and stored observation counts
provide that evidence after the operator runs it.

Public release for the BEN source remains disabled. In the 2026-11-16 mapping review, the immediate
statistical blocker is that these ENO observations are cumulative snapshots,
not single-week incident counts. Concept mapping retains `no_projection`.
Any weekly derivation requires a separately reviewed contract for compatible
definitions, cutoff periods, missing weeks, year resets and retrospective
revisions; weekly publication frequency alone does not supply that contract.

The [government terms](https://www.argentina.gob.ar/terminos-y-condiciones)
establish default CC BY 4.0 for government-owned content, subject to declared
and third-party exceptions. Applicability to the actual tables still needs
recorded provenance; this does not authorize every third-party item in a PDF.
Rights evidence and a generated snapshot must also pass acceptance. See the
[reuse and publication guide](SOURCE_REUSE_PUBLICATION_REQUIREMENTS.md)
for the permission checklist, cumulative example and unblock conditions.

## Verification

```bash
venv/bin/python -m pytest tests/unit/test_argentina_ben.py -q
```

Real official page extracts cover all four later ENO families, the older
multi-year grid, explicit zeroes, missing confirmed cells, cumulative and source
calendar semantics, merged PDF rows, and laboratory/clinical separation.
Transport tests check persistence failures and source-only offline replay.
