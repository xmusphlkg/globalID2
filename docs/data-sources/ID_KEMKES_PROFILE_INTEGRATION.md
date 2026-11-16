# Indonesia Ministry of Health annual Health Profile

The persistent channel is the Ministry's live [Health Profile download directory](https://kemkes.go.id/id/category-download/profil-kesehatan), not the similarly named `/id/category/` path. The connector discovers the actual annual pages and their published download links on every collection, then applies `start_year` and `end_year`. There is no fixed upper year in the adapter. On 2026-11-16 this directory published nine books, 2017–2025; all nine complete PDF files were captured.

The integration contract is country `ID`, scope `id_kemkes_profile`, source ID `SRC_ID_KEMKES_PROFILE`, updater `IndonesiaKemkesProfileUpdater`, and national geography `country:ID:national`. The archive start year is 2017. Facts remain source-native holding observations pending mapping review. Public release is disabled and source reuse terms remain unverified.

## Source transport and continued updates

Annual pages are `https://kemkes.go.id/id/profil-kesehatan-indonesia-YYYY`. The 2017–2023 pages link Ministry-hosted PDFs. The 2024 page links a public Google Drive file; its file ID is obtained from the actual annual-page link. The 2025 page links `https://s.kemkes.go.id/profil2025`. Collection captures the official public shortlink resolver, linked public drive view and its public file metadata before constructing the same filename-based download URL used by the official Download button. Private accounts, login cookies and credentials are not required or stored.

The 2025 Download button initially returned 404 during source research. The later live collection succeeded and archived a complete 11,348,932-byte PDF. A source failure is therefore a retriable channel condition, not a hardcoded exclusion of that year. The main collection's 2021 PDF request timed out; a separate live retry succeeded and was explicitly merged into a new immutable collection plan. Both original attempts and the SHA-linked successful retry remain recorded.

Each collection performs live requests. It records retrieval time, resolved URL, ETag and Last-Modified where supplied, and persists source bytes before organization. A failed live request is never silently served from an old cached book. An explicit offline replay reuses verified raw artifacts, while the derived parse cache is bound to the PDF hash, official reference and parser hash frozen at module import.

## Observation contract and numerical coverage

Complete raw books do not imply every printed number has been converted into observations. The admitted numerical subset is explicitly partial. Each observation preserves the source PDF URL/hash, physical page, source report year, statistic year, source count label, table header contract, column, national row and extraction evidence. Historical years in table headers retain their own statistic year; report year remains separate.

Only explicit national prose and an exact `Indonesia` table row can become national facts. Source-native identities distinguish table columns, demographic subsets and prose from tables; they must not be summed as disjoint disease totals. For example, the 2022 book prints tuberculosis 677,464 in national prose and 724,309 in the appendix. These source values remain separately traceable and unreconciled; neither silently replaces the other. Counts, deaths, suspected cases, laboratory-confirmed cases, tests, treated patients, registered stocks, newly registered chronic cases and patient transfers remain distinct. Annual calendar periods use January 1–December 31 only when verified in the source. Registered stocks have no annual start date. Explicit zero is retained; source missing symbols and unreadable/overlapping cells never become zero.

Several official PDFs contain hidden text belonging to adjacent pages outside the page boundary. PDFium extraction is bounded to the physical page and pdfplumber tables are cropped to the physical page box. A merged national/province row is accepted only when every populated cell has exactly the same source line alignment as the row labels. No OCR digit repair or provincial summation is used.

The parser rejects rates, population denominators, planning targets, monthly columns disguised as annual counts, treatment cohorts, non-polio AFP, outbreak-frequency tables, risk-factor tables and schemas whose classification has not been verified. COVID cumulative and service-capacity tables, pneumonia tables with interleaved denominators/death headers, and unverified K/M abbreviations are retained in raw books and audited, not guessed into annual case observations. The table-of-contents line `LAMPIRAN` is not treated as the start of the appendices.

The 2024 national source evidence includes dengue 257,271 cases and 1,461 deaths; HIV 63,707; suspected measles 25,639 and laboratory-confirmed measles 3,309; neonatal tetanus 43 cases. Filariasis distinguishes 5,601 registered chronic patients, 66 newly registered cases, 47 transfers and 519 deaths. These are independent source-native measures.

## Capture, offline replay and import

```bash
PYTHONPATH=. venv/bin/python scripts/imports/id_kemkes_history.py
PYTHONPATH=. venv/bin/python scripts/imports/id_kemkes_history.py --start-year 2024
PYTHONPATH=. venv/bin/python scripts/imports/id_kemkes_history.py --replay-manifest data/raw/id/kemkes-history/20261116-complete/manifest.json
```

Outputs are `data/current/id/indonesia_kemkes_profile.csv` and `data/current/id/source_audit.json`. The immutable raw archive is `data/raw/id/kemkes-history/20261116-complete/manifest.json`, referencing the original complete live capture and explicit 2021 retry. Organization validates the captured directory, annual-page download links, official resolution chain and exact PDF hashes offline.

A new explicit retry can fill an unavailable captured year without altering the original raw evidence:

```bash
PYTHONPATH=. venv/bin/python scripts/imports/id_kemkes_history.py --start-year 2021 --end-year 2021 --capture-only --raw-directory data/raw/id/kemkes-history/retry-2021
PYTHONPATH=. venv/bin/python scripts/imports/id_kemkes_history.py --replay-manifest data/raw/id/kemkes-history/20261116-live/manifest.json --retry-manifest data/raw/id/kemkes-history/retry-2021/manifest.json --raw-directory data/raw/id/kemkes-history/merged-retry
```

The retry must match a previously discovered missing report, and its original directory and annual-page evidence are validated before merging. It cannot implicitly replace a successfully captured edition. The organization audit separates unresolved failures from source errors subsequently resolved by a SHA-linked live retry.

Database import is handled by the shared `scripts/imports/import_surveillance_archive.py --country ID --manifest ... --apply` staged ingestion path. This source script never writes the case database or publishes a website. Raw capture, numerical organization, database import and public publication are separate states; use the source audit and database import receipt for actual progress.

## Regression evidence

`tests/unit/test_id_kemkes_profile.py` uses exact selected physical pages from the 2017, 2018, 2020, 2022 and 2024 official books, actual directory/annual HTML, and actual 2025 public resolver metadata. Sidecar provenance files record original file hashes and fixture-to-source physical-page mappings. Regressions cover dynamic future-year discovery, real values, source year dimensions, suspected/confirmed counts, stock/flow, laboratory tests, monthly/cohort/denominator exclusions, hidden off-page content, national row alignment, zero/missing, source plan tampering, offline replay and raw persistence failures.
