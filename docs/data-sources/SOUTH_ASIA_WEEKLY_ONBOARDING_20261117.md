# India, Pakistan, Nepal and Sri Lanka: historical backfill acceptance

This is an internal source integration and historical backfill, not a public
data release. The four country implementations are connected to the existing
staged ingestion runner, source taxonomy, country bootstrap, ontology source
registry, dashboard source labels, archive replay and saved official channels.
Default scheduling and public release are disabled for all four.

| Country | Source scope | Verified numerical capability | Outstanding coverage |
| --- | --- | --- | --- |
| India | `in_idsp_weekly` | **878** verified outbreak-event snapshots from 2020 (628) and 2026 (250) official reports | Routine S/P/L data unavailable publicly in the verified archive; older/different PDF layouts and late-report annexes remain unparsed. Snapshots are not weekly incidence. |
| Pakistan | `pk_nih_idsr_weekly` | **3,606** numeric observations from 204 official PDFs (2021–2026) | 482 `NR` cells remain unknown and are not inserted or coerced to zero; calendar boundaries are unverified and deaths are not inferred. |
| Nepal | `np_sormas_weekly` | **1,080** observations from all 15 indexed 2026 reports, nine categories and eight geographies | Calendar boundaries, reporting definitions, network completeness and reuse terms require review. EWARS remains a distinct researched source. |
| Sri Lanka | `lk_epid_wer_weekly` | **864** observations from two verified 2007 Table 5 PDFs recovered through Wayback (432 each) | Current official pages return HTTP 500/509; later layouts remain unparsed and archive replay reuse terms require review. |

All adapters preserve source provenance and explicit zero/missing distinctions.
No placeholder cases, missing weeks, invented deaths or combined S/P/L totals
have been created. Unreviewed source categories use nonprojectable holding
series. The verified rows above are in the shared source-series ledger with
`public_release_enabled=false`; no public snapshot has been generated.

## Validation

- Four-country unit, real-document, offline replay, holding-store identity and
  dispatch tests: **40 passed**.
- Ontology static validation initially succeeded. The final rerun of
  `scripts/sync_disease_ontology.py --offline` is blocked by concurrently added
  Colombian BES/FluNet series without availability assertions. The four new
  source declarations remain present. No global registry migration was applied;
  full database acceptance must wait for the shared registry to validate.
- Existing dashboard source-label tests passed; new weekly source choices
  and bilingual labels are registered.
- The broader existing priority-source test run returned 58 passes and nine
  failures involving concurrent additions of high-frequency sources for
  CL/ID/MX/AR and old fixed channel-count expectations. These are separate
  from the passing four-country dispatch tests.
- Dashboard-wide `tsc --noEmit` is currently blocked by the unrelated missing
  `surveillance_rebuild_all` field in
  `src/features/control-plane/publication-planning.test.tsx:32`.

Country contracts: [India](IN_IDSP_INTEGRATION.md),
[Pakistan](PK_NIH_IDSR_INTEGRATION.md),
[Nepal](NP_EDCD_SORMAS_INTEGRATION.md), [Sri Lanka](LK_WER_INTEGRATION.md).

Database acceptance receipts are retained under `data/current/` and
`data/raw/`. Reviewed clinical mappings, source calendars, rights and real
generated snapshots are required before any public support claim.
