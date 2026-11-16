# Malaysia iDengue daily reported dengue cases

Official source: [iDengue](https://idengue.mysa.gov.my/), a collaboration of the
Ministry of Health Malaysia (KKM) and Malaysian Space Agency (MYSA). Verified
and imported on 2026-11-16. This source is independent of the annual STD feed.

- Scope: `my_idengue_daily`; source ID: `SRC_MY_IDENGUE_DAILY`.
- Crawler: `src/data/crawlers/my_dengue.py`.
- Updater: `MalaysiaIDengueDailyUpdater` in `src/data/processors/my_dengue.py`.
- Staged connector: `src/services/ingestion/countries/my_dengue.py`.
- Internal source-native holding observations; public release disabled pending
  reuse review. Mapping notifications are disabled for the source.

## Daily contract and geography

The national daily value is explicitly published in the `MALAYSIA` row. It is
retained independently of the 15 source regional rows; their daily sum is a
validation check and never substitutes for the national figure. The captured
2026-11-15 release contains 193 national daily cases and 15 regional daily
values, including three explicit zeroes. The 16 observations are distinct by
geography; they must not be added together as a national total.

Source labels and `country:MY:source_region:<label>` geography keys are kept
for regions. In particular, `WILAYAH PERSEKUTUAN` is a source reporting region;
the adapter does not split it between Kuala Lumpur and Putrajaya. Labuan's
separate source row remains separate. Both raw provenance and stable dimensions
retain source geography identity, and `series_geography_from_rows=true` prevents
the shared ingestion path from replacing region keys with a national default.

`KES HARIAN PADA <date>` supplies the observation date. The source does not
prove onset-date, diagnosis-date or another underlying event-date basis, so
the definition is `my_idengue_explicit_daily_count_v1` with reporting basis
`published_daily_count_event_date_unspecified`. No underlying event date is
invented. Values are provisional source-revisable counts, not incidence rates.

The parser handles the actual malformed source HTML, checks all three source
columns, requires all reviewed regional labels and one national daily row,
validates the daily/cumulative end date and national/regional reconciliation,
and rejects duplicate regions, missing counts, unexpected geography coverage,
future dates and changed heading semantics. Unknown cells are never made zero.

## Cumulative measures remain separate

The captured cumulative case heading starts at `31 DEC 2023`, despite its end
date being 2026-11-15. That total is not asserted to be 2026 year-to-date data.
Its original heading and per-row cumulative text remain in raw provenance,
but no cumulative case series or daily differences are generated from it.

The separate death card explicitly states 68 cumulative deaths for
2026-01-01 through 2026-11-15. This is retained in the source audit and original
HTML, never labelled as 68 deaths on November 15 or as an inferred daily count.

## Ongoing updates and historical limitations

Each collection captures the original HTML before offline organization. Capture
time and SHA-256 are durable, and replay uses the recorded capture time rather
than the current clock. A repeat release upserts identical date/geography
identities; a new daily date extends stored history. Older stored observations
are retained when the homepage advances to another day.

The public homepage exposes its current published day. A complete historical
daily API was not verified. The linked ArcGIS applications' configuration was
publicly readable, but their portal item-data requests returned permission
errors. Those map links do not establish a usable historical day-count feed.
Missed collection days remain gaps; cumulative case counts cannot backfill them.
Daily polling must not be described as a complete pre-existing daily history.

Persistent schedule configuration belongs to
`scripts/configure_national_notifications.py`. An enabled job alone is not
proof of its first completed scheduled run.

## Verified import and replay

Initial live capture task: `6ebe644b-d4cb-4524-ba20-14cd56db0c3a`.
Final offline replay task: `0393b2b1-2319-42a0-ab8f-058006a642f3`, CrawlRun
`30998`. The latter completed with fail-closed quality checks and zero issues.

Database verification found one source series, 16 distinct natural keys,
one national observation and 15 regional observations. Every stored value,
date, geography, definition, dimension and complete raw-data row matched an
offline replay of the captured artifact. Replay bytes matched the initial
live capture. Full evidence:
`reports/onboarding/my_idengue_database_20261116.json`.

```bash
venv/bin/python scripts/import_my_dengue.py
venv/bin/python scripts/import_my_dengue.py --replay-task-uuid 6ebe644b-d4cb-4524-ba20-14cd56db0c3a
venv/bin/python -m pytest tests/unit/test_my_idengue.py -q
```

The foreground importer maintains its own task lease, fences terminal status
updates against lease ownership and never changes the source calendar or
generates missing historical days.
