# Canada Provincial High-Frequency Surveillance Data Plan

Status: Ontario monthly MVP live-integrated and site-verified as an independent country/region dataset  
Last reviewed: 2026-08-07

## 1. Objective and decisions

GlobalID will keep PHAC CNDSS annual data as the comparable Canada baseline and
publish native provincial observations as independently addressable region
datasets linked to their parent country.

Ontario follows the same product pattern as China (`CN`) and Hong Kong, China
(`HK`): each published jurisdiction has its own location code, standard page,
API identity, and monthly pipeline. Ontario therefore uses the ISO subdivision
code `CA-ON`, while its metadata retains `parent_country_code=CA` and
`location_type=subdivision`. This avoids a Canada-wide geography selector and
lets the existing country/region read path work unchanged.

The implementation follows these rules:

- Never split annual counts into monthly or weekly facts.
- Never label an Ontario series or a partial jurisdiction aggregate as Canada.
- Keep case notifications, laboratory detections, admissions, deaths, and
  outbreaks in separate series.
- Treat missing, not reported, and suppressed values as distinct from zero.
- Preserve preliminary observations and later revisions instead of forcing them
  to reconcile to an annual total.
- Store lossless provincial facts in `disease_series_observations` and write a
  compatibility projection to `disease_records` under the independent `CA-ON`
  country/region id. Never write Ontario facts under the `CA` country id.

The first release does not attempt an all-disease, all-province weekly dataset,
individual-level data, or modelled estimates for missing periods.

## 2. Layered source strategy

| Layer | Grain | Role | Initial source |
| --- | --- | --- | --- |
| A | Canada annual | Long-run comparable baseline | PHAC CNDSS |
| B | Province monthly | Broad recent disease trends | Ontario PHO IDTO |
| C | Province weekly | Disease-specific early warning | PHO respiratory and vector-borne tools; PHAC specialist feeds |
| D | Multi-province aggregate | Coverage-qualified derived view | Later phase only |

Layer D is allowed only when jurisdictions are mutually exclusive, definitions
and time bases are compatible, and the reporting population is explicit.
Otherwise the product must be named a `reporting jurisdictions aggregate`.

## 3. Ontario monthly MVP

### Source

- Publisher: Public Health Ontario (PHO).
- Product: Infectious Disease Trends in Ontario (IDTO).
- Landing page:
  `https://www.publichealthontario.ca/en/data-and-analysis/infectious-disease/reportable-disease-trends-annually`
- Public embed:
  `https://ws-rpt1.publichealthontario.ca/Home/EmbedReport/14b5691a-c95d-46b2-84f1-9119080e083b`
- Source scope: `pho_idto_monthly`.
- Source registry id: `SRC_CA_ON_PHO_IDTO`.
- Jurisdiction code: `CA-ON`.
- Parent country: `CA`.
- Geography key: `country:CA-ON:national` (the full published Ontario
  jurisdiction, not Canada national).
- Reporting grain: calendar month, stored at the first day of the month.
- Measure: reported case count.
- Status: preliminary for the current-year monthly table.

PHO states that the current-year monthly table covers selected diseases at the
Ontario level, is refreshed monthly, and reflects iPHIS data current as of the
second Wednesday of each month. The source export is the authority for which
diseases and periods are present in a release.

### Acquisition modes

The connector supports two acquisition modes with the same normalization
contract:

1. Live public-report export through the embedded Power BI report.
2. A PHO-exported CSV/XLSX supplied through an explicitly selected,
   operator-configured local-file override.

The local-file mode is a supported, auditable fallback, not a synthetic data
path. It is also the deterministic input for backfills and replay tests. A live
run must fail closed when the report cannot be loaded or the monthly table cannot
be identified; it must not reuse a stale snapshot silently.

Live is always the default. Merely setting `CA_ON_IDTO_FILE` does not switch a
run into replay mode. A single task must explicitly use
`acquisition_mode=configured_file`; the connector then reads the configured path
and records the original filename, modification time, byte count, and SHA-256.
Arbitrary dashboard-provided paths are not accepted.

The live discovery contract supports both Power BI's legacy embedded
`config/query` containers and its Fabric PBIR report definitions. For PBIR, the
connector reconstructs the read-only semantic query from the official table
projections plus report, page, and visual filters; it does not hard-code the
current PHU, disease exclusions, or reporting cutoff. The configured page and
visual must still resolve uniquely, and the official date filter must identify
exactly one reviewed reporting year. Missing or ambiguous contracts fail the
run rather than reusing prior data or publishing an empty success.

### Normalized row contract

Each row must contain:

- `Date`, `Year`, and `Month`
- `RawDiseaseLabel` and optional source disease code
- `Cases`
- `JurisdictionCode=CA-ON`, `ParentCountryCode=CA`, and
  `LocationType=subdivision`
- `GeographyKey=country:CA-ON:national`
- `ReportingArea=Ontario`
- `Source` and `SourceURL`
- `DatasetStatus=preliminary` and `IsProvisional=true`
- source extraction/publication metadata when available

Year-to-date counts and rates may be retained as provenance fields, but they are
not additional monthly observations. Explicit monthly zeroes are facts. Blank
cells remain missing. Values such as `<5` are stored as suppressed observations
with no numeric substitute.

### Case-definition contract

The IDTO series represents confirmed cases except for diseases PHO explicitly
documents as including confirmed and probable cases. Each registered source
series must encode the applicable case status and definition version. The
connector must not combine acute and chronic hepatitis B or other overlapping
categories.

### Reviewed July 2026 Registry coverage

The current source exposes 54 disease rows. Of these, 42 (77.8%) have reviewed
source-series registrations: 39 exact mappings and 3 deliberately narrower
mappings. At the current January–May grain this means 210 registered facts out
of 270 source facts. The other 60 facts remain in the normalized source snapshot
and are counted as explicit Registry exclusions; they are not silently coerced
to a near-neighbor concept.

The three narrower mappings are `Echinococcus Multilocularis Infection`,
`Haemophilus Influenzae Disease, All Types, Invasive`, and
`Syphilis, Early Congenital`.

The 12 explicit exclusions are:

- No dedicated ontology concept: Anaplasmosis, Babesiosis, Blastomycosis,
  Candida auris infection, Cyclosporiasis, Ophthalmia Neonatorum,
  Paralytic Shellfish Poisoning, and Powassan virus.
- Unsafe near-neighbor: Carbapenemase-producing Enterobacteriaceae,
  invasive group A streptococcal disease, neonatal group B streptococcal
  disease, and infectious syphilis.

The machine-readable decisions are in `configs/mapping/ca-on_exclusions.json`.
Registered and excluded labels must remain disjoint and total 54 for definition
version `PHO_IDTO_2026_07`; a source-label change triggers review rather than an
automatic mapping.

## 4. Coverage, revisions, and reconciliation

- Record coverage by source, disease, period, and jurisdiction.
- Use `complete`, `partial`, `not_reported`, `suppressed`, or `unknown` coverage
  states; absence is never zero.
- Re-fetch at least the current calendar year because IDTO monthly observations
  are preliminary and can change after late reporting or data cleaning.
- Compare the incoming Power BI model refresh time with the newest stored
  refresh. A newer release may revise equal-quality provisional rows; an
  unchanged release is a no-op and an older release is rejected to prevent
  rollback. PHO's offset-free Power BI refresh value is treated as UTC solely
  for stable release ordering, not as a claimed publication timezone.
- Under the same database mutation lock, compare all stored current-year
  Ontario identities with the incoming complete live snapshot. If a newer
  snapshot turns a prior value into blank or otherwise retracts an identity,
  fail closed instead of leaving a stale value in a hybrid snapshot. Explicit
  missing/tombstone storage is a later model enhancement.
- Archive each downloaded/exported artifact with retrieval time and a content
  hash when raw archival is enabled.
- Keep the lossless import idempotent on
  `(time, series_code, geography_key, dimension_key)` and its compatibility
  projection idempotent on `(time, disease_id, CA-ON country_id)`.
- Compare a 12-month sum with the compatible PHO annual series only when the
  measure, case definition, geography, and date basis agree.
- Classify differences as `revision_timing_difference`, `coverage_difference`,
  `definition_difference`, or `unresolved`; never force-balance values.

## 5. Validation and acceptance criteria

### Data

- Every source-exposed disease is in an imported or explicit excluded list.
- Normalized counts match the official export row for row.
- Zero, blank, non-numeric, and suppression markers have unit tests.
- No duplicate observation natural keys are produced.
- Ontario rows never receive the `CA` country id or `country:CA:national`.
- Year-to-date values are not mistaken for monthly observations.

### Operations

- Replaying one artifact is idempotent.
- Current-year refreshes update revised observations.
- Raw source, content hash, retrieval time, and source URL are traceable.
- Live acquisition fails with a diagnostic error if the public embed changes.
- The source can be refreshed without changing repository-tracked data.

### Product

- Ontario uses the standard `/countries/ca-on/` country/region page, downloads,
  and general disease API; no province-specific chart query path is required.
- Canada keeps `all` and `country:CA:national` as its defaults. PHO IDTO is
  registered only under `CA-ON` and can never become the Canada-wide default.
- UI and downloads expose the Ontario name, parent Canada, source, case status,
  and preliminary/final state.
- Missing, suppressed, and zero values remain distinguishable.

## 6. Delivery phases

### Phase 0 — Contract and location registration

- Keep Canada (`CA`) and register Ontario (`CA-ON`) as separate country/region
  records, with explicit parent/subdivision metadata.
- Reuse the existing national-grain series identity inside the Ontario
  jurisdiction: `country:CA-ON:national`.
- Validate the public report export mechanism and retain a file-export fallback.

### Phase 1 — Ontario monthly MVP

- Normalize the current-year `Monthly preliminary data` table.
- Add reviewed disease mappings and source-series registrations.
- Import Ontario observations with revision-safe dual writes.
- Reuse the shared monthly crawl pipeline, standard site export, downloads, and
  series observation API.

### Phase 1A — Ontario specialist weekly feeds

- Add respiratory and vector-borne feeds as separate source scopes.
- Register cases, tests, detections, admissions, and outbreaks separately.

### Phase 2 and later

- Audit British Columbia, Quebec, and Alberta next.
- Add other provinces only at their native public grain.
- Generate cross-province views only after coverage and comparability gates pass.

## 7. Stop conditions

Leave a series `ingestion_pending` rather than publishing data when:

- the interactive-report endpoint is unstable or disallows the required export;
- only a screenshot is available;
- the source time basis or case definition cannot be identified;
- province identity would be lost or written under the parent `CA` identity;
- suppressed values would have to be inferred; or
- the product cannot clearly communicate partial geographic coverage.

## 8. Official references

- PHAC CNDSS: `https://diseases.canada.ca/notifiable/`
- PHO IDTO landing page: the URL in section 3
- PHO IDTO user guide:
  `https://www.publichealthontario.ca/-/media/Data-Files/idto-tool-user-guide.pdf`
- PHO IDTO technical notes:
  `https://www.publichealthontario.ca/-/media/Data-Files/idto-tool-technical-notes.pdf`
- Statistics Canada population estimates:
  `https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1710000901`

## 9. Implementation touchpoints

- `configs/country_bootstrap.json`
- `configs/disease_ontology.json`
- `configs/mapping/ca-on.csv`
- `configs/mapping/ca-on_exclusions.json`
- `configs/reporting_sources.yml`
- `src/core/country_library.py`
- `src/core/source_scopes.py`
- `src/data/crawlers/ca.py`
- `src/data/processors/ca.py`
- `src/services/crawl_service.py`
- `src/services/crawl_pipelines/monthly.py`
- standard country/region API, site export, and Ontario connector tests

## 10. Implemented release snapshot and follow-ups

The 2026-08-07 live verification read PHO Power BI model refresh
`2026-07-16T15:28:46.083` and produced:

- 54 reviewed source labels and 270 January–May source observations;
- 42 registered series and 210 imported observations;
- 12 explicit exclusions and 60 intentionally unmapped observations;
- 27,754 total notifications from 2026-01-01 through 2026-05-01;
- 42 active series, 42 `available` Registry assertions, and zero projection
  loss-risk diseases;
- zero PHO/Ontario observations stored under the parent `CA` record; and
- 210 reversible before/after audit entries for the legacy geography migration.

The generated `/countries/ca-on/` page, compact site data, and partitioned
CSV/JSON/XLSX downloads were built successfully. UN WPP national population is
deliberately not assigned to `CA-ON`; subdivision-specific rates remain absent
until an approved Ontario denominator is integrated.

The remaining work is shared platform hardening, not an Ontario-specific data
path: make the destructive full-rebuild command capability-aware for
current-source jurisdictions and include Registry synchronization in its
preflight.
