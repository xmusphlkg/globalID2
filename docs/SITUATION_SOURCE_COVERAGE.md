# Situation authority coverage

Situation collects official event reports, rather than every country's routine
case-count series. A country with no points in the selected time window is not
necessarily disease-free. The map defaults to 30 days; 90 days and All show
older reports.

## Registered feeds

| Feed | Scope and transport |
| --- | --- |
| `who_don` | WHO global Disease Outbreak News API |
| `who_wer` | WHO Weekly Epidemiological Record publications API |
| `who_sitreps` | WHO global emergency reports plus mpox, cholera and Ebola topic lists |
| `ecdc_cdtr` | ECDC weekly threat reports |
| `ecdc_events` | ECDC event pages, newest update per event per week |
| `africa_cdc` | Africa CDC statements and weekly reports |
| `who_afro` | WHO Africa weekly outbreak/emergency bulletin archive |
| `who_searo` | WHO South-East Asia epidemiological bulletins |
| `who_emro` | WHO-authored Eastern Mediterranean situation and polio reports distributed by UN OCHA ReliefWeb |
| `who_euro` | WHO Europe Health Security Bulletin |
| `who_wpro` | WHO Western Pacific respiratory, avian influenza, dengue and Pacific surveillance lists |
| `paho` | PAHO IRIS surveillance newsletters, principally respiratory viruses |
| `paho_alerts` | PAHO-authored epidemiological alerts/updates and situation reports distributed by UN OCHA ReliefWeb |

## PAHO gap and provenance

The previous PAHO adapter queried only IRIS, required `dc.type=Newsletters`, and
accepted English records. In the database audited on 2026-11-02, all 110 PAHO
records were respiratory regional updates. That is not the PAHO alerts archive.
Successful newsletter collection therefore did not demonstrate alert coverage.

PAHO's public website and PDFs returned HTTP 403 from this host during validation.
The new feed uses the publicly accessible ReliefWeb archive and original report
PDF attachments. It verifies `/organization/paho` on each listing card and again
on the report body before downloading. WHO EMRO likewise requires the WHO author
and Eastern Mediterranean title scope. Author and distribution channel are
explicitly distinguished in the source registry. No search-engine result or
article abstract substitutes for a full report.

Report dates explicitly stated at the end of a title take precedence over mirror
publication dates. Pagination follows upload dates, so a newly uploaded old
report cannot hide newer reports on subsequent pages. Changed archive markup,
repeated pages, and pagination failures fail visibly rather than reporting an
empty successful collection.

The shared HTTP client retries zero-byte HTTP 200 responses before parsing;
these occurred intermittently in the mirror during the initial backfill.
Report attachments include PDFs tagged as infographics (notably EMRO polio
bulletins), while unrelated recommended reports remain outside the reader's scope.

WHO AFRO download links may use IRIS `/bitstreams/.../download`; SEARO uses
`/server/api/core/bitstreams/.../content`. Both are downloaded as original PDFs.
The country in an AFRO hosting URL is not used as the report's geographic scope.
Countries are extracted from the full regional report through normal review.

## Refresh and verification

The scheduler's unscoped refresh includes every registered feed. A process that
already imported the old registry needs a normal task-runtime restart to load
new adapters. Do not interrupt a running data release merely to reload imports.

```bash
venv/bin/python scripts/run_situation_notices.py \
  --sources paho_alerts who_afro who_searo who_emro who_euro who_sitreps \
  --since-days 120 --document-limit 200 --ai-limit 200 --require-cross-model
```

The CLI queues work through the existing dispatcher. Metadata collection,
full-text retrieval, reviewed alert extraction, and the static site release are
separate stages. A collected report is not a published map point until extraction
passes review and a release exports it. Standard scheduler catch-up drains the
remaining queue; failed documents retain the existing retry policy.

`sources.json` retains the latest run **per source**, including after scoped
refreshes, and exports `latest_report_date`, `latest_alert_date`, and `alert_count`.
These distinguish successful HTTP collection from reports that actually yielded
alerts. The date/count fields describe stored reports and exported alerts; they
do not certify complete regional coverage.

Regression fixtures under `tests/fixtures/global_sources/` are small excerpts
of official public archive cards retrieved on 2026-11-02. Relevant offline checks:

```bash
venv/bin/python -m pytest -q tests/unit/test_global_sources_*.py \
  tests/unit/test_situation_*.py tests/unit/test_collection_source_control.py
```

## Coverage boundaries

All six WHO regions now have an explicit feed, alongside global WHO products and
ECDC/Africa CDC. This does not claim exhaustive coverage of national ministries,
local health departments, or all WHO country-office products. Those require
individual adapters and provenance checks. WHO Europe publishes quarterly;
regional lists and mirrors may lag publication. Some WHO topic lists expose only
recent reports, so a large `--since-days` value cannot guarantee a full historical
backfill for those lists. The old EMRO Weekly Epidemiological Monitor page was
last dated 2023 during the audit and is not presented as an active recent feed.
