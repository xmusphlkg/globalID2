# India IDSP official source fixtures

`ncdc_weekly_outbreaks.html` is an unchanged response from the official NCDC
weekly outbreak archive. `ncdc_2026_week37.pdf` is the linked official 2026
week 37 report. Retrieved 17 November 2026 for internal source-contract testing;
reuse rights have not been established and this integration's public release is
disabled. See `receipt.json` for URLs, byte sizes and SHA-256 provenance.

The main table contains 44 events; the two late-report annex events are excluded
from normalized rows. Dates, cases, deaths, geography and source event IDs are
tested against the PDF, without interpreting outbreak counts as weekly incidence
or merging routine S/P/L reporting forms.
