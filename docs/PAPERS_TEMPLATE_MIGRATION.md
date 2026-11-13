# Papers JSON templates and unified research publication

## Deployment status

The stable public URL policy was deployed on 2026-11-12 with site release
`20261112T144553Z-13ed03c39c6e` (task `d4e8f489-2e9f-4e10-adb1-0658583e3036`)
and Papers Worker version `145915b0-c375-45ec-ae87-8e638ada000e`.
Main Pages, Papers and Reports were republished. Live checks confirmed three
article locales return 200 at short canonical URLs, removed snapshot article
URLs return 410, research navigation contains no snapshot page links, and
versioned scripts/styles still return 200. The research sitemap uses the Papers
canonical URLs. API, workers, scheduler, dashboard web and local site processes
were restarted; interrupted tasks were recovered by their stopped owner IDs.

The shared-paper structure was activated on 2026-11-12 as `template-cutover-20261112`, using the same research publication pointer for full-site and independent literature updates. The research-only Worker serves 24 routes across the main, `www` and Papers hosts. Checks passed for all nine canonical host/locale article combinations, three versioned article locales, three versioned research homepages and nine referenced assets. The research homepage also passed a browser check without console errors. The fenced cleanup completed after those checks: 24,579 replaced static article objects and 2,146 inactive v1 prototype objects were deleted (26,725 total, 238,943,659 stored bytes). Shared assets, research data and historical immutable releases were retained.

- Catalogue generation: `89b185ac744d79d60c1c303bf712405120ad46b669fcdd7483e8ec978ff41d74`.
- Publication pointer SHA-256: `67252622c3ca37b6ddd70a59c983cb1ade21fa191dd63315100ac408f054d56e`.
- Worker version: `8e65f5d5-a8df-46ec-9cf9-7238b6e78267`.
- Final activation reused all 47,365 content objects and 418 public JSON artifacts; it uploaded no content payloads.

## Storage and serving

Each article is stored as one content-addressed JSON record containing the already-public head and main fragments for English, Chinese and French. Three shared locale templates supply the surrounding HTML. This is a representation of public HTML, not an export of private database records.

The only live research publication pointer is:

```text
site-data/research-publication/current.json
  -> releases/sha256/catalogues/<generation>.json
     -> releases/sha256/catalogue-shards/<sha256>.json
        -> paper-template-v2/records/<sha256>.json
        -> paper-template-v2/templates/<sha256>.json
```

The catalogue remains schema 2. Article entries retain their original `logical_key`, `path`, HTML `sha256` and HTML `bytes`. Their `key` references the shared record, and `paper_template` contains `template_sha256`, `record_sha256`, `locale` and `slug`. Other assets keep their existing content-addressed object entries.

The research-only `globalid-papers-template` Worker reuses the catalogue reader from `globalid-surveillance-data` for stable research URLs. It validates bounded, decoded JSON against its content hash, assembles the HTML, verifies the original HTML digest and size, then pins supporting data and assets to the page's generation. Responses identify template rendering with `X-GIDS-Paper-Mode: template` and expose the verified original digest in `X-GIDS-Paper-SHA256`.

Public article and disease-literature links, canonical metadata, language alternates, feeds and sitemaps use the stable `https://papers.globalinfectiousdisease.com/[locale/]research/...` addresses. Research landing and other collection pages use the main host. The publication-mode flag controls hosting and build routing; it does not change article canonical URLs. Client-side cards and graph links follow the same short-URL policy.

Snapshot **page** URLs under `/research-publication/<generation>/...` are removed with `410 Gone`, without a redirect or static fallback. Only supporting JSON, scripts, styles, images and fonts retain versioned URLs. They are needed by pages already open during a publication switch. Storage generations remain available for internal resolution and rollback; removing public snapshot pages does not delete their backing objects.

Release rebuilt research pages/client assets before the Worker change: old client bundles still generate snapshot page links. Validate GET and HEAD on removed page URLs, stable navigation in all three locales, sitemap/canonical agreement, and versioned resource loading.

The earlier standalone prototype reader was replaced. `paper-template-v2/current.json` is not the production publication path: do not activate a second pointer. The staged record/template keys are reused by the unified catalogue.

## Publishing both kinds of update

Full-site publication captures the research pointer baseline before export and rendering. It freezes a private bundle containing the split Papers content, locale research landing pages and required assets, and separately freezes the public research JSON projections. `scripts/publish_papers_r2.py` publishes this candidate through `publish_site_research`.

Independent literature publication retains its existing frozen snapshot and baseline. `scripts/publish_research_candidate.py` prepares JSON templates under the run's `paper-templates` directory. A previously frozen legacy run retains its original binding only for idempotent recovery; its old baseline cannot overwrite a newer publication.

Both paths use `publish_literature_candidate`: upload immutable records/templates and other assets, publish catalogue shards and root, then conditionally switch the single research pointer. Publication failure leaves the prior pointer intact. A crash after the switch can resume acknowledgement without uploading everything again. The shared committed receipt is `data/cache/isolated-research-publication.json`; it includes both record and template keys.

Unchanged objects are reused from a receipt bound to the committed generation. During migration, `verified_existing_objects(candidate, inventory)` can prove reuse from exact wire size and MD5 ETag for either raw bytes or deterministic gzip. An object name alone is not proof. Reusing an existing local template candidate also reconstructs every article against the frozen HTML manifest, rejecting stale or mismatched input.

A style-only change leaves article JSON unchanged; a single article update changes one combined record. Shared-template changes still update catalogue shards because those retain original rendered HTML hashes and template references. This removes repeated article-body uploads, but does **not** eliminate full local article rendering or every metadata upload.

The initial 2026-11-12 audit reconstructed 24,579 pages exactly. Gzip level 6 compressed the old HTML pages to 230,207,626 bytes and the 8,193 multilingual records to 85,773,356 bytes, excluding small templates/catalogue metadata. These are local representation measurements, not account-wide storage or request-latency estimates.

## Cutover, cleanup and recovery

1. Fence publication workers during the final cutover/cleanup window. Preserve the old pointer, canonical manifest, object inventory and a separate SHA-verified copy of every static article.
2. Prepare and validate the unified candidate, reuse verified staged objects, and publish every referenced payload. Verify immutable data/assets and the staged article assembly, deploy compatible canonical Worker routes, then switch the research pointer conditionally.
3. Verify template mode, original article digest and release identity in all three languages on the main, `www` and Papers hosts. Validate the complete article coverage before deleting any old page.
4. Use `scripts/cleanup_legacy_papers.py` with its reviewed exact-key plan. It requires every replacement article's digest and size to equal the preserved original, checks public canaries, conditionally prunes the canonical static manifest, and checks the publication pointer before each deletion batch. Durable checkpoints make interrupted cleanup resumable.

Cleanup removes only the covered legacy static article keys and audited inactive v1 prototype objects. Shared assets, research data and historical immutable generations remain for open pages and rollback. A pointer change stops further deletion. All static publishers must remain fenced during cleanup; a manifest check alone cannot exclude unfinished uploads.

After static deletion, rollback to a retained immutable research generation must keep compatible Worker routes. Removing those routes requires restoring the preserved static originals first. Do not delete retained generation assets as part of this migration.

## Focused validation

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_papers_content.py \
  tests/unit/test_literature_publish.py \
  tests/unit/test_site_research_publish.py \
  tests/unit/test_papers_reuse.py \
  tests/unit/test_papers_cleanup.py

cd cloudflare/surveillance-data
npx tsc --noEmit
node --experimental-strip-types --test tests/paper-template-content.test.ts
node --test tests/runtime.test.mjs
```

The runtime suite also accepts `GIDS_PAPERS_CANDIDATE` and `GIDS_PAPERS_SOURCE` to verify real staged English, Chinese and French article output. Deployment and deletion require their own saved production verification and cleanup receipts.

## Opt-in structured projection protocol v3 (2026-11-13, not deployed)

### Production qualification decision, 2026-11-13

Keep v2 active for the production rollout. The compatible dual reader may be
deployed with `PAPERS_PROJECTION_V3=0`; do not enable the publisher's
`GIDS_PAPERS_PROJECTION_V3` flag or switch the research pointer to a v3 candidate.
The current requirement is unchanged visitor content, styling, links and
interactions, and the initial structured renderer does not meet that requirement.

The qualification compared three real articles in all three locales against
the current public v2 release `20261112T152513Z-d1d67eca1c36`. All nine canonical
URLs and their seven evidence paragraphs matched. The comparison nevertheless
found concrete differences: title truncation, missing `x-default` and regional
hreflang forms, Open Graph/Twitter metadata, MedicalWebPage/BreadcrumbList JSON-LD,
breadcrumb and publication-version notices, the open-access badge, metadata
labels/order, source-link labels and selection, editorial disclosure wording,
and relationship explanatory text. Related-article lists also differed between
the frozen candidate and production; snapshot drift must be separated from
renderer behavior before those lists can be considered equivalent.

Browser checks covered en/zh/fr at 1440 and 390 pixels for the article with
Situation context, in both production v2 and local v3. Both retained all seven
evidence sections and one expandable Situation group with three events, with
no horizontal overflow or page errors. The main layout and notices visibly
differed; successful interaction and content-count checks are insufficient
evidence of visual equivalence. Some text-only Situation differences were
whitespace between adjacent elements, not missing events.

Saved evidence is in `exports/papers-v3-qualification-20261113/`:
`qualification.json`, nine complete HTML/semantic comparison pairs,
`browser-comparison.json`, twelve full-page screenshots, and two side-by-side
desktop/mobile views. This sample disqualifies activation; it is not a complete
article-inventory proof. Activation remains blocked until the renderer preserves
the existing public contract and a frozen production candidate passes complete
coverage plus representative visual/interaction checks. No production pointer,
object, Worker or service was changed by this qualification.

The first remediation pass (renderer revision
`gids-evidence-v1.20261113.1`) now emits the missing regional and `x-default`
alternate links, Open Graph/Twitter fields, MedicalWebPage/ScholarlyArticle/
BreadcrumbList JSON-LD, visible breadcrumbs, the open-access badge, integrity
and publication-version notices. The original comparison receipt remains the
baseline evidence; these changes have passed the projection unit and Worker
runtime fixtures but have not yet been re-run against the complete frozen
article inventory. The v3 production gate therefore remains off.

The reader now supports the existing v2 contract and an explicitly gated v3
contract. Both Workers default `PAPERS_PROJECTION_V3` to `"0"`. Existing v2
entries still reconstruct and verify their exact original HTML SHA-256 and byte
count. A v3 entry must declare `paper_template.schema: 3` and
`integrity: "independent-v3"`; it deliberately has **no legacy HTML `sha256` or
`bytes` fields**. Its independent `record_sha256`, `template_sha256` and
`assets_sha256` identify bounded, hash-verified immutable JSON objects below
`paper-template-v3/{records,templates,assets}/`.

The schema-2 catalogue and the single
`site-data/research-publication/current.json` pointer are unchanged. There is no
v3 publication pointer. The existing publisher uploads dependencies and catalogue
shards before the existing conditional pointer commit. Retained v2 and v3 objects
remain available for rollback; preparation never deletes published objects.

V3 records come directly from the already-public article export, using an
explicit field projection. They contain structured metadata, all seven localized
evidence sections, locale-specific editorial provenance, source references,
integrity history, versions, public surveillance links and evidence relationships.
They contain no template or CSS versions and no full rendered HTML. Text is
escaped by the Worker renderer; source links accept HTTPS or safe local paths,
JSON-LD escapes HTML delimiters, and nonpublic Situation links are excluded.
Unknown peer-review status is not labelled a preprint. Editorial approval in one
language does not approve another language.

The initial migration converts one **verified existing v2 shell per locale**,
retaining its static header/footer, compiled resources and scoped CSS attributes.
It does not split every new article from newly rendered HTML. The versioned
`gids-evidence-v1` renderer generates head and main fragments from each structured
record. Its response cache key includes renderer revision, catalogue generation,
record/template/assets digests, locale and logical path. Change
`PROJECTION_RENDERER_REVISION` whenever renderer behavior changes. Stable page
responses still read the current research pointer and use `no-store`; only
immutable dependencies and the internal generation-specific response are cached.

### Prepare and verify a candidate

Initial migration requires the exact release ID and verified build manifest of
the v2 baseline. The complete article catalogue must be covered; missing locales,
new routes and missing compiled dependencies fail closed.

```bash
venv/bin/python scripts/prepare_papers_projection.py \
  --source-root /absolute/frozen/research-stage \
  --build-manifest /absolute/frozen/papers-build-manifest.json \
  --legacy-templates /absolute/frozen/paper-templates \
  --articles-dir /absolute/public-snapshot/build/research/articles \
  --base-release-id VERIFIED_BASELINE_RELEASE \
  --release-id NEW_RELEASE --output /absolute/new/v3-candidate

node --experimental-strip-types astro-site/scripts/verify-papers-projection.mjs \
  --candidate /absolute/new/v3-candidate/candidate.json \
  --output /absolute/new/v3-preview
```

The verification command uses the actual Worker reader and checks every selected
page, SEO metadata and the complete localized evidence section values. Add
`--slug EXISTING_SLUG` for a focused check. Its HTML hashes are verification
receipts, not a new catalogue coupling between content and templates.

A subsequent body update needs only its previous frozen candidate and changed
public article JSON. It does not invoke Vite or Astro or read old article HTML:

```bash
venv/bin/python scripts/prepare_papers_projection.py \
  --previous-candidate /absolute/previous/candidate.json \
  --article /absolute/public-snapshot/build/research/articles/EXISTING_SLUG.json \
  --release-id NEW_RELEASE --output /absolute/new/body-candidate
```

Changing only compatible locale shells accepts `--templates shells.json` instead
of `--article`; the JSON maps `en`, `zh` and `fr` to v3 template objects. Existing
records retain their hashes. This is lightweight catalogue assembly, not a claim
that arbitrary Astro template changes avoid compilation.

### Existing publisher integration and boundaries

`scripts/publish_research_candidate.py --projection-candidate .../candidate.json`
accepts the frozen candidate only when `GIDS_PAPERS_PROJECTION_V3=1`. All existing
run, snapshot, stage, release and receipt arguments remain accepted; the stage
path is not read on this explicit path. The source candidate's base generation
must equal the baseline captured for the run. The reader must already have been
deployed with compatible v3 support before enabling candidate publication.

This initial fast path is deliberately limited to existing-article **summary and
updated-at changes** and compatible template updates. Changes to title, sources,
integrity, relationships, identity or routes require the regular directory/SEO
projection build. The publisher additionally compares the frozen research index
file digests to the captured publication: changed index/directory projections
reject the fast path. It cannot silently reuse old directory HTML for new index
content. The existing scheduled build remains the default; automatic selection
of the v3 fast path is not enabled.

After committing through `publish_literature_candidate`, the opt-in caller checks
public canonical canaries in all three languages against release, record,
template and asset identities and the body record marker. Changed articles are
preferred for these canaries. A failed public check leaves the committed run
recoverable and does not write `published.json`; retry reuses the committed
candidate without reuploading it and repeats verification. This adds no second
commit mechanism. The existing v2 retry behavior is unchanged.

Rollback restores a retained research generation with the same baseline-aware
publication mechanism. Keep the dual reader enabled until no live or retained
v3 generation needs it; changing the flag to zero while a v3 generation is live
fails closed with 503, so it is not a rollback procedure.

### Validation evidence and remaining rollout work

Local tests cover old full-HTML checksum rejection, v3 hash corruption, disabled
gates, cache isolation across template/assets/locales, escaping, locale approval,
unknown peer review, stable topic paths, body-only scope guards, the existing
single-pointer commit and recovery after public-verification failure. A workerd
runtime test also verifies canonical HTML rewriting, pinned CSS and the existing
410 behavior for removed snapshot pages.

Two real public article projections were migrated into six locale pages. A
single-summary edit generated one record and changed its three locale catalogue
entries; the other record remained unchanged. Preparation recorded zero Astro
builds, HTML assembly and content rendering. Separate verification assembled six
pages. Desktop (1440 px) and mobile (390 px) browser checks in en/zh/fr confirmed
seven full evidence sections, canonical and three alternates, ScholarlyArticle
JSON-LD, source links, editorial disclosure and no horizontal overflow. These
are local checks, not production migration or global cache propagation evidence.
A third real public article verified its Situation group and three source events
in all three locales at both widths; each native details control expanded
successfully. Evidence receipts and screenshots are saved locally under
`exports/papers-projection-validation-20261113/`.

The shared shell and visual system are preserved, but the new body renderer and
localized relationship labels still require representative editorial/visual
review before a complete production migration. Situation signal presentation reuses the existing `groupResearchSignals` helper
and preserves grouped expandable details, deduplicated source events, geography,
latest dates and change percentages. Exact, historical and disease-only context
remain distinct; private signals are omitted. The production gate stays
off. The full migration must validate all article coverage and public resources,
then capture publication timing and rollback evidence before retirement of v2.
