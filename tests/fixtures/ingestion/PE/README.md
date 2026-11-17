# Peru official bulletin fixtures

These derived fixtures retain official report text and vector table geometry while
keeping each tracked file below the repository's 5 MiB limit. They are test inputs,
not replacement copies of the source publications. Source URLs and original hashes
remain in the provenance records and ingestion tests.

`official_bulletins.zip` contains the unmodified listing and 1993 week 52 report.
The 2013 week 52 report retains original pages 1 and 22; the 2019 week 51 report
retains original pages 1 and 11. Their national-summary evidence becomes fixture
page 2. `2026w13_cover_summary.pdf` retains original pages 1 and 23 (fixture pages
1 and 2). Raster images were removed with pypdf 7.1.0; vector lines and text were
preserved. No PDF editing library is required by the ingestion runtime.

`fixture_provenance.json` records source/derived SHA-256 digests, byte sizes and
original page mappings. `2026w13_provenance.json` retains its source report digest
and separately identifies the derived fixture. Audit page numbers and artifact
hashes therefore describe the derived test file; production ingestion continues
to record the actual downloaded report's hash and page numbers.

Acceptance compares extracted disease identities, dates, values, measurement and
reporting bases, definition versions and semantic dimensions with the original
fixtures. Only the expected relocated page numbers and artifact hashes are omitted
from that comparison. The original larger files are retained locally outside Git.
