# Research Radar backlog governance

The governance command is read-only by default. It reports aggregate reason
and age distributions, duplicate indicators, deterministic policy outcomes,
the projected actionable backlog, and a content-bound plan hash. It never
prints article identifiers, titles, URLs, or review notes.

```bash
venv/bin/python scripts/govern_literature_backlog.py \
  --article-min-score 0.60 \
  --max-projected-backlog 500 \
  --no-export
```

The calibrated threshold does not weaken any integrity, bibliographic,
disease-confidence, evidence-trace, fingerprint, or summary-quality gate. It
closes the previously oversized `0.60 <= discovery_score < 0.70` band only
when all other automatic publication gates pass.

An operator must review a fresh dry-run report before applying it. Applying
requires the exact `plan_sha256` from that report and fails closed if the live
projection has changed, the projected backlog exceeds 500, or more than the
allowed number of rows would transition:

```bash
venv/bin/python scripts/govern_literature_backlog.py \
  --article-min-score 0.60 \
  --max-projected-backlog 500 \
  --max-changes 5000 \
  --apply \
  --confirm-plan-sha256 '<fresh-plan-sha256>'
```

Run this during the documented task-runtime maintenance window so ingestion
cannot change the plan between projection and application. Do not resolve the
remaining queue by bulk deferral. Corrected-integrity articles, relevance or
disease-confidence gray-band articles, low-quality summaries, stale source
fingerprints, and insufficient evidence traces remain human decisions.
