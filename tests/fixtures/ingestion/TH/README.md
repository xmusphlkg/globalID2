# Thailand Report 506 source fixtures

Captured from the official DOE Report 506 public site on 2026-11-15.
`reports_2023.zip` contains the official index, 72 linked disease history pages
and their 72 monthly case/death RTF reports for Buddhist year 2566 (2023).
The RTF source publication date is 12 September 2567 (2024-09-12).
`audit_2023.json` records each exact report URL, native label, row count and
published annual national count, independently checked during onboarding.

`legacy_samples.zip` and `legacy_samples.json` retain representative 2003,
2019 and 2020 official reports and their provenance. These cover the older
layout with the Buddhist report year embedded in the title cell and the
singular annual `death` header. The importer also verifies the Thai RTF
Unicode header after Windows-874 and RTF fallback decoding.

The completed 2003–2023 review validates 1,324 available disease/year reports
and 15,888 national disease/month rows. Complete source downloads remain in
`data/raw/th/history_review_20261115/`, with per-report export evidence in
`data/current/th/source_audit.json`. Historical HTTP 404s remain source gaps.

The two uncompressed 2024 RTF files are rejection fixtures: both were updated
on 2024-12-25, before that reporting year closed. The influenza file also has
a national monthly sum that differs from its annual total.

These are internal parser verification fixtures. Public reuse rights have
not been verified, and the Thailand connector keeps public release disabled.
Source aggregates overlap their component reports and are separate identities.
Do not add their totals together.
