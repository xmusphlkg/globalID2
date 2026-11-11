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
capture research paths prematurely. No new route has been deployed yet.

The current Cloudflare credential returned HTTP 403 for Workers Routes access.
The new canonical Research Radar links and its sharded sitemap therefore use
the main Pages hostname, which can serve the new path without additional zone
permissions. Previously published direct `papers.globalinfectiousdisease.com`
links still resolve to their old R2 objects; making those old URLs follow the
new publication would require Workers Routes access. The existing static
site publication continues to run until the new generation is initialized.

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
