# Papers hosting channel (R2)

Research article pages outgrew Cloudflare Pages’ free **20,000-file** ceiling.
Public hosting is therefore three channels:

| Channel | Content | Target |
| --- | --- | --- |
| Pages main | Countries, diseases, situation, research list/index pages, `_astro`, scripts | Cloudflare Pages (`globalidv2`) |
| GitHub downloads | Time-partitioned CSV/JSON/XLSX | `globalID2_data_download` `main` |
| R2 papers | Article HTML (en/zh/fr), card PNGs, `site-data/research/*` | R2 `globalid-papers` @ `papers.globalinfectiousdisease.com` |

## Release flow

1. Generate site data and build the full Astro `dist` (unchanged for local `npm run build`).
2. `scripts/split_papers_bundle.py` moves papers paths into `exports/papers-site/`, rewrites article HTML so shared assets point at the main origin, appends redirects/CORS, and enforces the Pages file budget.
3. Digest and deploy use the **slim** `dist` only.
4. `scripts/publish_papers_r2.py` incrementally syncs `exports/papers-site` to R2, in parallel with GitHub downloads / raw archive. It lists object metadata, verifies local SHA-256 checksums, and compares single-PUT ETags with local MD5 hashes. Matching objects transfer no body bytes, including objects left by an interrupted attempt.
5. Cloudflare Pages deploy + production verification, then dirty-marker commit.

The report directory and report pages use a separate R2 bucket and direct
custom domain, like this Papers channel. See [`REPORTS_HOSTING.md`](REPORTS_HOSTING.md)
for the object-key layout, publication, and cadence-based generation.

## Configuration

```text
DATA_RELEASE__PAGES_FILE_BUDGET=20000
DATA_RELEASE__PAGES_FILE_WARN=18000
DATA_RELEASE__R2_PAPERS_ENABLED=true
DATA_RELEASE__MAIN_SITE_ORIGIN=https://globalinfectiousdisease.com
R2_PAPERS_BUCKET=globalid-papers
R2_PAPERS_PUBLIC_BASE_URL=https://papers.globalinfectiousdisease.com
R2_ACCESS_KEY_ID=…
R2_SECRET_ACCESS_KEY=…
# optional; defaults to CLOUDFLARE_ACCOUNT_ID
R2_ACCOUNT_ID=
R2_ENDPOINT_URL=
# bounded concurrency, 1–32; persistent HTTP connection pool
R2_PAPERS_UPLOAD_WORKERS=32
```

Create an R2 S3 API token (Object Read & Write on `globalid-papers`) and put the
access key pair in `.env`. The Cloudflare account API token alone is enough to
list buckets for operations, but the publisher signs S3 PutObject requests with
the R2 access key.

## Control panel

Publish → preflight shows:

- Repository access (GitHub downloads)
- Hosting access (Pages)
- Papers storage (R2)
- Pages file budget
- Build tools / repository boundary

After preflight, a three-card channel strip summarizes Pages main, GitHub
downloads, and R2 papers readiness.

## Operator notes

- The splitter retains unchanged rewritten HTML only when its source hash,
  output hash, rewrite implementation and origins all match. Removed paths are
  reconciled locally; `papers-manifest.json` never includes itself.
- The uploader holds a per-bucket process lock, limits pending futures to twice
  the worker count, and retries transient transport/429/5xx failures. A failed
  upload drains in-flight requests without advancing the final manifest.
- Public verification precedes deletion of obsolete keys declared in the prior
  manifest. The new manifest is written last. This is a completion marker;
  independent objects are updated in place, not switched atomically as a site.
- Browser research shards use record-derived boundaries and content hashes in
  filenames, preserving the existing ordered-list client contract. The uploader
  finishes shard uploads before updating `research/index.json` and
  `research/catalogue.json`. Generation retains retired shards for 48 hours
  after their last active reference, including legacy numbered shards during
  migration, so cached indexes keep resolving. Cleanup remains manifest-scoped.
- When a release introduces new hashed `/_astro/` files, publish a compatibility
  Pages artifact containing the prior site plus those files before uploading R2
  HTML. Verify the new asset bytes and CORS on the public origin first. Unchanged
  assets skip this extra deployment. This prevents new R2 pages referencing
  unavailable styles while the final Pages deployment is still pending.
- The complete completion manifest retains its JSON schema and all fields, but
  uses a deterministic gzip HTTP representation when smaller. The S3 signature
  covers `Content-Encoding`, encoded length and encoded payload; readers accept
  old plain JSON and decoded or encoded gzip responses. Ordinary object bytes
  and their ETags are unchanged. R2 supports this metadata on
  [PutObject](https://developers.cloudflare.com/r2/api/s3/api/).
- Repeat a successful publication to measure an unchanged run: the summary
  should report `uploaded: 0` and `bytes_uploaded: 0`. Object listings still
  transfer metadata; no existing HTML, PNG or JSON bodies are downloaded.
- The public probe also uses HTTPX. It retries transient transport/429/5xx
  failures, falls back to a one-byte range GET when HEAD is unsupported, and
  retains the HTTP status in failure diagnostics. Authentication/403 failures
  remain terminal; verification is never skipped to complete a release.

- Main-site `_redirects` send `/research/articles/*`, locale variants, cards, and
  `/site-data/research/*` to the papers host with 301.
- Main-site `_headers` allow the papers origin to load `/_astro/*`, `/scripts/*`,
  and logos cross-origin. Papers pages also fetch `/site-data/*` and
  `/release.json` from the main origin (search index, release badge, site
  status), so those paths need the same CORS rule. The split step adds any
  missing path and leaves existing rules alone.
- R2 objects are uploaded with `Cache-Control` because a custom domain on R2
  does not read Pages `_headers`: HTML `max-age=0, must-revalidate`, JSON
  5 minutes, `papers-manifest.json` `no-store`, everything else one day.
- R2 cannot attach per-object security headers. Add a Cloudflare Transform Rule
  (Modify Response Header) on `papers.globalinfectiousdisease.com` that sets
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and
  `Referrer-Policy: strict-origin-when-cross-origin`. This is a dashboard
  setting, not part of the release, so check it once after the first publish.
- Article URLs without a trailing slash are redirected by the main site to the
  same path on the papers host, where the R2 key has the trailing slash. Add a
  Transform/Redirect Rule on the papers host that appends `/` to extensionless
  paths, or check that `/research/articles/<slug>` resolves after the first
  publish.
- Failed releases write `exports/release-failures/<task_uuid>/dist-inventory.json`
  before rolling back local artifacts.
- Literature-only releases still publish R2 papers; they may skip GitHub
  downloads and the raw archive.
