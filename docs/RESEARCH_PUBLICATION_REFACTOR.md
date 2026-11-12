# Research publication isolation: work in progress

The existing six-hour static publication remains the production path. The
candidate code in this branch is not an enabled publication channel.

The intended boundary is an exact set of committed literature dirty IDs and a
single exported PostgreSQL read snapshot. The exporter receives explicit
published surveillance and Situation inputs and writes into a private
candidate directory; it must not modify the running site's generated files.
`freeze_literature_json` freezes selected JSON as content-addressed objects,
reuses digest receipts for unchanged inputs, and records counts and timings.
Only small structural checks and the conditional R2 pointer fence remain;
there is no full per-paper content review or per-object remote read-back at
publication time. Browser readers pin a single manifest.

This is **not yet sufficient to switch production**. The separate task,
six-hour scheduler, exact-ID acknowledgements, retries and publication fences
are implemented behind `DATA_RELEASE__LITERATURE_JSON_ENABLED=false`. They
still need a complete production-scale build and controlled initialization
before the old site-release publication is retired.

The experimental content-addressed Papers code is currently disconnected from
production. Its caller must obtain reuse information from a committed earlier
release, preserve immutable object storage, and record failures. Publication
logs cannot detect damaged or missing objects that are assumed unchanged.

The candidate publisher now uploads Papers content and catalogue shards with
bounded concurrency, binds the Papers generation to the JSON manifest, and
switches that single manifest with the captured baseline as a fence. A local
receipt permits reuse only when its generation and destination match the
committed pointer; a missing receipt falls back to conditional creates. The
new Worker routes can serve the current generation or a specific immutable
generation; page assets and interactive requests stay pinned to that generation.
The Pages routing change is packaged only when the independent channel is
enabled, so deploying ordinary site changes before initialization does not
capture research paths prematurely. The Pages route is now deployed and serves
the current independent research generation.

The current Cloudflare credential returned HTTP 403 for Workers Routes access.
The new canonical Research Radar links and its sharded sitemap therefore use
the main Pages hostname, which can serve the new path without additional zone
permissions. Previously published direct `papers.globalinfectiousdisease.com`
links still resolve to their old R2 objects; making those old URLs follow the
new publication would require Workers Routes access. Static site publication
continues independently after the first research generation is initialized.

The isolated Astro builder uses a private research snapshot and a separate
working directory. A real article was rendered in English, Chinese and French
in the isolated workspace; this was a small rendering test, not a complete
backlog publication benchmark. It records article input digests while writing
the snapshot and reuses matching article pages and their prior assets without
reading each cached page back. A second local build of the same article rendered
zero article pages and restored its three localized pages from the cache.

The context capture reads the current surveillance and Situation pointers and
uses the referenced immutable data rather than newer live observations. On a
local, no-network sample of 239 diseases, coverage derivation took about eight
seconds cold and 0.2 seconds with all derived coverage receipts reused. This is
not a measurement of remote R2 latency or a full literature release.

An offline test used that real three-language article build (292 output files)
with an in-memory object store and a synthetic JSON index. It exercised the
catalogue, object uploads and pointer switch without network or model calls;
it does not measure Cloudflare upload speed or the complete research backlog.

## Main-site build reuse

When independent literature publication and the content-only site release are
enabled, the site export reuses the snapshot named by
`exports/research-current-receipt.json`. It installs the build and browser data
from the same completed publication under the research artifact lock, without
querying or rewriting individual literature records. If the receipt or snapshot
is missing, the release fails instead of silently publishing inconsistent data.
The independent literature publisher continues to generate its own snapshots.

The main-site build omits research article, collection, catalogue, feed, and
sitemap shard pages that the independent publication serves. It still renders
the main site, including its homepage and disease panels, against the reused
published snapshot. Public staging omits the research JSON tree when the worker
serves those paths. Ordinary site checks continue to cover the main site.

On 2026-11-12 a read-only test against the local published snapshot reused
approximately 2.3 GB of research data in 22 seconds; a content-only database
projection without the literature rebuild took 1.5 seconds. An isolated full
main-site pipeline produced 4,124 pages and passed its main-site checks. These
are local measurements, not an end-to-end production deployment time.
