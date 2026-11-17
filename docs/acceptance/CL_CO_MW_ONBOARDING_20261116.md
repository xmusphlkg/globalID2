# CL, CO and MW source onboarding acceptance

Date: 2026-11-16. This records internal source-native ingestion. Public release is disabled for all three countries; no country is promoted to a validated public disease snapshot by this work.

| Country | Captured source and imported facts | Independent check | Remaining boundary |
| --- | --- | --- | --- |
| CL | MINSAL ENO CSV: 333,300 case rows from 2007–2024, aggregated to 442 annual observations; 5 original artifacts | `data/current/cl/database_acceptance.json`: 442 DB rows, sum 333,300, zero row differences | CKAN description disagrees with the current CSV scope; disease mapping and public snapshot review remain |
| CO | INS SIVIGILA SODA: 2,465,826 municipality/event/week rows published for 2007–2022; all 254 year/page batches are imported | `data/current/co/database_acceptance.json`: all 16 years and 2,465,826 rows pass full source-record and physical-identity comparison, with zero missing, extra, duplicate or mismatched rows | Source week boundaries and completeness for a national aggregate are unverified |
| MW | PHIM IDSR posts and media catalogue: 91 artifacts including 57 PDFs; 56 in-window reports yield 1,241 national Table 1 observations from 2025-02-16 through 2026-10-25, across 28 holding series | `data/current/mw/database_acceptance.json`: all 1,241 source rows match DB, with zero missing, extra, duplicate or mismatched identities/values | Missing official weeks remain; failed old post links were recovered from official media. Malaria, district annexes and cumulative narratives are outside this contract |

Country, scope, disease-source and update-channel registration is in `configs/country_bootstrap.json`, `src/core/country_library.py`, `src/core/source_scopes.py` and `configs/surveillance_channels.json`. The authenticated control-panel configuration and source-flow check is saved in `exports/priority-country-updates-20261116/live-source-config-final.json`; channels are checked against their configured enablement policy. CO was enabled after full historical and live metadata-gate acceptance. The API and worker were reloaded after the code update. This proves configuration visibility, not a completed scheduler-triggered run of the new jobs.

The CO import is deliberately separate from daily checks. The historical controller completed all 254 batches in `data/current/co/history-state.json`. Full-row verification and the live metadata gate passed; the CO daily metadata job is now enabled. A new source version must be replayed and independently checked before it can be treated as current. CL and MW jobs are enabled, with a healthy worker and scheduler; their first scheduler-triggered executions remain to be checked.

Focused shared onboarding and update tests: 59 passed. Final CO scheduling and metadata-gate regression: 52 passed. Source-specific tests and the completed full-row reconciliation are recorded in each country guide and acceptance receipt.

Final CO acceptance: 2,423,515 municipality observations, 29,752 unknown-location observations and 12,559 exterior observations. The live metadata-only check is recorded in `data/current/co/update_check_acceptance.json`; job enablement is recorded in `exports/priority-country-updates-20261116/co-automation-enabled.json`. A changed dataset requires explicit full replay; no automatic full-backfill launch is claimed.
