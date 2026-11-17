# Nigeria NCDC source fixtures

Retrieved from the official public NCDC archives on 2026-11-15. Exact source
URLs, original hashes and excerpt formats are in `provenance.json`.

- `wer_2026_w34.pdf`: complete official report. Dedicated disease tables,
  cumulative comparison years and sentinel influenza are retained separately.
- `wer_2016_w36_first_page.pdf`: unchanged first-page excerpt of the original
  report, exported by PDFium. The official route ends in week 37 but the report
  explicitly identifies week 36; tests preserve and audit that discrepancy.
- JSON files contain the original PDFium page text, with page boundaries and
  line breaks preserved. They are source fixtures, not reconstructed case data.
- Historical fixtures cover Lassa 2017/2019 narrative and 2020/2021/2024
  tables, Mpox paired/multiweek/indicator layouts, COVID cross-year and reordered
  headers, Cholera 2023 weekly tables and Yellow fever 2018 weekly prose.

The numerical contracts deliberately exclude unverified graphs, demographic
tables and contradictory reporting periods. Complete originals are retained
under the local raw archive. Fixture availability is not a licence or a public
country release decision.
