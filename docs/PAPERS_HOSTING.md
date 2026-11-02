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
4. `scripts/publish_papers_r2.py` incrementally syncs `exports/papers-site` to R2 (hash manifest, orphan delete, public sample verify), in parallel with GitHub downloads / raw archive.
5. Cloudflare Pages deploy + production verification, then dirty-marker commit.

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
