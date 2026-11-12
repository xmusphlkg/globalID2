# Papers JSON templates and unified research publication

## Deployment status

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

The research-only `globalid-papers-template` Worker reuses the catalogue reader from `globalid-surveillance-data` for stable research URLs and `/research-publication/<generation>/...` URLs. It validates bounded, decoded JSON against its content hash, assembles the HTML, verifies the original HTML digest and size, then applies generation-specific asset/link rewriting. Canonical and social metadata remain stable. Responses identify template rendering with `X-GIDS-Paper-Mode: template` and expose the verified original digest in `X-GIDS-Paper-SHA256`.

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
2. Prepare and validate the unified candidate, reuse verified staged objects, and publish every referenced payload. Verify the immutable-generation URLs, deploy compatible canonical Worker routes, then switch the research pointer conditionally.
3. Verify template mode, original article digest and release identity in all three languages on the main, `www` and Papers hosts. Validate the complete article coverage before deleting any old page.
4. Use `scripts/cleanup_legacy_papers.py` with its reviewed exact-key plan. It requires every replacement article's digest and size to equal the preserved original, checks public canaries, conditionally prunes the canonical static manifest, and checks the publication pointer before each deletion batch. Durable checkpoints make interrupted cleanup resumable.

Cleanup removes only the covered legacy static article keys and audited inactive v1 prototype objects. Shared assets, research data and historical immutable generations remain because cached or versioned links may still use them. A pointer change stops further deletion. All static publishers must remain fenced during cleanup; a manifest check alone cannot exclude unfinished uploads.

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
