# Bangladesh DGHS captured fixtures

Retrieved 2026-11-17 with verified HTTPS. HTML/PDF originals are unmodified;
English text fixtures contain the first page's PDFium text extraction.

- `dghs_dengue_directory.html`: the first ten reports from the official DGHS
  miscellaneous-info directory filtered by `6a9cf0471fa8cd87d1f50227`.
- `dghs_dengue_2026-11-15.pdf`: its first linked official PDF,
  `https://objectstorage.ap-dcc-gazipur-1.oraclecloud15.com/n/axvjbnqprylg/b/V2Ministry/o/office-dghs/2026/10/76c9bf79-729b-4156-b5ea-3a51d628fbaa.pdf`.
- `dghs_dashboard_2026-10-10.txt` and `dghs_dashboard_2026-09-04.txt`:
  first-page text of those explicitly dated dashboard PDFs, downloaded through
  their official DGHS directory links. They exercise ten complete regional
  labels and omitted chart categories respectively. Their original PDF URLs
  and SHA-256 hashes are in `dashboard_fixture_provenance.json`.
- The 2026-09-30 dashboard text exercises a duplicated comma glyph requiring
  two independent count checks; 2026-11-07 / 2026-10-26 summary text exercises
  a reviewed Bengali font encoding and a national total printed after its
  label. Their source provenance is in the same JSON file.

Tests parse the source's embedded Bengali glyphs and validate national/division
counts against one another. They never infer daily counts from cumulative
columns. The object path's `2026/10` is upload storage structure, not the
observation month; directory and PDF both explicitly date this report 15 November.
