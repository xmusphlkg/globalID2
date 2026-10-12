import { performance } from "node:perf_hooks";
import { adaptCatalog } from "../src/components/charts/surveillance/adapters/catalog.ts";
import { analyze } from "../src/components/charts/surveillance/analysis/engine.ts";
import { initialConfig } from "../src/components/charts/surveillance/state/config.ts";

const SERIES_COUNT = 5;
const POINTS_PER_SERIES = 2_000;
const WARMUP_RUNS = 5;
const MEASURED_RUNS = 30;
const P95_BUDGET_MS = 500;

function dateAt(index) {
  const date = new Date(Date.UTC(2018, 0, 1 + index));
  return date.toISOString().slice(0, 10);
}

const dates = Array.from({ length: POINTS_PER_SERIES }, (_, index) =>
  dateAt(index),
);
const entries = Object.fromEntries(
  Array.from({ length: SERIES_COUNT }, (_, seriesIndex) => {
    const regionId = `BENCH-${String(seriesIndex + 1).padStart(2, "0")}`;
    const cases = dates.map((_, pointIndex) =>
      Math.round(
        500 +
          350 * Math.sin(pointIndex / 35 + seriesIndex / 4) +
          120 * Math.sin(pointIndex / 7) +
          seriesIndex * 30,
      ),
    );
    return [
      regionId,
      {
        disease_id: "D_BENCH",
        name_en: regionId,
        name_zh: regionId,
        dates,
        cases,
        deaths: dates.map(() => 0),
        incidence_rates: [],
        weekly_equiv_cases: [],
        total_cases: cases.reduce((sum, value) => sum + value, 0),
        period_granularity: "daily",
        metric_type: "case_notifications",
        reporting_basis: "notifications",
        time_basis: "report_date",
        definition_version: "benchmark-v1",
        comparability: "direct",
      },
    ];
  }),
);

const catalog = adaptCatalog(entries, {
  id: "benchmark:surveillance",
  kind: "disease",
  diseaseId: "D_BENCH",
});
console.log(
  `Surveillance analysis benchmark on Node ${process.version} ` +
    `(${process.platform}/${process.arch}); synthetic deterministic fixture.`,
);
const selectionIds = Object.keys(catalog.selections);
const base = initialConfig(catalog, "burden", selectionIds);
const scenarios = [
  {
    name: "burden-overlay",
    config: {
      ...base,
      task: "burden",
      selectionIds,
      measures: ["cases"],
      window: null,
      layout: "overlay",
      smoothing: 1,
      showReference: false,
    },
  },
  {
    name: "historical-reference",
    config: {
      ...base,
      task: "historical",
      selectionIds,
      measures: ["cases"],
      window: null,
      layout: "facets",
      smoothing: 1,
      showReference: true,
      referenceYears: 10,
    },
  },
];

function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)];
}

let failed = false;
for (const scenario of scenarios) {
  for (let index = 0; index < WARMUP_RUNS; index++)
    analyze(catalog, scenario.config);
  const durations = [];
  for (let index = 0; index < MEASURED_RUNS; index++) {
    const start = performance.now();
    analyze(catalog, scenario.config);
    durations.push(performance.now() - start);
  }
  const p50 = percentile(durations, 0.5);
  const p95 = percentile(durations, 0.95);
  console.log(
    `${scenario.name}: ${SERIES_COUNT} × ${POINTS_PER_SERIES} points; ` +
      `P50 ${p50.toFixed(1)}ms, P95 ${p95.toFixed(1)}ms, ` +
      `max ${Math.max(...durations).toFixed(1)}ms ` +
      `(${p95 <= P95_BUDGET_MS ? "PASS" : "FAIL"}; budget ${P95_BUDGET_MS}ms)`,
  );
  if (p95 > P95_BUDGET_MS) failed = true;
}

if (failed) process.exitCode = 1;
