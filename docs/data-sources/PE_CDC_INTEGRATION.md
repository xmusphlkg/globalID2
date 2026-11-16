# Peru CDC epidemiological bulletin archive

The official public bulletin directory supplies **1,714 directory rows and
1,661 unique linked PDFs**, starting in 1993. Discovery uses POST
`https://epipublic.dge.gob.pe/publico/listado_ajax` with `bq_anio=` and
`bq_categoria=1`. The source scope is `pe_cdc_weekly`, ontology source
`SRC_PE_CDC`, stored label `Peru CDC Epidemiological Bulletin`.

The complete directory is archived in
`data/raw/pe/cdc-history/938c8d309a6f01eb/full_listing.html`.
Every immutable batch includes original directory HTML, complete PDF bytes,
collection plan/audit, hashes and original URLs. Partial HTTP responses stay
in the resumable cache and are never registered as complete source artifacts.
Standard ingestion registers source artifacts and source-native observations;
no automatic canonical disease mapping or public release is enabled.

## Current verified import

At integration validation on 2026-11-16, **94 reports were archived and
registered in nine ordinary ingestion batches; 2,365 exact native metrics
were stored**. There are 1,567 reports still outstanding. This is verified
partial coverage of the complete archive plan, not a claim that all 1,661
reports have finished downloading or decoding.
`data/current/pe/history-state.json` and `source_audit.json` report live progress.
Each raw batch has its full numerical coverage audit and database import log.

Known real extraction fixtures cover 1993 W52 (eight national cumulative
narratives), 2013 W52 (27 native table categories, 76 numerical metrics),
and 2019 W51 (26 categories, 111 numerical metrics). The first import also
contains national historical narratives from other cached editions. Many
early scans/OCR matrices remain unsupported; their original PDFs are retained
and their unknown numerical coverage is explicitly audited.

## Periods, categories and counts

- Source weeks use the explicit bulletin Sunday–Saturday interval. For
  example, 1993 W52 is 1993-12-26 through 1994-01-01; 2010 W52 is
  2010-12-26 through 2011-01-01. ISO weeks are not substituted. Derivation
  from an explicit end date requires a physically present source calendar
  definition. The cover must independently confirm its reporting year.
- The complete directory's week field is authoritative discovery metadata.
  Nine real filename variants cannot safely be parsed for their week.
  Duplicate 1993 directory rows, W7/W17 URL aliasing and invalid publication
  dates are retained as source catalog metadata. Two title/year conflicts
  are flagged rather than silently corrected.
- Only explicit national narrative counts or a verified national comparison
  table become facts. Local counts, international news, reporting completeness,
  incidence rates and OCR O/0 substitutions never become national case counts.
- Weekly cases, cumulative cases, cumulative deaths and weekly deaths retain
  different native codes/definitions. Confirmed/probable columns stay separate.
  Maternal, fetal and neonatal death events remain death measures. Each source
  category, including noninfectious events such as snakebite, is retained
  without a forced disease mapping. Raw OCR labels remain raw labels.
- Prior-year comparison values and rates remain source evidence dimensions;
  they are not assigned unverified historical dates or converted to counts.
  Cumulative periods have an unknown accumulation start, separate from the
  source report's verified seven-day interval. Unknown is never replaced by zero.
- Same-period official variants (2021 W4 and 2024 W52 in the captured directory)
  are deferred until all linked editions meet in one organization batch.
  Contradictory facts are quarantined as a group without inventing an official
  revision order. Source URL/SHA also form document provenance dimensions.
- Modern PDFs can place many physical disease lines inside a single geometric
  table cell. These original line boundaries are preserved before parsing.
  Labels containing numerical table cells or concatenated disease rows are
  rejected and audited. The real 2026 W13 fixture contains 28 source categories
  and 113 exact metrics; maternal/perinatal death rows retain death measures
  even when the source table includes an additional numerical column.
  A different 2026 W17 neonatal-death row repeats values across unverified
  confirmed/probable columns: that whole row is quarantined. Five incorrectly
  typed current facts were removed by exact original URL/SHA, with CSV/state
  counts repaired; original task counts, raw evidence and immutable revisions
  are preserved in `w17_death_contract_correction_receipt.json`.
- Matrix facts require the matching source week, year order, accumulated/death/
  incidence headings and the complete verified classification header. All 94
  processed reports were reparsed under this contract: the 2,365 resulting
  unique identities/values exactly match current DB facts. The audit is
  `data/current/pe/parser_repair_validation.json`. Genuine named diseases such
  as historical neonatal tetanus remain cases when the source explicitly says
  `casos`; a neonatal age label alone does not make a count a death measure.

## Persistent archive and import

The controller holds an exclusive process lock, reuses complete hash-verified
cache receipts, resumes partial GETs with Range/If-Range, retries source errors
and checkpoints immutable batches. Every DB import is a separate, synchronous
subprocess using `import_surveillance_archive.py --apply`; concurrent PE batches
cannot race CSV/audit exports. Failed DB batches are replayed on restart.
The `--supervise` option retries controller/import exceptions with exponential
delays of 30, 60, 120, 240, 480 and at most 900 seconds. Successful batch progress
resets the delay. The state retains the real error, next retry time and a
30-second heartbeat during backoff. Parser cache schema v2 uses a source
fingerprint frozen when the parser module loads, so a long-running process
cannot write old parser results under a newly edited source file's hash.
Before organization/import, the controller also verifies that its loaded
parser fingerprint still matches the source file on disk. A mismatch requires
a controller restart, preventing CSV and a new DB subprocess from using
different parser versions.

```bash
venv/bin/python -u scripts/imports/pe_cdc_history.py \
  --apply --supervise --workers 8 --batch-size 12 \
  --listing-file data/raw/pe/cdc-history/938c8d309a6f01eb/full_listing.html
```

The running background process writes `data/current/pe/history-controller.log`.
Its PID and heartbeat appear in `history-state.json`; do not launch another
controller while its process lock is held. State is bound to the exact
directory SHA and requested year range. Historical source transfers can be
slow. The controller records outstanding files and ends `partial` when source
errors remain, or `complete` for full artifact capture; this does not imply
all scanned numerical tables have been decoded. Normal country ingestion is
also available through the standard PE collect/organize connector.

To inspect/import an existing captured batch offline, use:

```bash
venv/bin/python scripts/imports/import_surveillance_archive.py \
  --country PE --manifest data/raw/pe/cdc-history/938c8d309a6f01eb/batch-05db664fc0d0b4dcbe33/manifest.json
```

Add `--apply` for standard registered DB ingestion. Integrity failures prevent
import. Real fixtures and 20 source-contract tests are in
`tests/unit/test_pe_cdc.py` and `tests/fixtures/ingestion/PE/official_bulletins.zip`.
The separate `2026w13_cover_summary.pdf` preserves original pages 1 and 23;
its provenance JSON records the full source URL/SHA and original page numbers.
`2026w17_summary.pdf` preserves original page 13 and tests the quarantined
neonatal-death row against its matching source headers.
Run `PYTHONPATH=. venv/bin/python -m pytest -q tests/unit/test_pe_cdc.py`.

The initial 46 older observations preceded document provenance dimensions.
`scripts/imports/pe_document_identity_migration.py --apply` idempotently stamps
only current rows backed by exact archived URL/SHA, under the PE mutation lock,
and repairs current CSV dimensions. Its receipt records old/new identities in
`data/current/pe/document_identity_migration_receipt.json`. Numerical values
and immutable revision history are preserved.
