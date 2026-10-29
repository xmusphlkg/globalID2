# Situation automation

The Situation module turns official outbreak reports into map alerts: one
disease in one place, with the counts and dates the report states, one quoted
sentence and a link to the original. One shared runner serves the CLI
(`scripts/run_situation_notices.py`), the `refresh_situation_sources` task and
the data release (`src/services/situation_notices/runner.py`).

## Pipeline

1. **Collect.** Adapters in `src/services/global_sources/` list recent reports
   from WHO Disease Outbreak News, ECDC CDTR, Africa CDC, PAHO (IRIS) and the
   WHO WER/WPRO listings. Each report carries a `document_ref` saying where its
   body lives.
2. **Fetch full text.** `documents.py` downloads the body: WHO DON sections via
   the hub API, HTML pages, PDFs (text via pdfplumber) and PAHO DSpace items.
   PDFs are also kept as original files.
3. **Extract alerts.** An author model reads the whole document and drafts
   alerts; a different reviewer model audits the draft. Routes that read PDFs
   natively get the original file; others get extracted text. A route that
   rejects a file is retried as text and stays on text for six hours. Set
   `extra_params.runtime_native_files` to `true` or `false` on a model route to
   override the guess. Deterministic guardrails drop alerts whose quote is not
   in the document, whose counts are not in the document, or whose country or
   disease is not in the catalogue.
4. **Group outbreaks.** Alerts of one disease named by one report, or in one
   country within 60 days, form one outbreak. Routine alerts are not outbreaks.
5. **Export.** `export.py` writes `astro-site/src/data/situation/international`
   (schema `gids.situation_alerts.v1`): `map.json`, `outbreaks/`, `by-country/`,
   `by-disease/`, `weekly/`, `monitoring.json`, `sources.json`.

There is no human gate. An extraction is public once the reviewer accepts it.
Without a second model route the same model reviews itself and the output is
labelled `single_model`. `--skip-ai` stores and exports without model calls.

## Internal-only full text

Full report text and original PDFs are never published. They live in the
`situation_documents` table and the cache directory `data/cache/situation`
(git-ignored). The export holds only structured facts, the quoted sentence and
the link. A quote is published only for sources whose terms are cleared
(`metadata_and_extract` in `SOURCE_REGISTRY`); other sources export facts and
link without the quote.

## Release gate

```bash
venv/bin/python scripts/run_situation_notices.py
venv/bin/python scripts/generate_site_data.py
cd astro-site && npm run build:astro
cd .. && venv/bin/python scripts/validate_situation_release.py --site-dir astro-site/dist
```

The validator reads files only. It checks the schema version, source URLs,
quote rules, absence of full-text fields, that every map point appears on its
outbreak timeline, and that `/situation/` is built in en, zh and fr.

## Environment

- `DATABASE_URL`: PostgreSQL (`postgresql+asyncpg://...`).
- Model routes come from the Model Center; no extra secrets are needed. Add a
  second active route to get cross-model review.
- Migration `0021_situation_alerts` creates the tables and sends every stored
  report back to `pending` so it is read again from its full text.
