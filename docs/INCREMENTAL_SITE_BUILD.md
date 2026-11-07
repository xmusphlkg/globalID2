# Incremental site build

Surveillance data updates now have an independent JSON publication path and do
not invoke the build described here after migration. See
[STATIC_SITE_AND_DYNAMIC_DATA.md](STATIC_SITE_AND_DYNAMIC_DATA.md). These build
optimizations apply to template and static content publication.

The first optimization stage keeps the existing static site and release gates.
It reduces repeated rendering, cache writes and dependency parsing, and runs
independent validators on multiple CPU cores.

## Execution order

`npm run build` and `npm run build:astro` use `scripts/build-pipeline.mjs`:

1. Synchronize flag assets.
2. Start style, source translation and map ISO checks in separate Node processes.
3. Concurrently plan the incremental build, render, restore reusable pages and verify inputs; wait for both rendering and source checks.
4. Optimize the generated world map.
5. Run performance, SEO and output translation checks in separate Node processes.
6. Promote the page caches only after all checks pass.

`GIDS_BUILD_CHECK_WORKERS` controls checker processes (1–3, default the smaller
of 3 and available CPUs). Set it to 1 to restore serial checking. Each checker
has a 1.5 GiB Node heap cap; Astro retains its separately configured rendering
heap and concurrency. A failed checker stops queued work and terminates peers,
including child process groups. Signals also cancel the active group.

The SEO auditor copies title and canonical keys before retaining them in global
uniqueness maps, so regex substrings cannot retain each complete HTML document.
A bounded-heap regression audits a catalogue larger than the checker heap.

The render, map write and cache promotion remain ordered. Papers and reports
splits still run serially because both modify the main `_redirects` and
`_headers` files. Running those existing splitters concurrently is unsafe.

## Incremental coverage and integrity

Country/disease routes join the existing three-locale page bundle cache. Inputs
include country data, country aliases' alert shards, disease research and the
country download entry, together with the transitive template dependencies.
The existing top-50 selection remains authoritative: newly selected routes
render, removed routes disappear from the clean output and cache.

Unchanged cache files are no longer overwritten during finalization. Copies
request filesystem reflinks with ordinary copy fallback; they never hard-link
HTML because the splitters rewrite output for separate hosts. File identities
retain inode, size, nanosecond timestamps and the existing racy-stat safeguard.

Verified page caches retain their shared-asset references. Unchanged HTML need
not be reparsed to verify those references, but each distinct referenced asset
must still exist in the assembled output. Missing assets still trigger a full
render. Papers/reports dependency traversal similarly caches parsed references
by artifact checksum and parser version; asset existence and transitive changes
are checked on every split. Deletion and prior asset-generation retention are
unchanged.

## Observability and validation

`[build-stage]` and `[build-timing]` report checker, rendering, planning, restore,
consistency-check and promotion durations. `[build-plan]` groups counts by page
type and reason. `data/cache/site-build-plan-report.json` records the initial
plan with individual reasons and removed keys:

- `forced`, `missing-manifest`, `shared-inputs`
- `new`, `inputs`, `missing-output`, `damaged-output`, `reused`

An asset-recovery full rebuild is separately logged after the initial plan.
The first build after template/cache implementation changes is intentionally
full. Compare subsequent builds using identical inputs and hardware; a cold
full build is not a valid baseline for warm incremental speedup.

Relevant tests:

```sh
cd astro-site
node --experimental-strip-types --test scripts/build-pipeline.test.mjs scripts/page-build-cache.test.mjs scripts/research-build-cache.test.mjs scripts/file-fingerprints.test.mjs scripts/source-dependencies.test.mjs scripts/reports-build-cache.test.mjs scripts/check-seo-output.test.mjs
```

```sh
venv/bin/python -m pytest tests/unit/test_bundle_assets_cache.py tests/unit/test_papers_bundle.py tests/unit/test_reports_bundle.py tests/unit/test_artifact_index.py
```

Source-closure tests and a complete build use generated site inputs; a fresh
checkout needs an exported data snapshot first.

### Verification on 2026-11-06

An isolated copy of the production inputs rendered 27,783 HTML pages. The first
post-render SEO audit exceeded the new 1.5 GiB checker cap, exposing retained
HTML strings. After the string-ownership fix, the same output passed all gates;
SEO took 41.4 seconds with a peak RSS of 195,604 KiB. A 64 MiB heap test also
audited 96 MiB of fixture HTML successfully.

Full-render outputs were retained across changes confined to validation and
cache-reference string ownership. Before promoting the isolated test cache,
every bundle's data/template fingerprint and output paths were asserted equal
to the rendered pending snapshot. Production retains the ordinary conservative
cache invalidation; no migration bypass was added.

A subsequent complete `npm run build` passed on unchanged inputs:

| Measurement | Result |
| --- | --- |
| Research bundles reused / rendered | 6,529 / 0 |
| Other tracked bundles reused / rendered | 2,456 / 0 |
| Cached outputs matching full-render checksums | 33,484 / 33,484 |
| Cache planning and verification | 34.1 s |
| Astro render (remaining 828 uncached pages) | 253.8 s |
| Restore reusable output | 6.9 s |
| Restore and verify shared assets | 0.1 s |
| Recheck source consistency | 23.6 s |
| Parallel performance / SEO / translation checks | 40.8 s wall time |
| Same checks and outputs, serial comparison | 68.6 s wall time |
| Cache finalization | 13.0 s |
| Sum of complete pipeline stage wall times | 377.5 s |

An isolated warm dependency-traversal benchmark used the same 1,200 real paper
pages and asset files for both implementations. The prior helper took 3.662 s
and read HTML 1,200 times; the new helper took 0.163 s with zero HTML reads.
Both returned the same 11 transitive assets. This is a component benchmark,
not an end-to-end publication speedup.

These are single-run observations alongside other server work. The earlier
production release rebuilt 497 tracked bundles, so its total time is not a
controlled before/after comparison with this unchanged-input run. The remaining
Astro compilation, global routes and historical Situation pages are still the
largest measured cost.

## Subsequent stages

This stage does not yet skip an entire unchanged publication partition, remove
full output audits, or shard Astro rendering across processes. Those changes
need immutable partition receipts covering deletion, target configuration and
failed-publication recovery, and independent output directories. Remote object
inventory reconciliation is retained. Use the new stage measurements to choose
the next bottleneck instead of increasing concurrency for shared writers.

## Second stage: route work and bounded data lifetimes

Historical Situation weeks, months and outbreak JSON now participate in the same
verified page cache. Weekly dependencies are the displayed week, matching alert
details, outbreak labels and adjacent-period links. Unrelated outbreaks, alerts
from later weeks and map export timestamps do not invalidate an older week.
Corrections to that week's alerts and navigation changes still rebuild it.
Monthly pages depend on their constituent weeks and neighboring months; removed
periods and outbreaks disappear from the planned route set.

Weekly publication metadata uses the earliest displayed source-notice date. The
map export timestamp is no longer advertised as a historical page's modification
date; the source alert contract does not supply a reliable correction timestamp.

During static rendering (`GIDS_STATIC_RENDER=1`, set by the build wrapper),
sitemap partitions share one derived catalogue and RSS routes share one inverted
facet index. Development does not memoize these build catalogues. Public feed
filtering, preprint gates, aliases, item order and XML stay unchanged.

JSON readers use bounded LRU caches keyed by device, inode, size and nanosecond
mtime/ctime. Reads verify file identity again after parsing; recently written
files retain the existing racy-stat safeguard. The shared small-file cache holds
64 MiB of source bytes with a 16 MiB per-file limit. Surveillance readers use a
separate 128 MiB source-byte budget with a 64 MiB per-file limit. Parsed objects
can occupy more memory than source JSON, so these are not RSS limits. Callers
treat cached objects as read-only; oversized files are read without retention.

Country and disease directory caches retain only route candidates, indexability
and timestamps. Full records are loaded on demand through the bounded cache.
Top-country/disease selection keeps only the best candidates while scanning,
allowing losing histories to be collected. A real-data test before this change
exhausted the existing 6 GiB Astro heap at sitemap generation; the remedy changes
data lifetimes rather than increasing that limit.

Additional regression tests:

```sh
cd astro-site
node --experimental-strip-types --test src/lib/build-memo.test.ts src/lib/json-file-cache.test.ts src/lib/site-data-loaders.test.ts src/lib/research-feed.test.ts src/lib/sitemap-entries.test.ts scripts/situation-build-cache.test.mjs
```

Component benchmarks on the frozen 2026-11-06 snapshot:

| Work | Before | After | Equality check |
| --- | --- | --- | --- |
| RSS definitions and facet selection | 18.076 s | 0.196 s | 860 en/fr XML outputs identical |
| 462 map reads | 7.855 s | 0.043 s | Same parsed data; one physical read |
| Seven sitemap group computations | 19.023 s | 2.517 s | Same entries; one computation |

These are isolated component measurements, not additive end-to-end speedups.

A separate real-data check compared compact country/disease directories with
full-record sitemap readers. All 17,370 generated entries matched (Situation
was omitted from both sides of this directory-only check). First summary
construction took 35.993 s, and a repeated group computation using those summaries
took 2.179 s; the full-record comparison took 55.556 s. This check ran alongside
the full build and includes file loading, unlike the primed catalogue benchmark
above. It is a correctness and bounded-retention check, not a controlled overall
speed comparison.

### Complete-build verification

The updated full build passed with `NODE_OPTIONS=--max-old-space-size=6144`
and rendering concurrency 8. Astro rendered 27,780 HTML pages in 733.3 s.
The sampled Astro peak RSS was 8,785,892 KiB (RSS includes memory outside the
6 GiB JavaScript heap). Source consistency, performance, SEO and translation
gates all passed; only then were 6,528 research bundles and 3,759 other bundles
promoted. The new Situation coverage comprises 153 historical weeks, 38 months
and 1,112 outbreak JSON endpoints.

The subsequent unchanged-input run uses production's default rendering
concurrency 2 and the same 6 GiB heap cap. The earlier first-stage 377.5 s run
used concurrency 8 and a slightly different research snapshot, so it is a
reference observation rather than a controlled percentage-speedup baseline.

The unchanged-input build passed all gates in 241.6 s (sum of pipeline group
wall times). It reused every tracked bundle and rendered only 255 remaining
HTML pages. All 35,165 cached output files were independently SHA-256 checked
against the completed full-render baseline and matched exactly.

| Warm-build stage | Wall time |
| --- | --- |
| Plan and verify caches | 34.2 s |
| Astro compilation and remaining routes | 120.5 s |
| Restore reusable files | 6.2 s |
| Restore and verify shared assets | 0.1 s |
| Recheck source consistency | 22.1 s |
| Parallel output gates | 40.0 s |
| Finalize caches | 13.8 s |

54 targeted Node tests, a clean-fixture build integration test and a targeted
TypeScript check passed. Logs and checksum evidence are archived under
`data/cache/build-verification/20261106-stage2/`. The first build after adopting
the changed templates will regenerate its caches. Remaining measured work is
Astro compilation/global route initialization, country-directory planning and
full output validation; this stage does not remove those integrity gates.

## Third stage: persistent directories shared by planning and rendering

The planner now stores compact country facts (aliases, candidate scores, series
keys and disease IDs) and disease facts (indexability and timestamps). Directory
facts are independent of rendered-page caches and never represent a successful
publication or a passed release gate. They can be reused after a failed render.

Entries bind every source path and content checksum to a projection version
covering the projection, selection, coverage, SEO and URL helper implementations.
The existing file fingerprint cache still checks device, inode, size and
nanosecond modification/change times on each lookup. Only changed files need
new content hashes and JSON parsing. Missing files, edits and newly added
knowledge files invalidate the relevant summaries. Malformed caches or damaged
summary checksums cause recomputation. Writes use atomic replacement and drop
entries not consumed by the current plan.

The build wrapper supplies an internal directory receipt to Astro. Readers
verify its version, data root, source identities and summary checksums; recent
files use the one-second racy-stat safeguard. Missing or stale receipts fall
back to the normal bounded JSON reader, so direct Astro commands and development
remain functional. Rendering does not write this cache. Source consistency is
still checked after the render, and all performance, SEO and translation gates
still run.

Ranking now operates on compact candidates. Link-only consumers access code,
slug and score without loading histories. The selected detail-page candidates
expose full country/series data through lazy accessors, preserving existing
route props and ranking. The planner retains complete content hashes in page
dependencies, so history corrections still invalidate affected pages even when
the compact ranking facts are unchanged.

### Component validation

On the same frozen data, with the existing file caches warmed for both paths,
page planning fell from **20.357 s to 3.220 s**. All 3,759 bundle fingerprints
and output paths matched the previous planner. There were 827 summary lookups,
zero misses and zero history JSON parses. The initial summary construction
performed 533 projections; subsequent work reused them across processes.

A separate renderer-directory comparison returned identical directories while
reducing full-history reads from 772 files / 3,692,937,096 bytes to zero. Loading
the compact receipt and checking metadata still occurs. The observed component
times were 38.676 s without the receipt and 0.034 s with it; this check ran
alongside the full build and is not an overall publication speedup.

```sh
cd astro-site
node --experimental-strip-types --test scripts/directory-build-cache.test.mjs scripts/page-build-cache.test.mjs src/lib/site-data-loaders.test.ts src/lib/sitemap-entries.test.ts scripts/source-dependencies.test.mjs scripts/file-fingerprints.test.mjs
```

The regressions cover same-size edits with restored mtime, deletion, newly
published knowledge, projection version changes, corrupt summaries, mismatched
roots, recent writes, reads racing with input changes, ranking ties/limits and
lazy history access. `[directory-cache]` records hit/miss/parse counts during
normal builds; these internal receipts need no user configuration.

### Complete-build verification on 2026-11-06

A full build of 27,780 HTML pages passed with rendering concurrency 8 and a
6 GiB heap cap. Astro took 696.6 s; the complete pipeline took 820.7 s. A second
build on the same frozen inputs used production's default concurrency 2 and
passed in **138.9 s**, rendering 255 uncached HTML pages and reusing all 10,287
tracked bundles. Every one of the 35,165 restored output files matched the
full-render baseline by SHA-256.

| Warm-build stage | Wall time |
| --- | --- |
| Plan inputs and verify page caches | 14.1 s |
| Astro compilation and remaining routes | 59.5 s |
| Restore reusable files | 4.7 s |
| Restore and verify assets | <0.1 s |
| Recheck source consistency | 3.6 s |
| Parallel output gates | 39.4 s |
| Finalize caches | 13.1 s |

The prior stage's 241.6 s run is a reference observation on the same server,
not a controlled whole-pipeline percentage-speedup benchmark. The planner
comparison above explicitly warms both implementations and checks identical
inputs/outputs. The directory-read comparison checks the actual renderer
loaders. Comparing files from different worktree roots also exposes Astro
island UID differences on interactive pages; restored-file verification uses
the completed full build from this worktree as its exact baseline.

34 targeted Node tests, the clean-fixture build integration and a targeted
TypeScript check passed. Evidence is stored in
`data/cache/build-verification/20261106-stage3/`. Initial adoption regenerates
template/page caches once. Remaining fixed work is compilation/global routes
and full output audits; this change keeps those integrity checks in place.


## Fourth stage: shorten the publication path

The optimization target is elapsed time from a requested update to verified
public visibility. CPU and memory utilization are secondary. The release
renderer receives a 12 GiB heap allowance and defaults to concurrency 8
(`GIDS_ASTRO_BUILD_CONCURRENCY=1..8` remains configurable). Source checks overlap
rendering in independent processes; any failure cancels the group and prevents
cache promotion and publication. Output validators remain concurrent. SEO HTML
parsing and per-page checks additionally use up to 8 worker threads by default
(`GIDS_BUILD_SEO_WORKERS=1..16`). Their results are merged before checking
catalogue-wide uniqueness and sitemap/redirect targets; a worker failure fails
the audit and terminates its peers. The original serial auditor remains
available for comparisons. Bundle HTML asset-reference parsing also uses up to
8 read-only processes (`GIDS_BUNDLE_ASSET_WORKERS=1..16`) when at least 64
uncached pages need parsing. Only the parent copies assets and writes manifests
and routing files; small changes retain the existing serial cache-hit path.
Astro's rendering concurrency overlaps work within one process; it is not an
8-process renderer.

Large generated research indexes and the homepage's China/Japan data now use
server-side JSON readers instead of JSON module imports. Development observes
file changes; a static render consumes one frozen snapshot. This removes large
payloads from the compiler/typechecker graph without making public visits depend
on a database or introducing an SSR service.

The release entry point stores a local receipt only after rendering, validation
and required bundle splits complete. On another task with the same inputs, it
verifies all final partition files, stamps fresh homepage provenance and skips
Astro, output audits and splitting. Release manifests, destination reconciliation,
uploads, public verification and database acknowledgements still run normally.
A build receipt never proves successful remote publication.

Input identity includes generated/public data, rendering and validation code,
configuration, dependency locks, relevant environment, Node version and UTC day.
Output identity includes every file in required partitions except the release
sidecar replaced by the orchestrator. Private Git metadata and the release
projection acknowledgement sidecar are excluded. File identities use the existing
strong checksum receipts with the racy-stat safeguard; changed files are read
again, with independent checksum misses hashed on up to 8 threads. Warm
metadata-only hits do not create hashing jobs. Missing/corrupt receipts, changed inputs or outputs, and cache I/O failures
fall back to the normal build. No remote file downloads are needed for this local
reuse decision. The first receipt creation reads all inputs/outputs; warm runs
still enumerate local metadata and do not eliminate every filesystem traversal.

This fast path applies only to unchanged rendering inputs. A small data change
still goes through dependency-selected rendering, compilation/global routes and
output audits. Papers/Reports splitters still run in order because both mutate
main-site routing files. Those are remaining latency costs, not claimed solved
by higher concurrency or by the no-change fast path.

### Measurements and verification on 2026-11-06

All measurements below used a frozen temporary worktree, not a production
publication. They exclude database export, uploads and CDN propagation. The
baseline full render generated 27,780 HTML pages; its complete pipeline took
804.8 s before introducing the parallel SEO runner. Subsequent rendering
restored all 35,165 cached files with exact SHA-256 agreement.

| Measurement | Observed elapsed time |
| --- | --- |
| SEO on the same complete output, serial | 40.0 s |
| SEO on the same complete output, 8 threads | 9.3 s |
| Warm npm pipeline (sum of logged stage groups) | 87.5 s |
| Build entry point including cold receipt initialization and split preparation | 197.8 s |
| Another task, identical inputs and verified partitions | 19.2 s |
| One country summary changed, including build gates and split preparation | 151.6–165.5 s |

The country experiment modified only the isolated `countries/ca.json` fixture.
A one-count change selected five dependent bundles but was hidden by the UI's
rounded summary. A second change crossed that rounding boundary and visibly
changed all three Canadian homepages from 14.2M to 14.3M. Exactly three cached
HTML checksums changed; 35,162 were unchanged. Both runs selected one country
bundle and four country/disease bundles, reusing the other 10,282 bundles.
Synthetic values were restored afterward and were never uploaded.

A prior cold Papers/Reports split took 186.2/89.1 s. With already rewritten HTML
and parallel reference parsing it took 47.2/13.2 s; with reference receipts warm,
33.1/8.2 s. These are different cache states, not a controlled process-count
speedup comparison. The timings expose the remaining cost of entering both
splitters even for a change confined to country data.

79 Python regressions, 48 distinct targeted Node tests, the clean-fixture
build, and `astro check` passed (335 files, zero errors/warnings; existing
vendor hints remain). Regressions cover cross-worker uniqueness and sitemap
checks, parser-worker failures, changed shared sources, malformed receipts,
output corruption/deletion/addition, compressed empty provenance attributes,
and optional-cache failures falling back to validated builds. Evidence is in
`data/cache/build-verification/20261106-stage4/`.

The no-change path is not a claim of second-scale data refresh. Small changes
still pay compilation/global-route, complete performance-audit, cache-finalization
and partition-preparation costs. The next architectural boundary is to retain
validated unaffected partitions through a data update and remove those full-site
steps from its critical path. These measurements do not establish end-to-end
production refresh latency.

## 2026-11-06: audit facts and partition preparation (0.17.5)

The renderer concurrency setting and benchmarks in this historical section
predate the November 7 memory fix. The default is now zero prerender workers;
see `DATA_RELEASE_RESILIENCE.md` for the current setting and its tradeoffs.

The build now pins Astro 7.4.7 and project-local Node 22.23.4. npm scripts
automatically use the local runtime; the release receipt records that runtime.
`GIDS_ASTRO_RENDER_WORKERS=8` enables native parallel prerendering (`0` disables
it). Two workers did not improve the measured warm render; eight remain the
default. `GIDS_BUNDLE_HTML_WORKERS=8` rewrites changed partition pages on separate
CPU processes. Readers return bytes to one writer; failed workers cannot promote
a manifest. Work is submitted in bounded batches.

Performance audits persist extracted page facts, then evaluate current budgets
and current asset dependency graphs. They do not cache a blanket pass result.
Captured asset names are detached from their HTML backing strings: the first
full-data test exposed a retention bug, now covered by a 96 MiB HTML / 64 MiB heap
regression. Reused page-cache files are not copied again during finalization.

Bundle asset graphs remember both existing and absent dependencies. Partitioning
preserves unchanged PNG/JSON files instead of replacing their inodes and
rehashing them for the manifest. Rendered-file digests can cross the Node/Python
boundary only when device, inode, size, nanosecond mtime/ctime and the stable-stat
window match; otherwise the file is read. Same-size edits, deleted assets,
changed imports and damaged receipts still invalidate reuse.

On the same isolated full dataset (27,780 rendered HTML pages), the final visible
Canada change took **125.98 seconds** through the real local release entrypoint:

| Local stage | Seconds |
| --- | ---: |
| Build, restore and validate Astro output | 76.56 |
| Situation gate | 0.42 |
| Papers partition | 19.25 |
| Reports partition | 6.45 |
| Input/output receipts, identity and deployable digest | 23.30 |

The earlier comparable measurement was 151.6–165.5 seconds. Exactly three
localized Canada HTML files changed; 35,162 cached files stayed identical, and
all 35,165 cached file checksums were verified against their manifests. The
validated release receipt also matched all current inputs and partition outputs.
The synthetic count was confined to the worktree and restored after verification.

Initial partition preparation after the code change measured 60.14 seconds for
Papers and 28.16 seconds for Reports, versus 151.91 / 88.13 before parallel HTML
rewriting and file-preserving transfers. The no-change entrypoint measured
15.91 seconds before the final partition optimizations. These are local tests,
not database-export, network-upload, CDN propagation or production freshness
measurements. A changed-data refresh still takes about two minutes here; this
does not establish second-scale refresh or eliminate all full-site work.

Validation: 108 Python regressions, 66 distinct targeted Node regressions, a
clean-fixture build, and `astro check` (338 files, zero errors/warnings, nine
existing vendor hints). Evidence: `data/cache/build-verification/20261106-stage5/`.
