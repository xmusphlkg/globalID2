# Sri Lanka Epidemiology Unit WER onboarding

Canonical scope: `lk_epid_wer_weekly`. Registry source: `SRC_LK_EPID_WER`.
Updater: `SriLankaWERUpdater`. Connector: `countries.lk_wer`.

## Verified access state, 2026-11-17

The supplied official [Weekly Epidemiological Report archive](https://www.epid.gov.lk/epid/public/weekly-epidemiological-report)
returned HTTP 500 containing a `509 Bandwidth Limit Exceeded` error document.
Both apex and www hosts were checked. The current root archive path and legacy
`/web/images/pdf/wer/` paths returned HTTP 403. No successful PDF, archive
index, public API, licence or numerical table was verified. The current origin
cannot currently be described as a working live case-data integration. A
historical recovery is available through the public Internet Archive CDX index:
archived English originals under the official `epid.gov.lk/web/images/pdf/wer/`
path were replayed with their original URL, capture timestamp and digest.

The actual error body and retrieval receipt are retained in
`tests/fixtures/ingestion/LK/`. `wayback-cdx.json`,
`wayback-2007-vol34-no05.pdf` and `wayback-2007-vol34-no06.pdf` are two real
historical originals. Their Table 5 layout is verified: 27 district/national
geographies, eight disease categories, A current-week notification counts and
B year-to-date cumulative notification counts. Report weeks are taken from the
source (20–26 Jan 2007, week 4; 27 Jan–2 Feb 2007, week 5). No confirmed-case
measure is inferred. Link-topology examples in tests are synthetic.

## Proxy comparison, 17 November 2026

At the operator's request, 44 publicly listed proxy endpoints were tested with
TLS verification enabled. Two established HTTPS connections to the official
WER page. The proxy `http://43.155.62.157:443` also passed an independent
egress-IP check, confirming a different public exit from the current route.

Both the current route and that verified proxy returned HTTP 500 with an HTML
title of `509 Bandwidth Limit Exceeded`. The bodies were identical: 7,309 bytes,
SHA-256 `a8a284f377cb9f21c53e5553234ecb693dc4c2c38f3306b6cde4aead5e05e913`.
A second TLS-connected proxy, `http://1.10.185.111:443`, returned the same body;
its independent IP-check request failed, so its exit IP is not claimed verified.
The official homepage, alternate WER path and direct connection to the public
DNS address `66.85.158.91` also returned the same error.

This favors a site/account bandwidth-quota problem over a block of only the
current source IP. It does not establish that every geographical access policy
has been ruled out. The public proxies were used for diagnosis only; production
network settings and default collection schedules were not changed.

[Comparison receipt](evidence/lk-proxy-access-20261117.json) preserves the source
list URLs, proxy responses, verified egress comparison, original-body paths and
database-status check. Historical LK backfill is now recorded separately below;
the proxy evidence remains a live-source access audit.

## Implemented initial integration

The staged collector traverses linked official WER archive pages and downloads
linked English PDFs, with default limits of ten index pages and eight reports.
`max_index_pages` and `max_reports` are explicit bounds; `start_year` (default
2025) and `end_year` filter unambiguously dated listings. No PDF filenames or
year-specific endpoints are guessed. Different language editions are excluded
when explicitly labelled. Unknown or conflicting listing years are skipped.

Raw responses are persisted before any PDF processing. HTTP error bodies and a
collection plan survive a failed index fetch. The immutable plan records
discovery, selected reports, hashes, failures and truncation. Persistence
failures fail the task. Collection fails when no originals are captured.

`inspect_artifacts` replays only recorded artifacts and verifies the PDF
manifest before inspection. It does not contact the source. `collect_wayback`
captures a bounded CDX history and archived originals with an immutable
manifest. `scripts/imports/backfill_lk_wer_history.py` produces a review JSON
and durable raw files; it deliberately does not write the application database.

`organize` and `prepare_rows` emit source-native rows only for the verified
legacy Table 5 layout. They retain separate `cases` and `cumulative_cases`
measures, district versus national geography, source week dates and the WRCD
notification basis. Unverified layouts remain unparsed. Schedule and public
release stay disabled pending rights review and snapshot acceptance.

## Historical preview and database receipt

The two verified 2007 originals are organized by
`data/raw/lk/wer-history-2007/manifest.json`. Offline preview produced 864
rows (432 per report), with zero parse failures. The normal archive replay
runner then stored all 864 source-native facts on 2026-11-17 under task
`31831838-5706-4896-b0c3-117c2d368cec`, crawl run `33030`; the receipt is
`data/raw/lk/wer-history-2007/database-import-receipt.json`.

The facts are retained in holding series because no reviewed `source_series`
categories have been declared yet (`selected=0`, `holding=864`). They are not
publicly exported (`public_release_enabled=false`). The store quality audit
flagged three cross-series all-zero-tail checks for explicit source zeroes at
the two observed national periods. These are review flags, not converted
missing values or withdrawn facts. Archive reuse terms remain unverified.

## Remaining numerical acceptance work

Once official access is restored, capture current originals and the linked
archive. Validate the cover's source-defined week/date range rather
than assuming ISO weeks. Identify national versus district table rows,
notifications versus confirmed cases, current-week versus cumulative columns,
cases versus deaths and explicit zero versus missing/suppressed cells.

These definitions require separate source-native category identities. District
observations must retain their geography; district totals must not be counted
again alongside a national total. Missing weeks and unavailable tables must
remain unknown. Do not infer routine case counts from narrative outbreak
totals. Validate category/geography/period conflicts before enabling numerical
processing and add real-PDF offline regression fixtures. An incomplete capture
never authorizes source-window replacement.

Reuse terms, reviewed category mappings and a generated public snapshot remain
unverified. Public release is explicitly false; no source-series categories
should be declared reviewed solely from this initial capture adapter.
