import test from "node:test";
import assert from "node:assert/strict";
import { adaptCatalog } from "../adapters/catalog.ts";
import { buildReportComparison } from "../adapters/reportComparison.ts";
import {
  readReportSnapshotSeries,
  reportPeriodIndices,
} from "../adapters/reportSnapshot.ts";
import { analyze } from "../analysis/engine.ts";
import type { Measure } from "../domain/types.ts";
import { csv, dataRows } from "../presentation/export.ts";
import {
  initialConfig,
  makeAggregationGroup,
  restoreConfig,
  serializeConfig,
} from "../state/config.ts";
import type { CountryDatasetSeriesEntry } from "../../countryDataset";

function entry(
  overrides: Partial<CountryDatasetSeriesEntry> = {},
): CountryDatasetSeriesEntry {
  return {
    disease_id: "D017",
    name_en: "Example",
    name_zh: "示例",
    dates: ["2023-01-01", "2023-02-01"],
    cases: [10, 20],
    deaths: [0, 0],
    incidence_rates: [1, 2],
    weekly_equiv_cases: [],
    total_cases: 30,
    period_granularity: "monthly",
    reporting_basis: "notification",
    time_basis: "report_date",
    definition_version: "v1",
    comparability: "direct",
    ...overrides,
  };
}
const context = {
  id: "disease:D017",
  kind: "disease" as const,
  diseaseId: "D017",
};
test("adapter separates disease, geography, source and measure identities and retains zeroes", () => {
  const catalog = adaptCatalog({ "CN-BJ": entry() }, context);
  assert.equal(catalog.entities["CN-BJ"].diseaseId, "D017");
  assert.equal(catalog.entities["CN-BJ"].parentId, "CN");
  assert.equal(Object.values(catalog.series).length, 3);
  assert.deepEqual(
    catalog.series["CN-BJ::published::deaths"].observations.map((p) => p.value),
    [0, 0],
  );
});
test("recorded-time snapshot metadata is retained and included in the data fingerprint", () => {
  const snapshot = "2025-02-03T04:05:06+00:00";
  const complete = adaptCatalog(
    { CN: entry({ recorded_at_as_of: snapshot, recorded_history_complete: true }) },
    context,
  );
  const incomplete = adaptCatalog(
    { CN: entry({ recorded_at_as_of: snapshot, recorded_history_complete: false }) },
    context,
  );
  assert.equal(complete.recordedAtAsOf, snapshot);
  assert.equal(complete.recordedHistoryComplete, true);
  assert.equal(incomplete.recordedHistoryComplete, false);
  assert.notEqual(complete.version, incomplete.version);
});
test("source-only entries stay separate from the public projection", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates: [],
        cases: [],
        deaths: [],
        incidence_rates: [],
        source_series: [
          {
            series_code: "archive",
            metric_type: "case_count",
            temporal_granularity: "annual",
            dates: ["1997-01-01"],
            values: [3],
          },
        ],
      }),
    },
    context,
  );
  assert.equal(catalog.series["CN::published::cases"], undefined);
  assert.deepEqual(
    catalog.series["CN::archive::cases"].observations.map((point) => point.value),
    [3],
  );
});
test("same-source demographic strata retain distinct selectable identities", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        source_series: [
          {
            series_code: "REGISTRY",
            selection_key: "REGISTRY::sex:female::1",
            metric_type: "case_notifications",
            dimension_key: "sex:female",
            dimensions: { sex: "female" },
            dates: ["2024-01-01"],
            values: [40],
          },
          {
            series_code: "REGISTRY",
            metric_type: "case_notifications",
            dimension_key: "sex:male",
            dimensions: { sex: "male" },
            dates: ["2024-01-01"],
            values: [60],
          },
        ],
      }),
    },
    context,
  );
  const female = Object.values(catalog.selections).find(
    (selection) => selection.sourceLabel?.includes("sex: female"),
  );
  const male = Object.values(catalog.selections).find(
    (selection) => selection.sourceLabel?.includes("sex: male"),
  );
  assert.ok(female);
  assert.ok(male);
  assert.deepEqual(
    female.seriesIds.map((id) => catalog.series[id].measure),
    ["cases"],
  );
  assert.deepEqual(
    catalog.series[female.seriesIds[0]].observations.map(
      (point) => point.value,
    ),
    [40],
  );
  assert.equal(
    catalog.series[male.seriesIds[0]].dimensions.sex,
    "male",
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog),
    selectionIds: [female.id],
    measures: ["cases" as Measure],
  });
  assert.match(dataRows(result, catalog, "en", "raw")[0].series, /sex: female/);
  assert.match(csv(result, catalog, "en", "raw"), /sex: female/);
});
test("point quality overrides stale provisional boundaries and nulls stay null", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        provisional_from: "2023-01-01",
        point_quality_statuses: ["raw", "raw", "provisional"],
        dates: ["2023-01-01", "2023-02-01", "2023-03-01"],
        cases: [4, 5, null],
      }),
    },
    context,
  );
  const points = catalog.series["CN::published::cases"].observations;
  assert.deepEqual(points.map((point) => point.quality), [[], [], ["provisional"]]);
  assert.deepEqual(points.map((point) => point.value), [4, 5, null]);
});
test("report snapshot adapter accepts complete frozen series and rejects incomplete archives", () => {
  const snapshot = {
    schema_version: "report_surveillance.v1",
    version: "a".repeat(64),
    series: {
      D017: {
        ...entry(),
        dates: ["2026-01-01"],
        cases: [0],
        deaths: [null],
        incidence_rates: [null],
        period_starts: ["2026-01-01"],
        period_ends_exclusive: ["2026-02-01"],
        point_quality_statuses: [""],
      },
    },
  };
  const archived = readReportSnapshotSeries(snapshot, "D017");
  assert.deepEqual(archived?.cases, [0]);
  assert.deepEqual(archived?.deaths, [null]);
  assert.equal(archived?.snapshot_version, "a".repeat(64));
  const catalog = adaptCatalog(
    { D017: archived! },
    {
      id: "report:123:D017",
      kind: "report",
      diseaseId: "D017",
      geographyId: "JP",
      snapshot: true,
      sourceVersion: archived!.snapshot_version,
    },
  );
  assert.equal(catalog.snapshot, true);
  assert.equal(catalog.sourceVersion, "a".repeat(64));
  assert.equal(
    Object.values(catalog.series)[0].provenance.snapshotVersion,
    "a".repeat(64),
  );
  assert.equal(readReportSnapshotSeries(snapshot, "D018"), null);
  assert.equal(
    readReportSnapshotSeries(
      {
        ...snapshot,
        series: { D017: { ...snapshot.series.D017, deaths: [] } },
      },
      "D017",
    ),
    null,
  );
  for (const invalid of [
    { period_ends_exclusive: [] },
    { period_ends_exclusive: ["2026-01-01"] },
    { period_starts: ["2026-01-02"] },
    { point_quality_statuses: [] },
    { dates: ["2026-02-30"] },
  ]) {
    assert.equal(
      readReportSnapshotSeries(
        {
          ...snapshot,
          series: { D017: { ...snapshot.series.D017, ...invalid } },
        },
        "D017",
      ),
      null,
    );
  }
});
test("frozen report periods retain annual bins that start before the report window", () => {
  assert.deepEqual(
    reportPeriodIndices(["2025-01-01"], "2025-03-01", "2025-12-31", true),
    [0],
  );
  assert.deepEqual(
    reportPeriodIndices(
      ["2025-01-01", "2025-06-01", "2026-01-01"],
      "2025-03-01",
      "2025-12-31",
      false,
    ),
    [1],
  );
  assert.deepEqual(
    reportPeriodIndices(
      ["2025-01-01"],
      "2025-03-01",
      "2025-12-31",
      false,
      ["2025-01-01"],
      ["2026-01-01"],
    ),
    [0],
  );
});
test("default weekly series retains exact source calendar boundaries", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates: ["2026-01-05", "2026-01-12"],
        cases: [2, 3],
        deaths: [null, null],
        incidence_rates: [null, null],
        period_granularity: "weekly",
        period_starts: ["2026-01-05", "2026-01-12"],
        period_ends_exclusive: ["2026-01-12", "2026-01-19"],
        calendar: "iso-week",
      }),
    },
    context,
  );
  const periods = catalog.series["CN::published::cases"].observations.map(
    (point) => point.period,
  );
  assert.equal(periods[0].calendar, "iso-week");
  assert.equal(periods[0].precision, "exact");
  assert.equal(periods[0].endExclusive, "2026-01-12");
});
test("exact ISO weeks expose missing weeks and only total complete windows", () => {
  const make = (dates: string[], cases: number[]) =>
    adaptCatalog(
      {
        CN: entry({
          dates,
          cases,
          deaths: dates.map(() => null),
          incidence_rates: dates.map(() => null),
          period_granularity: "weekly",
          period_starts: dates,
          period_ends_exclusive: dates.map((date) =>
            new Date(Date.parse(date) + 7 * 86_400_000)
              .toISOString()
              .slice(0, 10),
          ),
          calendar: "iso-week",
        }),
      },
      context,
    );
  const complete = make(["2026-01-05", "2026-01-12", "2026-01-19"], [2, 3, 4]);
  const summary = analyze(complete, initialConfig(complete)).summaries[0];
  assert.equal(summary.expectedPeriods, 3);
  assert.equal(summary.total, 9);
  const missing = make(["2026-01-05", "2026-01-19"], [2, 4]);
  const gap = analyze(missing, initialConfig(missing)).summaries[0];
  assert.equal(gap.expectedPeriods, 3);
  assert.equal(gap.missing, 1);
  assert.equal(gap.total, null);
});
test("inferred frequency is a display hint and does not certify complete periods", () => {
  const catalog = adaptCatalog({
    CN: entry({
      dates: ["2025-01-01", "2025-02-01"],
      cases: [10, 20],
      period_granularity: "monthly",
      period_granularity_basis: "date_pattern",
    }),
  }, context);
  const series = Object.values(catalog.series).find((item) => item.measure === "cases")!;
  assert.equal(series.cadence, "unknown");
  assert.equal(series.displayCadence, "monthly");
  assert.equal(analyze(catalog, initialConfig(catalog)).summaries[0].total, null);
});
test("report-window fragments cannot be presented as complete annual burden", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates: ["2025-01-01"],
        cases: [5],
        deaths: [null],
        incidence_rates: [null],
        period_granularity: "annual",
        period_starts: ["2025-01-01"],
        period_ends_exclusive: ["2026-01-01"],
        point_quality_statuses: ["partial_report_window"],
      }),
    },
    context,
  );
  const config = initialConfig(catalog);
  const native = analyze(catalog, config);
  assert.equal(native.summaries[0].total, null);
  assert.ok(
    native.diagnostics.some((item) => item.code === "partial_report_window"),
  );
  const annual = analyze(catalog, { ...config, aggregation: "annual" });
  assert.ok(annual.diagnostics.some((item) => item.code === "incomplete_year"));
});
test("report revision comparison aligns current periods and marks scope mismatches", () => {
  const disease = { disease_id: "D017", name_en: "Example", name_zh: "示例" };
  const snapshot = {
    ...entry({
      dates: ["2025-01-01"],
      cases: [5],
      deaths: [null],
      incidence_rates: [null],
      period_granularity: "annual",
      period_starts: ["2025-01-01"],
      period_ends_exclusive: ["2026-01-01"],
      point_quality_statuses: ["partial_report_window"],
      data_layer: "report_snapshot",
    }),
    snapshot_version: "b".repeat(64),
  };
  const latest = entry({
    dates: ["2025-01-01", "2026-01-01"],
    cases: [100, 200],
    deaths: [null, null],
    incidence_rates: [null, null],
    period_granularity: "annual",
    period_starts: ["2025-01-01", "2026-01-01"],
    period_ends_exclusive: ["2026-01-01", "2027-01-01"],
    calendar: "gregorian",
  });
  const comparison = buildReportComparison({
    series: snapshot,
    latestSeries: latest,
    disease,
    periodStart: "2025-03-01",
    periodEnd: "2025-12-31",
  });
  assert.equal(comparison.compareLatest, true);
  assert.deepEqual(Object.keys(comparison.entries), ["D017::snapshot", "D017::latest"]);
  assert.deepEqual(comparison.entries["D017::latest"].dates, ["2025-01-01"]);
  assert.equal(
    comparison.entries["D017::latest"].point_quality_statuses?.[0],
    "period_scope_mismatch",
  );
  const catalog = adaptCatalog(comparison.entries, {
    id: "report:JP:123:D017",
    kind: "report",
    diseaseId: "D017",
    geographyId: "JP",
    snapshot: comparison.hasSnapshot,
    sourceVersion: snapshot.snapshot_version,
  });
  const config = initialConfig(catalog, comparison.initialTask, comparison.initialSelectionIds);
  const result = analyze(catalog, config);
  assert.equal(result.lines.length, 2);
  assert.equal(catalog.snapshot, true);
  assert.equal(catalog.sourceVersion, snapshot.snapshot_version);
  assert.deepEqual(
    new Set(result.lines.map((line) => line.provenance.dataLayer)),
    new Set(["report_snapshot", "latest_published"]),
  );
  assert.equal(
    result.lines.find((line) => line.provenance.dataLayer === "latest_published")?.provenance.snapshotVersion,
    undefined,
  );
  const rows = dataRows(result, catalog, "en", "window");
  assert.equal(rows.find((row) => row.series.includes("report snapshot"))?.sourceVersion, snapshot.snapshot_version);
  assert.equal(rows.find((row) => row.series.includes("current published"))?.sourceVersion, null);
  assert.ok(csv(result, catalog, "en", "window").includes('"source_version"'));
  assert.equal(result.lines[0].panelId, result.lines[1].panelId);
  assert.equal(result.summaries.every((summary) => summary.total === null), true);
});
test("period change uses matched periods and does not fabricate gaps or zero baselines", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates: ["2025-01-01", "2025-02-01", "2025-03-01", "2025-06-01", "2026-01-01"],
        cases: [10, 0, 5, 30, 15],
        deaths: [null, null, null, null, null],
        incidence_rates: [null, null, null, null, null],
      }),
    },
    context,
  );
  const base = initialConfig(catalog, "change");
  const previous = analyze(catalog, { ...base, window: null });
  assert.equal(previous.lines[0].unit, "change_percent");
  assert.deepEqual(previous.lines[0].points.map((point) => point.value), [null, -100, null, null, null]);
  assert.equal(previous.summaries[0].total, null);
  assert.equal(previous.summaries[0].changePrevious, null);
  assert.ok(previous.diagnostics.some((item) => item.code === "change_baseline_missing"));
  assert.ok(previous.diagnostics.some((item) => item.code === "change_baseline_nonpositive"));
  const yearly = analyze(catalog, { ...base, window: null, changeBasis: "year" });
  assert.equal(yearly.lines[0].points.at(-1)?.value, 50);
  const exported = dataRows(yearly, catalog, "en", "derived").at(-1);
  assert.equal(exported?.baselineDate, "2025-01-01");
  assert.equal(exported?.baselineValue, 10);
  assert.ok(csv(yearly, catalog, "en", "derived").includes('"2025-01-01","10"'));
  const changed = catalog.series["CN::published::cases"];
  changed.events.push({ date: "2025-07-01", label: "definition_change" });
  const afterBreak = analyze(catalog, { ...base, window: null, changeBasis: "year" });
  assert.equal(afterBreak.lines[0].points.at(-1)?.value, null);
  assert.ok(afterBreak.diagnostics.some((item) => item.code === "change_definition_break"));
});
test("old shared configurations restore with a previous-period change default", () => {
  const catalog = adaptCatalog({ CN: entry() }, context);
  const old = { ...initialConfig(catalog), schemaVersion: 1 } as Record<string, unknown>;
  delete old.changeBasis;
  const restored = restoreConfig(JSON.stringify({ contextId: catalog.contextId, config: old }), catalog);
  assert.equal(restored?.schemaVersion, 3);
  assert.equal(restored?.changeBasis, "previous");
});
test("geographic aggregation sums aligned sibling regions and leaves incomplete periods blank", () => {
  const catalog = adaptCatalog(
    {
      "CN-ZJ": entry({
        dates: ["2024-01-01", "2024-02-01"],
        cases: [20, 40],
        deaths: [0, 1],
      }),
      "CN-GD": entry({
        dates: ["2024-01-01", "2024-02-01"],
        cases: [10, null],
        deaths: [2, 3],
      }),
    },
    context,
  );
  const selections = ["CN-ZJ::published", "CN-GD::published"];
  const config = {
    ...initialConfig(catalog, "burden", selections),
    measures: ["cases" as Measure],
    window: null,
    aggregationGroups: [makeAggregationGroup(selections)],
  };
  const result = analyze(catalog, config);
  assert.equal(result.lines.length, 1);
  assert.equal(result.lines[0].aggregateMembers?.length, 2);
  assert.deepEqual(result.lines[0].points.map((point) => point.value), [30, null]);
  assert.ok(result.lines[0].points[1].quality.includes("aggregate_incomplete"));
  assert.ok(result.diagnostics.some((item) => item.code === "aggregate_incomplete"));
  const exported = dataRows(result, catalog, "en", "derived")[0];
  assert.equal(exported.aggregationMembers, "Example; Example");
  assert.ok(csv(result, catalog, "en", "derived").includes('"aggregation_members"'));
});
test("geographic aggregation refuses a parent and its child", () => {
  const catalog = adaptCatalog(
    {
      CN: entry(),
      "CN-ZJ": entry(),
    },
    context,
  );
  const selections = ["CN::published", "CN-ZJ::published"];
  const result = analyze(catalog, {
    ...initialConfig(catalog, "burden", selections),
    measures: ["cases" as Measure],
    window: null,
    aggregationGroups: [makeAggregationGroup(selections)],
  });
  assert.equal(result.lines.length, 2);
  assert.ok(result.diagnostics.some((item) => item.code === "aggregate_geography_overlap"));
});
test("allowed entities cannot be restored through shared configuration", () => {
  const full = adaptCatalog({ CN: entry(), US: entry() }, context);
  const restricted = adaptCatalog(
    { CN: entry(), US: entry() },
    { ...context, allowedEntityIds: ["CN"] },
  );
  const config = {
    ...initialConfig(full, "burden"),
    selectionIds: ["US::published"],
  };
  assert.equal(
    restoreConfig(serializeConfig(context.id, config), restricted),
    null,
  );
});
test("default query chooses recent data and an explicit comparison uses a common window", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({ dates: ["2020-01-01", "2020-02-01"], cases: [1, 2] }),
      US: entry({ dates: ["2024-01-01"], cases: [3] }),
      BR: entry({ dates: ["2020-02-01", "2020-03-01"], cases: [4, 5] }),
    },
    context,
  );
  const config = initialConfig(catalog);
  assert.deepEqual(config.selectionIds, ["US::published"]);
  assert.deepEqual(config.window, {
    startDate: "2024-01-01",
    endDate: "2024-01-01",
  });
  const comparison = initialConfig(catalog, "burden", ["CN::published", "BR::published"]);
  assert.deepEqual(comparison.window, {
    startDate: "2020-02-01",
    endDate: "2020-02-01",
  });
});
test("default query selects the first populated metric when cases are unavailable", () => {
  const catalog = adaptCatalog(
    { CN: entry({ cases: [null, null], deaths: [2, 4], incidence_rates: [null, null] }) },
    context,
  );
  const config = initialConfig(catalog);
  assert.deepEqual(config.measures, ["deaths"]);
  assert.deepEqual(config.selectionIds, ["CN::published"]);
});
test("specific sources retain their own identity and non-case metric semantics", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        source_series: [
          {
            series_code: "a",
            metric_type: "case_notifications",
            dates: ["2023-01-01"],
            values: [5],
          },
          {
            series_code: "b",
            metric_type: "test_positivity",
            unit: "%",
            dates: ["2023-01-01"],
            values: [12],
          },
        ],
      }),
    },
    context,
  );
  assert.equal(catalog.series["CN::a::cases"].observations[0].value, 5);
  assert.equal(catalog.series["CN::b::source_value"].unit, "%");
  assert.equal(
    catalog.series["CN::published::cases"].observations[0].value,
    10,
  );
});
test("unloaded sources are represented explicitly rather than mistaken for no observations", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        source_series: [
          { series_code: "a", metric_type: "case_notifications" },
        ],
      }),
    },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog),
    selectionIds: ["CN::a"],
  });
  assert.equal(result.diagnostics[0].code, "source_unloaded");
});
test("annual aggregation rejects duplicate periods even with twelve values", () => {
  const dates = Array.from(
    { length: 12 },
    (_, i) => `2023-${String(i + 1).padStart(2, "0")}-01`,
  );
  dates[11] = dates[10];
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates,
        cases: dates.map(() => 10),
        deaths: [],
        incidence_rates: [],
      }),
    },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog),
    aggregation: "annual",
    window: null,
  });
  assert.equal(result.lines.length, 0);
  assert.ok(
    result.diagnostics.some((item) => item.code === "duplicate_period"),
  );
});
test("annual aggregation sums complete calendar coverage and preserves incomplete diagnostics", () => {
  const dates = Array.from(
    { length: 12 },
    (_, i) => `2023-${String(i + 1).padStart(2, "0")}-01`,
  ).concat("2024-01-01");
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates,
        cases: dates.map(() => 10),
        deaths: [],
        incidence_rates: [],
      }),
    },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog),
    aggregation: "annual",
    window: null,
  });
  assert.equal(result.lines[0].points[0].value, 120);
  assert.equal(result.lines[0].points.length, 1);
  assert.ok(result.diagnostics.some((item) => item.detail === "2024"));
});
test("registered non-additive series cannot produce annual or window totals", () => {
  const dates = Array.from({ length: 12 }, (_, index) =>
    `2025-${String(index + 1).padStart(2, "0")}-01`,
  );
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates,
        cases: dates.map(() => 3),
        deaths: dates.map(() => null),
        incidence_rates: dates.map(() => null),
        period_granularity: "monthly",
        aggregation_policy: "non_additive",
      }),
    },
    context,
  );
  const native = analyze(catalog, { ...initialConfig(catalog), window: null });
  assert.equal(native.summaries[0].total, null);
  const annual = analyze(catalog, {
    ...initialConfig(catalog),
    aggregation: "annual",
    window: null,
  });
  assert.ok(annual.diagnostics.some((item) => item.code === "not_additive"));
});
test("weekly dates without period boundaries do not invent complete calendar years", () => {
  const dates = Array.from({ length: 53 }, (_, i) =>
    new Date(Date.UTC(2023, 0, 1 + i * 7)).toISOString().slice(0, 10),
  );
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates,
        cases: dates.map(() => 1),
        period_granularity: "weekly",
        deaths: [],
        incidence_rates: [],
      }),
    },
    context,
  );
  assert.equal(
    analyze(catalog, {
      ...initialConfig(catalog),
      aggregation: "annual",
      window: null,
    }).lines.length,
    0,
  );
});
test("rates cannot be summed as annual counts", () => {
  const catalog = adaptCatalog({ CN: entry() }, context);
  const result = analyze(catalog, {
    ...initialConfig(catalog),
    measures: ["incidence_rates"],
    aggregation: "annual",
  });
  assert.ok(result.diagnostics.some((item) => item.code === "not_additive"));
});
test("no common interval keeps independent series available", () => {
  const catalog = adaptCatalog(
    { CN: entry(), US: entry({ dates: ["2010-01-01", "2010-02-01"] }) },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog, "burden"),
    selectionIds: ["CN::published", "US::published"],
    window: null,
  });
  assert.equal(result.lines.length, 2);
  assert.equal(result.layout, "facets");
  assert.ok(
    result.diagnostics.some((item) => item.code === "no_common_window"),
  );
});
test("explicitly noncomparable sources remain available only as independent panels", () => {
  const catalog = adaptCatalog(
    { CN: entry(), US: entry({ comparability: "not_comparable" }) },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog, "burden"),
    selectionIds: ["CN::published", "US::published"],
    layout: "overlay",
  });
  assert.equal(result.lines.length, 2);
  assert.equal(result.layout, "facets");
  assert.ok(
    result.diagnostics.some((item) => item.code === "overlay_unavailable"),
  );
});
test("automatic facets give large same-definition selections separate pages", () => {
  const codes = ["AU", "BR", "CN", "JP", "NO", "US"];
  const catalog = adaptCatalog(
    Object.fromEntries(codes.map((code) => [code, entry()])),
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog, "burden"),
    selectionIds: codes.map((code) => `${code}::published`),
    layout: "auto",
  });
  assert.equal(result.layout, "facets");
  assert.equal(new Set(result.lines.map((line) => line.panelId)).size, 6);
});
test("out-of-domain windows remain empty instead of clamping to a point", () => {
  const catalog = adaptCatalog({ CN: entry() }, context);
  const config = {
    ...initialConfig(catalog),
    window: { startDate: "2000-01-01", endDate: "2001-01-01" },
  };
  const result = analyze(catalog, config);
  assert.deepEqual(result.window, config.window);
  assert.equal(result.lines[0].windowPoints.length, 0);
  assert.equal(result.summaries[0].latest, null);
});
test("table input and summaries use the display window while peak normalization stays fixed", () => {
  const catalog = adaptCatalog({ CN: entry() }, context);
  const result = analyze(catalog, {
    ...initialConfig(catalog, "shape"),
    window: { startDate: "2023-01-01", endDate: "2023-01-31" },
  });
  assert.equal(result.lines[0].windowPoints[0].value, 50);
  assert.equal(result.summaries[0].latest?.value, 50);
  assert.equal(result.summaries[0].total, null);
});
test("historical references use only prior years and exclude provisional baseline values", () => {
  const dates = Array.from({ length: 7 }, (_, i) => `${2017 + i}-01-01`);
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates,
        cases: [10, 10, 10, 10, 10, 20, 1000],
        deaths: [],
        incidence_rates: [],
      }),
    },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog, "historical"),
    window: null,
  });
  assert.equal(result.lines[0].points[4].value, null);
  assert.equal(result.lines[0].points[5].value, 200);
  assert.equal(result.lines[0].points[6].expected, 10);
});
test("nonpositive observations disable log rendering with an explicit diagnostic", () => {
  const catalog = adaptCatalog({ CN: entry({ cases: [0, -2] }) }, context);
  const result = analyze(catalog, { ...initialConfig(catalog), scale: "log" });
  assert.equal(result.scale, "linear");
  assert.deepEqual(
    result.lines[0].points.map((point) => point.value),
    [0, -2],
  );
  assert.ok(result.diagnostics.some((item) => item.code === "log_nonpositive"));
});
test("seasonality retains actual source dates in periods while using a common phase axis", () => {
  const catalog = adaptCatalog(
    { CN: entry({ dates: ["2022-01-01", "2023-01-01"] }) },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog, "seasonality"),
    window: null,
  });
  assert.equal(result.lines.length, 2);
  assert.equal(result.lines[0].points[0].date, "2000-01-01");
  assert.equal(result.lines[0].points[0].period.label, "2022-01-01");
});
test("seasonality aligns exact ISO weeks by ISO year, including week 53", () => {
  const dates = ["2019-12-30", "2020-12-28", "2021-01-04"];
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates,
        cases: [1, 53, 2],
        period_granularity: "weekly",
        period_starts: dates,
        period_ends_exclusive: ["2020-01-06", "2021-01-04", "2021-01-11"],
        calendar: "iso-week",
      }),
    },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog, "seasonality"),
    window: null,
  });
  assert.deepEqual(result.lines.map((line) => line.labelSuffix), ["2020", "2021"]);
  assert.deepEqual(
    result.lines[0].points.map((point) => point.date),
    ["2003-12-29", "2004-12-27"],
  );
  assert.deepEqual(
    result.lines[0].points.map((point) => point.period.label),
    ["2019-12-30", "2020-12-28"],
  );
});
test("weekly seasonality stays unavailable without an exact declared ISO calendar", () => {
  const catalog = adaptCatalog(
    {
      CN: entry({
        dates: ["2024-01-01", "2024-01-08"],
        cases: [1, 2],
        period_granularity: "weekly",
      }),
    },
    context,
  );
  const result = analyze(catalog, {
    ...initialConfig(catalog, "seasonality"),
    window: null,
  });
  assert.equal(result.lines.length, 0);
  assert.ok(result.diagnostics.some((item) => item.code === "seasonality_calendar_unknown"));
});
