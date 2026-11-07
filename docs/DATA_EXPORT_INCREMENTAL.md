# Independent surveillance JSON releases

The surveillance update path is separate from static site generation. Its Python
entry point is `src.generation.surveillance_data_export.export_surveillance_data`.
It does not run Astro, generate research/report content, or regenerate CSV/XLSX
archives. The site renderer can continue using a separately attested static
snapshot for its document shell and search metadata.

## Outputs and browser contract

- `exports/surveillance-state/` stores private full country/disease projections,
  projection dependency receipts, and `_release-projection/` for the final
  database acknowledgement. It is not uploaded.
- `exports/surveillance-data/` stores compact browser data, small page summaries,
  immutable content-addressed objects, and `current.json`.
- Logical browser names are `countries/{code}.json`,
  `countries/{code}-source-series.json`, `countries/{code}-summary.json`,
  `diseases/{id}.json`, `diseases/{id}-summary.json`, `meta.json`, and
  `site-status.json`. Country/disease IDs in filenames are lowercase.
- `current.json` has `schema_version: 1`, `release_id`, `generated_at`, and
  `files`. Each file entry has `url`, `sha256`, and `bytes`. By default the URL
  is relative, `objects/{sha256}.json`; `public_url_base` can provide an
  absolute prefix for other custom publishers. The built-in publisher and
  Worker routing contract accept only the default relative object URLs.

A browser reads the pointer once, then uses its pinned immutable file URLs for
all charts and page counters. The publisher uploads all referenced objects
before replacing the remote pointer. It must not replace the pointer if an
object upload fails. Prior objects remain readable for already-open pages.
Object retention/garbage collection belongs to a separate maintenance job: it
must retain every object reachable from retained release manifests, not merely
from the newest pointer.

Country summaries include totals, date coverage, per-disease totals/latest
values, source information, and approved country interpretation fields. Disease
summaries include reporting jurisdictions, coverage dates and a preferred chart
jurisdiction. Archive downloads retain their own generation timestamp; an older
download is not relabelled as a current JSON release. No global sum adds national
totals to their subdivisions.

## Incremental execution

1. The caller selects exact dirty IDs and opens the existing PostgreSQL exported
   read snapshot. The data subprocess imports that snapshot into every database
   transaction.
2. The collector reads only selected jurisdiction observations. It skips reports,
   literature, knowledge-page enrichment and Situation exports. Each country is
   projected once; data-only exports release raw row dictionaries immediately
   after constructing the full source-aware country projection.
   If every selected marker covering a jurisdiction is precise and all changed
   source-series identities are understood by its completed baseline, the
   collector reads only their dependent diseases across their complete history.
   SQL filters the effective reviewed mapping target, rather than assuming a
   registry's historical canonical disease ID is authoritative. Unknown/new
   source identities, coarse parent scopes, missing input summaries and changed
   source configuration fall back to a complete jurisdiction read.
3. Full initialization builds disease views from those country projections,
   rather than projecting the same raw observations a second time per disease.
   Ordinary incremental runs merge selected slices into their full disease
   baseline.
   Private per-disease input summaries retain raw-row incidence source counts,
   monthly heatmap totals and record coverage. Replacing an entire disease
   history also replaces these summaries and recomputes country totals, dates,
   source counts and heatmap; source-only facts and deletions retain their
   original semantics. Unit regressions compare this result with complete
   country projection.
4. A completed country-to-disease dependency receipt identifies which diseases
   actually changed. It hashes every series field, including source observations,
   definitions, quality and supplemental measures. Display/source metadata changes
   invalidate all affected disease views. Removed series invalidate their former
   views. A missing/incompatible receipt conservatively refreshes the whole
   selected country scope.
5. Only affected country/disease browser JSON and summaries are rewritten. The
   writer promotes dependency receipts after all curve writes succeed. Output
   identity checks invalidate reuse after deletion, replacement, or manual edits.
6. The exporter freezes `_release-projection` and watermarks from the same read
   snapshot, writes/reuses immutable JSON objects, and atomically writes
   `current.json` last. It then writes `completed-generation.json` in private
   state. This marker attests the current pointer, all retained baseline inputs,
   and the projection implementation version.
7. The caller uploads a task-specific frozen generation, conditionally updates
   the remote pointer and acknowledges only its explicitly selected dirty IDs
   using the frozen projection. Observations committed during export/upload
   remain pending.

A disease-scoped country projection combines the previous completed versions of
unselected diseases with current versions of selected diseases. It is a
materialized incremental snapshot, not a claim that every disease was re-read at
the same instant. Its country-level source watermark is explicitly unknown;
selected dirty IDs are the acknowledgement authority. Complete country reads
retain their actual country-level watermark.

Static content uses `site_data_export.export(..., content_only=True)`. That path
reads the frozen local country metadata and compact disease coverage; it never
reads current surveillance facts or publication states, generates downloads, or
creates an acknowledgement projection. Missing frozen baselines fail closed.
Coverage has its own local file-identity receipt, so research/report content
updates do not rescan all disease JSON on every export.

`completed_generation(state_dir, public_dir)` returns a valid local baseline or
`None`. Metadata by itself is not a completed baseline. A partial write, missing
file, changed pointer, changed projection implementation or absent completion
marker requires explicit full initialization. The data-only API refuses an
incremental export without this attestation. Initializing from arbitrary local
Astro files is not implicit.

## Measured dependency scope

A read-only test on the existing JP-25 through JP-32 country JSON files measured:

| Operation | Result |
| --- | --- |
| Read eight full country JSON files | 2.448 s |
| Hash complete per-disease inputs for all eight | 1.164 s |
| Change one D038 value and select exact disease dependencies | 0.181 s |
| Previous conservative disease scope | 105 diseases |
| Exact changed disease scope | D038 only |
| Unaffected full/browser disease files avoided | 1,895,440,992 bytes |

This is an input-dependency benchmark, not an end-to-end data release timing.
It did not query PostgreSQL, write production outputs or upload data. The first
receipt initialization remains conservative. Precise scopes still read their
affected diseases' full history; conservative scopes read their full jurisdiction
history from PostgreSQL. Time-window SQL projection and per-window
source-aware merge baselines remain future work. Independent JSON publication
removes static rendering and archive generation from the regular data-update
critical path without claiming that those database costs have disappeared.

## Real-database local JSON measurements

The integration benchmark used exported PostgreSQL read-only snapshots and
isolated `/tmp` outputs for JP-25 through JP-32. It never wrote formal data,
acknowledged changes, built HTML, or uploaded to the network. A synthetic case
correction changed one D038 source observation in memory after the read; the
same correction was used by the independent complete-country reference.

| Run | Measured result |
| --- | --- |
| Initial data-only full projection of eight regions | 119.533 s total; 504 immutable objects created |
| Repeating all eight regions without precise disease hints | 113.576 s; zero changed files or bytes |
| One-disease SQL filter after per-row category resolution | 47.286 s; source SQL/decoding 44.201 s |
| Optimized warm single-source correction | **3.903 s** total; source SQL/decoding 0.951 s; country builder 0.015 s |
| Independent complete JP-25 projection with the identical correction | 57.263 s; full country dictionaries equal, ignoring only the generation label |
| Eight-process complete projection of eight regions | 70.5 s projection, 77.692 s total; existing objects reused |

The SQL improvement materializes the requested active mapping categories once,
then filters a safe superset of candidate observations before applying the
original best-category selection and effective disease check. The earlier
query repeated the category/mapping lookup over unrelated historical rows.
Filtering only after that lookup did not solve the SQL bottleneck.

The warm correction changed five logical JSON files out of 504, representing
10,863,904 uncompressed bytes. A separate local simulated uploader verified and
compressed those actual objects in 0.786 s: 609,662 compressed object bytes plus
204,251 control bytes. That simulation did not exercise network upload, remote
storage or database acknowledgement and must not be presented as production
end-to-end latency.

The warm correction's peak process RSS was 505,844 KiB. The eight-process run
observed a 4,181,440 KiB sum of process-tree RSS, sampled every 0.2 s after
startup; shared mappings are counted more than once. Its parent peak was
713,268 KiB and largest completed child peak 503,840 KiB. Process workers have
independent database engines and import the same exported snapshot. They return
projected country data and small input summaries, not raw observation rows.
Cancellation terminates and joins every reader before the snapshot owner can
close. The CLI sizes workers conservatively from the service cgroup budget;
the Python API defaults to one process.

The complete-projection runs had different object cache/format states, so the
reported totals are observations rather than a controlled claim of speedup from
process count alone. Cold full history remains materially slower than an
ordinary precise update. The following optimization now resolves each distinct
source identity once, instead of once per historical row, for complete reads.

## Complete-projection optimization

The full reviewed-mapping query materializes eligible observations without raw
provenance, computes the effective source code/label once and resolves each
distinct identity's category before joining its dated assertions. JP-25 category
lookups fell from 60,319 to 112. Controlled read-only-snapshot comparisons returned
the same 59,704 complete normalized rows: original query 49.248 s to identity
resolution 5.916 s; a second comparison reduced identity resolution from 5.654 s
to precomputed identities at 5.085 s. The final SQL server time was 3.460 s.

Call-local source-summary, period-key and cutover checks reduce pure computation
on the same captured JP-25 input from 3.959 to 2.757 CPU seconds (medians of five
alternating old/new runs). Complete country, browser and source payloads equal
the saved reference on every run. These are not persistent
caches and do not permit reuse after a projection-policy change.

The new eight-process run started with empty output directories: projection
19.7 s, full local export 27.682 s, 504 newly created immutable objects. Every
logical public object's hash equals the previous complete run. The process-tree
RSS observation peaked at 4,302,128 KiB; parent peak 709,172 KiB. PostgreSQL buffers
were warm and the earlier full run reused objects, so these whole-run observations
are not a controlled process-count comparison.

A synthetic single-D038 correction on that newly initialized baseline took
4.608 s, with source reading/selection 0.929 s and country builder 0.006 CPU s.
Five newly created objects changed; 499 remained unchanged. It excludes network
publication and database acknowledgement, as do the other local export timings.

Projection-source fingerprints now cover the series, reporting-period and cutover
policy helpers. Mapping release activation invalidates the old/new country scope
union transactionally, including withdrawn targets. Further period-sharded
materialization remains future work. See [PROJECTION_PERFORMANCE.md](PROJECTION_PERFORMANCE.md)
for the limits and the distinction between database work, pure projection and
publication.

Raw logs, benchmark scripts, complete-equivalence results, memory samples and
the local uploader simulation are archived under
`data/cache/build-verification/20261107-data-architecture/`.
