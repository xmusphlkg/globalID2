# Situation host metadata — phase 1

Implemented locally on 2026-11-15. This change adds source-supported host labels
to the existing official-notice pipeline; it does not establish a direct WOAH
connector or a cross-species transmission inference.

Database follow-up: revision 0032 was applied on 2026-11-16 at 07:49 Asia/Shanghai;
see the [migration acceptance](../acceptance/SITUATION_HOST_MIGRATION_20261116.md).
The implementation-time boundaries below remain historical; backend loading,
re-extraction and public publication were not performed by that migration.

## Data contract

`SituationAlert`, the public `gids.situation_alerts.v1` alert objects and the
control-plane alert dictionary now carry:

| Field | Values and meaning |
| --- | --- |
| `host_type` | `human`, `animal`, `unknown`; missing legacy fields mean `unknown`. |
| `host_species` | Source-language animal host phrase, or null. It is not a normalized taxonomy ID. |
| `count_unit` | `people`, `animals`, `unknown`; applies to both case and death figures. |
| `host_evidence` | Short verbatim support from the alert's quoted sentence, or null. Withheld from blocked sources; never present on map markers. |

Extraction prompt `situation-alert-extract.v4` asks the independent author and
reviewer to split human/animal events and different animal hosts when the report
separates their figures. Outbreak/premises, sequence and culling counts must not
be put into case/death fields.

Deterministic checks require quoted support for a declared host and an exact
source phrase for animal species; mismatched count units remain unknown. They
do not derive host type from the disease catalogue. Animal classification is
also withheld when the quoted sentence refers to human patients/cases: an
animal exposure in a patient's history is not an animal case. The host vocabulary
is conservative, so rare species, other languages and mixed-host sentences may
remain unknown. Exact text matching and the vocabulary are not a general proof
of affected-host roles or of which number belongs to which population; the
independent source review remains necessary.

## Public behavior

The map has an affected-host filter, shared via `?host=human`, `?host=animal` or
`?host=unknown`. Missing fields remain in the unknown selection. Monitoring
roundups without per-event host metadata are withheld under any host filter.
Counts, timeline badges and entity panels distinguish people, animals and
unknown units in English, Chinese and French. Human surveillance snapshots are
attached only to explicitly human events. Animal/unknown selections do not use
human case values as map shading.

Host type and species participate in alert deduplication and outbreak grouping.
A unique compatible old/new alert retains its existing `alert_id`, including
subsequent re-extractions, which also preserves email idempotency. A split or
changed explicit species receives distinct identities. Rebuilding host-separated
outbreaks can change `outbreak_id`; this is grouping, not evidence of transmission.

## Release steps and remaining verification

1. Apply migration `0032_situation_host_metadata` before loading backend code
   that selects `SituationAlert`. Existing rows receive `unknown`; the migration
   performs no host classification.
2. Load the backend and public templates through the existing release workflow.
   The v4 prompt version causes the existing stale-prompt requeue mechanism to
   schedule source re-extraction; it can consume model capacity. Old published
   readings remain available until an accepted new reading replaces them.
3. Verify actual human cases, actual animal cases, a patient with poultry
   exposure, a mixed report and an ambiguous report. Check the original quoted
   support, units, species, map filter, entity panels and all three locales.
4. Publish Situation JSON through its independent channel and check public
   markers/timelines and the RSS feed use that same generation. Run the release
   validator; it checks host/unit consistency and marker/timeline agreement.

No production migration, re-extraction, publication or email delivery was run
for this local implementation. The repository's existing `schema.sql` snapshot
does not contain the Situation tables; it was not regenerated wholesale here.
Alembic migration 0032 and ORM metadata define the additive schema change. The
standalone `generate_schema.py` default connects to a database and calls
`create_all`, so it should not be used as an offline validation command.

Local coverage includes quoted-evidence checks, exposure ambiguity, host splits,
repeated identity preservation, export withholding, host-separated map records,
SQLite migration upgrade/downgrade, release validation, host filtering and
three-language labels. Production data review and browser interaction remain
release acceptance work.
