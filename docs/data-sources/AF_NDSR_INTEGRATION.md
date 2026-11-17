# Afghanistan MoPH NDSR weekly collector

The numerically verified [WHO outbreak collector](AF_WHO_OUTBREAKS_INTEGRATION.md)
is a separate source. Its availability does not establish MoPH NDSR coverage.

Status checked 2026-11-17: collector and offline replay implemented; live weekly
originals inaccessible from this environment. No real NDSR numerical observation
has been verified or imported by this implementation. The numerical parser is a
candidate contract covered by synthetic tests, not a validated NDSR format.

| Contract | Value |
| --- | --- |
| Country | `AF` |
| Scope | `af_ndsr_weekly` |
| Source ID | `SRC_AF_MOPH_NDSR` |
| Updater | `src.data.processors.af.AfghanistanNDSRUpdater` |
| Staged connector | `src.services.ingestion.countries.af` |
| Requested history default | 2025, a search window rather than a coverage claim |
| Numerical geography | `source:AF:ndsr:reporting_sites` |
| Release | Disabled pending verification of actual originals and mappings |

## Source evidence and access limitation

The user supplied the direct [MoPH NDSR WER 33-2026 PDF](https://moph.gov.af/sites/default/files/2026-09/NDSR%20WER%2033-%202026_0.pdf).
Requests to it returned TLS EOF or connection timeout; retrying with `www`, HTTP,
and curl did not yield an original. The candidate [MoPH reports directory](https://moph.gov.af/en/reports)
also timed out. Its path is a discovery candidate, not an independently verified
working NDSR archive.

The [WHO EMRO surveillance introduction](https://www.emro.who.int/afg/programmes/communicable-disease-surveillance-response-dews.html)
initially returned HTTP 403, then succeeded using the collector's user agent
(64,468 bytes). It describes NDSR but contains no directly linked MoPH weekly
PDFs. The candidate EMRO infectious disease situation report directory initially
returned HTTP 403; a later HTTP 200 response was a soft 404 page (`WHO EMRO -
404 Error`), not an archive.
A [WHO PHSA dated 4 March 2026](https://cdn.who.int/media/docs/default-source/documents/emergencies/who-phsa-afghanistan_march_2026.pdf?download=true)
did download (645,873 bytes); it is contextual evidence and is not ingested as a
weekly NDSR case/death source. A web search connector was also unavailable
(`token_revoked`, HTTP 401). None of these failures establishes that the sources
are generally unavailable to other clients.

The collection plan distinguishes `explicit_report_url` seeds from PDFs found
in captured `official_link` pages. It does not manufacture guessed historical
URLs. The supplied seed is filtered by its report year, not its upload folder.
Callers can provide another explicit MoPH PDF using `report_urls=[...]`.

## Numerical boundaries

Parsing requires Afghanistan/MoPH and NDSR identity, an explicit source year and
week, and a seven-day source date interval. It never converts a week number to
an ISO date. The candidate table contract requires an explicitly national
current-week context and the exact columns `Disease / Current week cases /
Current week deaths` or `Disease / Weekly cases / Weekly deaths`. Other layouts
remain preserved originals with an audited parse rejection. Real MoPH table
layouts still require inspection and a captured-original regression fixture.

Cases and deaths retain separate `SourceDiseaseCode` identities and `Measure`
fields. The numeric payload uses the shared store's `Cases` value column for
both measures. Suspected or source-defined labels remain intact; they are not
silently mapped to confirmed disease totals. Missing values stay unknown.
Surveillance-site totals do not certify complete national incidence.

Cumulative figures, reporting-site counts, outbreak counts, laboratory tests,
charts, province and district tables are excluded. Conflicting duplicate
observations are quarantined together. Raw file hashes, URLs, page numbers,
source period and failed downloads are retained. Public release remains disabled
and `numerical_contract_live_verified` is false.

An inaccessible source returns an explicit `source_access_blocked` audit with
zero observations and no source latest date. Zero observations are not zero
disease cases. This supports collect-only archival diagnostics while preventing
an unverified data-coverage claim.

The shared runner rejects a processed run without verified numerical observations
(`requires_verified_observations=True`) and retains the organization audit.

## Validation and next verification

Run `venv/bin/python -m pytest -q tests/unit/test_af_ndsr.py`.
Tests cover source host restrictions, linked-only discovery, explicit calendar
boundaries, case/death separation, missing values, cumulative/outbreak/subnational
table rejection, immutable plan replay, capture failures and conflict quarantine.
All test report content is explicitly synthetic.

Before numerical coverage can be claimed, retrieve a genuine NDSR weekly PDF,
inspect its table and date conventions, record its digest and provenance, update
the parser to the actual format, and replay it into the registry holding series.
Only verified source category mappings should enter the public projection.
