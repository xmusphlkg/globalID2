# Research Radar screening review

Review date: 2026-11-09. Counts are from a read-only local database snapshot;
the public catalogue was generated at 2026-11-09 09:46 UTC. Journal counts
below use exact stored journal names and do not include name variants. They are
not estimates of all literature available from publishers.

## Findings

1. **High: the first classifier treats lack of a named disease match as
   exclusion.** `classify_candidate` excludes when no disease is matched or
   relevance is below 0.5. Infection, diagnostics, vaccines, pathogens, AMR,
   surveillance, and treatment can be in scope without matching a disease in
   the configured catalogue. Unknown is being treated as out of scope.
2. **High: discovery ranking has been used as an exclusion criterion.** Its
   weighted components include recency (0.15), OA (0.05), and surveillance
   relationship (0.15), none of which proves that an article is irrelevant.
   Current `decide_article` holds low-scoring records with disease links, but
   stored automatic decisions include older low-score exclusions. Stored
   decision reasons and current scores need not describe the same evaluation.
3. **High: excluded records do not enter ordinary autopilot reconciliation.**
   `_article_reconciliation_statement` selects only review and published
   articles, including when explicit article IDs are supplied. Reclassification
   preserves publication status. Hence a policy change or improved classification
   alone does not guarantee previously excluded records are reconsidered.
4. **High: a published bibliography is gated on two published summaries.**
   The site export withholds records lacking both English and Chinese published
   summaries. Summary generation independently requires sufficiently long
   abstracts and OA evidence and filters certain study types, including
   systematic reviews and meta-analyses. This creates a structural blind spot,
   not proof that missing records are irrelevant. These gates must not be
   bypassed by sending restricted material to an external model.
5. **Medium: current state and historical decision metadata can disagree.**
   Among excluded exact-name records, 88 Infectious Diseases and 53 Microbe
   records have a stored autopilot decision of `hold`; 1,075 and 365 have no
   stored autopilot decision respectively. The metadata alone cannot establish
   why the current status was set or which decision is newer. Status-event
   history must be reconciled before changing any record.

## Local evidence

| Exact stored journal name | Excluded | No disease link | Relevance >= 0.5 | No abstract | Published status | Public catalogue |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| The Lancet Infectious Diseases | 3,046 | 1,200 | 1,846 | 1,882 | 474 | 171 |
| The Lancet Microbe | 760 | 389 | 371 | 506 | 170 | 69 |

The exclusion columns overlap and must not be summed. Among the excluded,
1,787 and 363 respectively currently have relevance >= 0.8. Classification
scores are not a human relevance judgment. The public and database counts are
from different timestamps; the difference is not attributable to one gate
without a record-by-record comparison.

Examples in the excluded set include DOI `10.1016/s1473-3099(25)00075-1`
(paediatric urinary tract infection treatment; no disease match),
`10.1016/s1473-3099(25)00307-x` (measles and rubella eradication; historical
low-discovery-score exclusion), and `10.1016/s1473-3099(24)00665-0`
(pneumococcal vaccine surveillance; historical low-score exclusion). These are
illustrative cases, not an estimate of the false-exclusion rate.

## Recommended correction and validation

- Separate relevance/in-scope classification from discovery ranking. Retain
  unknown candidates for review or a clearly labelled bibliographic tier;
  use recency, OA and surveillance association only to rank.
- Use pathogen, infection, vaccine, diagnostic, AMR and surveillance evidence,
  in addition to disease catalogue links. Journal scope is a recall signal,
  not proof of article relevance or scientific quality.
- Design a resumable, bounded re-evaluation of historical exclusions, with
  a dry run, decision provenance, status-event history, integrity checks and
  editorial locks preserved. Avoid a full-corpus bulk re-publication.
- Consider a clearly identified metadata-only catalogue tier using permitted
  bibliographic fields; keep full evidence pages behind integrity and summary
  quality gates. Evaluate source terms and model-processing rights separately.
- Build a stratified, manually adjudicated set across both journals, score bands,
  diseases, article types and years. Report recall and false positives, then
  run a shadow comparison and a bounded canary before any release. Include
  regression cases for the examples above and tests for reopening previously
  excluded records without overriding editorial or integrity decisions.

No screening rule, database status, or public release was changed by this
review. A material policy change requires the re-evaluation and validation
described above to avoid replacing one unmeasured error with another.
