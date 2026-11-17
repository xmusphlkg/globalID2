# Maldives HPA monthly communicable disease reports

Scope: `mv_hpa_communicable`; source: `SRC_MV_HPA_COMMUNICABLE`; updater: `MaldivesHPAUpdater`; connector: `src.services.ingestion.countries.mv`.

The verified public source is the [Ministry of Health publication archive](https://health.gov.mv/en/publications?search=monthly), which links Health Protection Agency monthly communicable disease PDFs. A public weekly dengue report archive or SIDAS observation API has not been verified. This integration provides historical monthly observations and does not establish current weekly coverage.

## Numerical contract

The reviewed 2022–2024 two-page report templates contain Atoll incidence charts on page 1 and national monthly trend charts in Figure 2 on page 2. Only printed numeric labels attached to current-report-year bars become counts. The four source categories remain separate: Dengue Fever, ARI, AGE, and Viral Fever (also printed as VF).

The parser identifies each chart by its vector border and its own disease heading. Panel positions and order change between reports. It checks all twelve month labels, binds the printed report-year legend to a filled bar color, requires the number of bars to match the report month, checks each bar's month alignment, and reads attached vertical or horizontal vector glyphs. It does not estimate values from bar heights or historical line coordinates.

Each observation retains its original PDF URL, linked publication URL, source report year/month, page number, exact monthly interval, original-byte SHA-256, source disease code, count measure, and extraction definition. Existing 2024 definition identities are preserved; newly supported 2022/2023 originals use `mv_hpa_monthly_2022_2023_printed_bar_labels_v1`.

## Verified historical replay

The official archive was captured on 2026-11-17 in `data/raw/mv/20261117T013557Z-dcca19f4/manifest.json`: 18 report references, 17 original PDFs. Offline replay with the extended parser successfully reads 16 PDFs. One directory item labelled July 2022 links a PDF whose cover identifies August 2022; that document remains rejected because the source periods disagree.

After the existing organizer removes duplicate values and quarantines differing source versions, the replay yields **87 observations**: 32 in 2022, 39 in 2023, and 16 in 2024. Forty-one conflicting observation identities remain quarantined. Observed dates span January 31, 2022 through August 31, 2024. These numbers describe this captured archive and parser version, not complete national or temporal coverage.

Examples checked directly against original printed labels:

| Original report | Verified counts |
| --- | --- |
| December 2022 | 48 monthly counts across four categories; Dengue January–December: 22, 21, 26, 95, 212, 302, 361, 250, 269, 660, 282, 205 |
| February 2023 | ARI 29,307 / 30,628; Dengue 198 / 145; Viral Fever 8,219 / 7,738; AGE 3,502 / 4,357 for January / February |
| December 2023 | December ARI 31,225; Dengue 194; Viral Fever 10,452; AGE 3,486 |
| March 2024 | March ARI 33,637; Dengue 119; AGE 5,428; Viral Fever 11,760 |

Later reports sometimes revise earlier months. For example, the February 2023 report prints January Dengue as 198, while the December 2023 report prints 238. The connector retains that disagreement in its conflict audit and does not silently select a number.

## Explicit gaps

- Atoll incidence rates on page 1 are not national case counts and remain outside this contract.
- Image-only disease headings are not inferred from panel position. The February 2024 snapshot therefore contributes AGE and Viral Fever only.
- Overlapping, absent, or ambiguous labels omit the individual month/category; other explicit labels in the panel remain usable. The April 2024 Dengue March label is excluded this way.
- Missing months and source-version conflicts are unknown, never zero.
- Historical comparison line labels, unreadable charts, public weekly dengue, and SIDAS remain outside numerical coverage.

## Validation

`venv/bin/python -m pytest tests/unit/test_mv_hpa.py tests/unit/test_mv_hpa_history.py -q` — 19 tests passed.

The historical fixtures contain unmodified official PDFs for December 2022; February and December 2023; and February, March, and April 2024. Their official publication URLs, attachment URLs, byte sizes, and SHA-256 hashes are recorded in `tests/fixtures/ingestion/MV/historical_pdf_manifest.json`. Tests cover moving panels, vertical/horizontal labels, current-year binding, exact source periods, unknown image headings, individual ambiguous labels, source hashes, and offline publication-to-PDF replay.
