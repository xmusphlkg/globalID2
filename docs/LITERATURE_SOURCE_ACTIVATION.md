# Literature source activation

The Research Radar pipeline keeps every added source bounded and metadata-only.
Crossref remains the required core source. Springer Nature, Elsevier, publisher
RSS, and bioRxiv/medRxiv are optional discovery sources; one optional provider
failing does not stop the other providers or the core ingest.

## No-credential publisher policy

PubMed is a biomedical index, not a complete Springer Nature or Elsevier
catalogue. A PMID therefore remains enrichment evidence and must not be used as
the sole discovery gate. For the current curated-journal scope, however,
Crossref already supplies the required discovery metadata and Europe PMC adds
PMID/PMCID and biomedical fields when available. Springer Nature and Elsevier
APIs are not required merely because a stored record lacks a PMID.

Run the aggregate read-only audit before pursuing publisher credentials:

```bash
PYTHONPATH=. venv/bin/python scripts/audit_literature_publisher_coverage.py \
  --as-of 2026-08-27 --recent-days 365 --pretty
```

The command starts a read-only database transaction, makes no network request,
does not write a checkpoint, and emits counts only. It reports all-time and
recent DOI, PMID, Europe PMC, Crossref, and OpenAlex coverage for the overall
corpus, Springer Nature, Elsevier, and their leading journals. A zero
`core_provenance_gap` does **not** prove complete publisher-catalogue coverage:
articles absent from every configured source are unobservable without an
external catalogue.

The 2026-08-27 audit found all 33,425 stored Springer Nature records and all
9,796 Elsevier records had DOI and Crossref provenance. In the preceding 365
days (excluding future-dated metadata), PubMed/PMID covered only part of each
publisher's stored records, while Crossref covered all of them. The safe policy
is therefore:

1. keep Crossref as required discovery;
2. keep Europe PMC/PubMed as biomedical identifier and metadata enrichment;
3. keep both credential-gated publisher APIs disabled while credentials are
   unavailable and the audit returns
   `core_sources_sufficient_for_curated_scope`;
4. investigate Crossref/RSS/query scope first if a recent core provenance gap
   appears, and request publisher credentials only for a documented catalogue
   gap or required publisher-only field.

## Credential-gated publisher APIs

Both publisher APIs are disabled by default and make no request unless their
feature flag is enabled and a non-empty API key is present:

```dotenv
LITERATURE__SPRINGER_NATURE_ENABLED=true
LITERATURE__SPRINGER_NATURE_API_KEY=...
LITERATURE__ELSEVIER_ENABLED=true
LITERATURE__ELSEVIER_API_KEY=...
# Optional, only when supplied by the institution:
LITERATURE__ELSEVIER_INSTITUTIONAL_TOKEN=...
```

Only search metadata is normalized. The clients do not request or retain
publisher abstracts, HTML, PDFs, full text, or text-mining content. API keys and
institutional tokens are never stored in source payloads, counts, or checkpoints.
Operator-provided queries and per-run limits can be set with the corresponding
`LITERATURE__*_QUERY` and `LITERATURE__MAX_*_RECORDS` settings.

## bioRxiv and medRxiv

Enable official public-API discovery with:

```dotenv
LITERATURE__PREPRINT_DISCOVERY_ENABLED=true
LITERATURE__MAX_PREPRINT_RECORDS=100
```

Every record is explicitly normalized as a preprint. A hard pipeline gate holds
it in editorial review even if its discovery score would otherwise qualify for
automatic publication. A later peer-reviewed DOI is retained as a version
relation when the API supplies it. The client never follows JATS, HTML, or PDF
links.

## Publisher RSS readiness

RSS stays disabled by default. Validate the reviewed HTTPS whitelist without
network access:

```bash
PYTHONPATH=. venv/bin/python -m src.literature.rss_readiness \
  configs/literature/publisher_feeds.json
```

Run a metadata-only dry-run probe before activation:

```bash
PYTHONPATH=. venv/bin/python -m src.literature.rss_readiness \
  configs/literature/publisher_feeds.json --probe
```

The probe emits bounded health evidence (enabled feed IDs, success/failure
counts, records seen, and probe time) and does not write a database row or a
checkpoint. Activate production polling only after `ready` is `true`:

```dotenv
LITERATURE__PUBLISHER_RSS_ENABLED=true
```

Feed configuration must contain at least one enabled, unauthenticated HTTPS URL
whose host is explicitly present in `allowed_hosts`. Redirects are revalidated
and cannot downgrade to HTTP or leave the allowlist.
