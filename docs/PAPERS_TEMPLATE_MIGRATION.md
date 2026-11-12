# Papers JSON template migration (staged, not yet active)

The current Papers site serves one pre-rendered HTML file per article and locale. A shared footer or stylesheet change therefore changes thousands of files. This candidate factors each already-public page into a per-locale shared HTML shell and one JSON record per article containing the public head and main fragments for all three languages. A Worker assembles the same HTML server-side, preserving canonical URLs, metadata and visible content. It is not a semantic export of the internal article model; it contains only content already present in the public page.

The initial 2026-11-12 audit reconstructed all 24,579 pages byte-for-byte using three templates. A 100-paper sample of the combined multilingual records reduced compressed article bytes by approximately 64%. These are local measurements, not account-wide billing or request-latency estimates.

## Safety boundaries

- The existing static site remains the source of truth until the full pipeline and public canary are approved and verified. Do not delete old objects while fallback or cached pages may need them.
- The preparer accepts only a separate new output directory, validates every round trip, requires one compatible shell per locale, and leaves no output on failure.
- Candidate records, index shards, templates and generation manifests are addressed by SHA-256; the only mutable key is `paper-template-v2/current.json`. Stage immutable objects before the conditional pointer switch. No R2 deletion is performed.
- The Worker verifies each object hash, bounds decoded object sizes, preserves full HTML for crawlers, and falls back to the previous static R2 page if a template or record is absent or damaged. It currently has **no configured public route**.
- Keep old generations and old static objects until an explicit rollback and retention policy has been established and production-verified. Adding a `papers.globalinfectiousdisease.com` Worker route needs the corresponding Cloudflare zone permission; the current production token returned HTTP 403 for the routes API.
- Existing automatic site and literature releases still use static HTML by default. After a production canary and initial cutover, setting `R2_PAPERS_TEMPLATE_MODE=1` for the release worker enables generation and staging of immutable template objects, publication of assets while retaining static fallbacks, and a conditional pointer switch under the existing publication lock. This does **not** yet eliminate full article rendering. Verify the complete production pipeline, caching and rollback before enabling it; keep old objects until a safe retention policy exists.

## Local preparation and validation

```bash
venv/bin/python scripts/prepare_papers_templates.py --source exports/papers-site --output exports/papers-template-candidate
venv/bin/python -m pytest -q tests/unit/test_papers_template.py tests/unit/test_prepare_papers_templates.py tests/unit/test_papers_templates_publish.py tests/unit/test_papers_r2.py
cd cloudflare/papers-json
npx wrangler deploy --dry-run --outdir .wrangler/test-bundle
node --test tests/runtime.test.mjs
```

`publish_papers_templates.py --candidate <path>` stages immutable objects only. The `--activate` flag also switches the pointer and must not be used before a verified canary, complete candidate checks and concurrency/rollback planning. Storage is conservatively budgeted while both old and new objects coexist. A template-only change keeps unchanged records and index shards byte-identical; a test exercises this invariant.
