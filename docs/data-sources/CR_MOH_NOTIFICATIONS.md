# Costa Rica Ministry notification reports

Reviewed 2026-11-16 against original Ministry of Health responses. This is a
national primary source, independent of the WHO GHO country-reported annual
baseline. The available source archive is broader than the currently reviewed
numerical parser; `coverage_complete` remains false.

- Scope: `cr_moh_notifications`; source ID: `SRC_CR_MOH_NOTIFICATIONS`.
- Stored label: `Costa Rica Ministry of Health Notification Reports`.
- Crawler: `src/data/crawlers/cr_notifications.py`.
- Updater: `CostaRicaNotificationsUpdater` in
  `src/data/processors/cr_notifications.py`.
- Staged connector: `src/services/ingestion/countries/cr_notifications.py`.
- [Individual notification archive](https://www.ministeriodesalud.go.cr/index.php/biblioteca-de-archivos-left/documentos-ministerio-de-salud/material-informativo/material-publicado/estadisticas-y-bases-de-datos/estadisticas-y-bases-de-datos-vigilancia-de-la-salud/estadistica-de-eventos-de-notificacion-obligatoria/notificacion-individual-vigilancia-de-la-salud).
- [Current and historical epidemiological bulletin archive](https://www.ministeriodesalud.go.cr/index.php/biblioteca-de-archivos-left/documentos-ministerio-de-salud/material-informativo/material-publicado/boletines/boletines-vigilancia-vs-enfermedades-de-transmision-vectorial).

The reviewed discovery finds annual individual notification PDFs for 2014 and
2015, and bulletin year directories for 2019–2026, including numbered directory
pages. The complete discovery on this date found 388 unique linked PDFs. The
PDF itself, its directory link, cache receipt and source metadata are retained
before organization. Captured directories and the immutable collection plan
are checked against one another during offline replay.

## Numerical contract

The 2015 official annual report's explicit national Table 1 produces 64
source-native count observations. It includes infectious diseases and other
notifiable events. The parser attempts every row of that table; one overlapping
count/label cell is rejected and appears in the audit. It does not guess a
repaired label or count. A dengue example is 14,886 reported cases; the cholera
row is an explicit zero. The published rate is retained as source metadata and
never substituted for the count. Source asterisks indicating preliminary values
remain explicit. The 2014 layout is presently unsupported.

Annual category identities remove source footnote asterisks and exclude the
preliminary/final flag from dimensions. Finalization and later count revisions
therefore update the same observation; the original label and provisional flag
remain source evidence. The initial import's older identities were reconciled
only after matching all 64 rows to identical immutable originals and counts;
see the [onboarding acceptance](../acceptance/REMAINING_COUNTRIES_20261116.md).

For epidemiological bulletins, the reviewed national tables are dengue counts
by health region and chikungunya notifications/confirmed PCR cases by health
region. National totals are accepted only if all nine distinct health regions
are present and reconcile to the declared national total, separately for each
metric. Rates never become case counts; notification and confirmed PCR
categories remain distinct, non-additive source identities.

These bulletin values are **cumulative publication-date snapshots**. `Date` is
the explicit Spanish publication date on the bulletin cover; `Frequency=daily`
describes the precision of that date, and `PeriodType=cumulative` and the measure
retain the accumulation semantics. This is not a daily incidence feed. The
source epidemiological week is retained in dimensions and is not converted to
an unverified ISO calendar or inferred period end. For example, bulletin number
28 has publication date 2026-08-07 but its dengue/chikungunya tables describe
epidemiological week 29. It yields 2,425 dengue notifications, 113 chikungunya
notifications and 71 PCR-confirmed chikungunya cases.

Equivalent observations repeated across originals are deduplicated. Conflicting
values or epidemiological weeks for the same source category/publication date
are quarantined together; no arbitrary winner is published. Other disease
tables, graphs, narratives, sex/age/municipality breakdowns, historical collective
workbooks and RAR archives remain explicit numerical coverage gaps. An
unsupported report never manufactures zero observations.

## Refresh and historical replay

Discovery rereads the official year directory and every linked pagination page;
new years and new file URLs are picked up from actual Ministry links. Each
document cache entry has SHA-256 and byte-length verification, a bounded 24-hour
freshness period, and ETag/Last-Modified conditional revalidation. Expired cache
bytes are not presented as a successful live refresh when requests fail.
`force`/`force_refresh` bypasses freshness reuse. A failure to persist a captured
artifact is fatal rather than a source-gap warning.

The current control-panel dispatch uses full published history by default,
starting in 2014, so routine checks also revisit older source revisions. All
directories are discovered live; unchanged document bytes inside the 24-hour
cache window are reused and copied through the durable artifact writer without
a new source download. Once the TTL expires, conditional revalidation applies
to the full published archive. Explicit `start_year`/`end_year` requests can
bound a replay, but no recent-two-year default is imposed by this connector.
Neither a partial download nor an unsupported table withdraws previously stored observations.
Public release remains disabled pending source terms, mappings and a verified
country snapshot. Internal reported facts retain source attribution.

```bash
# Real originals and offline organization; no database writes.
venv/bin/python scripts/collect_cr_notifications.py --start-year 2014

# Pure offline replay of a completed local manifest.
venv/bin/python scripts/collect_cr_notifications.py \
  --directory data/raw/cr/moh_notifications_full_20261116 --replay

venv/bin/python -m pytest tests/unit/test_cr_notifications.py -q
```

`data/current/cr/cr_moh_source_audit.json` records the exact raw manifest
directory, linked/captured/parsed counts, unsupported reports, failures,
conflicts and source coverage. The completed 2026-11-16 local capture contains
all 388 discovered PDFs, 33 directory responses and one collection plan
(422 artifacts), with no download failures. Offline organization produces
150 unique national observations: 64 annual observations in 2015, 37 publication
snapshots in 2025 and 49 in 2026, through publication date 2026-11-02.

73 reports yield reviewed tables; 311 are rejected with individual reasons
(mostly no unambiguous publication date, region totals not reconciling, or a
report/table year mismatch), and four have unsupported layouts. These counts
describe report parsing, not missing diseases or zero cases. Equivalent
snapshots repeated in separate reports are deduplicated; no conflicts remain
in the reviewed output. The complete raw manifest is
`data/raw/cr/moh_notifications_full_20261116/artifacts_manifest.json`.
The first bounded import manifest separately covers the 2014–2015 originals
and 64 verified 2015 observations. Database and scheduler acceptance receipts
are separate from these local parse receipts.
