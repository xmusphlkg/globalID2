import {
  MEASURES,
  TASKS,
  fingerprint,
  type AggregationGroup,
  type AnalysisConfig,
  type Catalog,
  type Task,
} from "../domain/types.ts";
import { trailingWindow, validDate, windowFor } from "../domain/calendar.ts";

export function initialConfig(
  catalog: Catalog,
  task: Task = "trend",
  preferredSelectionIds?: string[],
): AnalysisConfig {
  const available = Object.values(catalog.selections).filter((selection) =>
    selection.seriesIds.some(
      (id) =>
        catalog.series[id].measure === "cases" &&
        catalog.series[id].observations.some((point) => point.value !== null),
    ),
  );
  const defaults = available.filter((selection) => selection.isDefault);
  const candidates = defaults.length
    ? defaults
    : available.length
      ? available
      : Object.values(catalog.selections);
  const scores = new Map(candidates.map((selection) => {
    const series = selection.seriesIds.map((id) => catalog.series[id]);
    const measure = MEASURES.find((candidate) =>
      series.some((item) => item.measure === candidate &&
        item.observations.some((point) => point.value !== null)),
    );
    const observed = series
      .filter((item) => item.measure === measure)
      .flatMap((item) => item.observations)
      .filter((point) => point.value !== null);
    return [selection.id, {
      measure,
      latest: observed.reduce((date, point) =>
        point.period.label > date ? point.period.label : date, ""),
      total: observed.reduce((sum, point) => sum + (point.value ?? 0), 0),
    }] as const;
  }));
  candidates.sort((left, right) => {
    const a = scores.get(left.id)!;
    const b = scores.get(right.id)!;
    return b.latest.localeCompare(a.latest) ||
      MEASURES.indexOf(a.measure!) - MEASURES.indexOf(b.measure!) ||
      (a.measure === b.measure ? b.total - a.total : 0) ||
      left.id.localeCompare(right.id);
  });
  const coverage = (selectionId: string) => {
    const selection = catalog.selections[selectionId];
    return windowFor(
      selection.seriesIds.flatMap((key) =>
        catalog.series[key].measure === "cases"
          ? catalog.series[key].observations
              .filter((point) => point.value !== null)
              .map((point) => point.period.label)
          : [],
      ),
    );
  };
  const validPreferred = preferredSelectionIds?.filter((id) => catalog.selections[id]);
  const selectionIds = validPreferred?.length
    ? [...new Set(validPreferred)]
    : candidates.length
      ? [candidates[0].id]
      : [];
  const points = selectionIds.flatMap((id) =>
    catalog.selections[id].seriesIds.flatMap((key) =>
      catalog.series[key].observations.map((point) => point.period.label),
    ),
  );
  const domain = windowFor(points);
  const [first, second] = selectionIds.map(coverage);
  const common =
    first && second
      ? {
          startDate:
            first.startDate > second.startDate
              ? first.startDate
              : second.startDate,
          endDate:
            first.endDate < second.endDate ? first.endDate : second.endDate,
        }
      : null;
  const defaultWindow = common ?? domain;
  const annual = selectionIds.every((id) =>
    catalog.selections[id].seriesIds.every(
      (key) => catalog.series[key].cadence === "annual",
    ),
  );
  const firstCandidateSeries = candidates[0]?.seriesIds.map((id) => catalog.series[id]) ?? [];
  const initialMeasure = MEASURES.find((measure) =>
    firstCandidateSeries.some((series) => series.measure === measure &&
      series.observations.some((point) => point.value !== null)),
  ) ?? firstCandidateSeries[0]?.measure ?? "cases";
  return {
    schemaVersion: 3,
    task,
    selectionIds,
    measures: [initialMeasure],
    aggregation: "native",
    window:
      defaultWindow && !annual
        ? trailingWindow(defaultWindow, 36)
        : defaultWindow,
    layout: "auto",
    scale: "linear",
    smoothing: 1,
    referenceYears: 10,
    showReference: true,
    changeBasis: "previous",
    compareWindow: "common",
    aggregationGroups: [],
  };
}
export function retainAggregationGroups(
  groups: AggregationGroup[],
  selectionIds: string[],
): AggregationGroup[] {
  const selected = new Set(selectionIds);
  return groups
    .map((group) => ({
      ...group,
      selectionIds: group.selectionIds.filter((id) => selected.has(id)),
    }))
    .filter((group) => group.selectionIds.length >= 2);
}
export function makeAggregationGroup(selectionIds: string[]): AggregationGroup {
  const members = [...new Set(selectionIds)].sort();
  return { id: `geography-${fingerprint(members)}`, selectionIds: members };
}
export function serializeConfig(
  contextId: string,
  config: AnalysisConfig,
): string {
  return JSON.stringify({ contextId, config });
}
export function restoreConfig(
  text: string,
  catalog: Catalog,
): AnalysisConfig | null {
  try {
    const data = JSON.parse(text);
    const config = data.config;
    if (
      data.contextId !== catalog.contextId ||
      !config ||
      ![1, 2, 3].includes(config.schemaVersion) ||
      !TASKS.includes(config.task)
    )
      return null;
    if (
      !Array.isArray(config.selectionIds) ||
      config.selectionIds.length > 500 ||
      !config.selectionIds.every(
        (id: unknown) =>
          typeof id === "string" && Boolean(catalog.selections[id]),
      )
    )
      return null;
    if (
      !Array.isArray(config.measures) ||
      !config.measures.length ||
      !config.measures.every((measure: any) => MEASURES.includes(measure))
    )
      return null;
    if (
      !["native", "annual"].includes(config.aggregation) ||
      !["auto", "overlay", "facets"].includes(config.layout) ||
      !["linear", "log"].includes(config.scale) ||
      ![1, 3, 7].includes(config.smoothing)
    )
      return null;
    if (config.task === "change" && config.smoothing !== 1) return null;
    if (
      !["common", "independent"].includes(config.compareWindow) ||
      typeof config.showReference !== "boolean" ||
      (!(config.schemaVersion === 1 && config.changeBasis === undefined) &&
        !["previous", "year"].includes(config.changeBasis)) ||
      !Number.isInteger(config.referenceYears) ||
      config.referenceYears < 5 ||
      config.referenceYears > 100
    )
      return null;
    if (
      config.window !== null &&
      (!config.window ||
        !validDate(config.window.startDate) ||
        !validDate(config.window.endDate) ||
        config.window.startDate > config.window.endDate)
    )
      return null;
    const groups = config.aggregationGroups ?? [];
    if (
      !Array.isArray(groups) ||
      groups.length > 20 ||
      !groups.every(
        (group: any) =>
          group &&
          typeof group.id === "string" &&
          Array.isArray(group.selectionIds) &&
          group.selectionIds.length >= 2 &&
          group.selectionIds.length <= 500 &&
          new Set(group.selectionIds).size === group.selectionIds.length &&
          group.selectionIds.every(
            (id: unknown) =>
              typeof id === "string" &&
              Boolean(catalog.selections[id]) &&
              config.selectionIds.includes(id),
          ),
      )
    )
      return null;
    const groupedSelectionIds = groups.flatMap(
      (group: { selectionIds: string[] }) => group.selectionIds,
    );
    if (new Set(groupedSelectionIds).size !== groupedSelectionIds.length)
      return null;
    return {
      schemaVersion: 3,
      task: config.task,
      selectionIds: [...new Set<string>(config.selectionIds)],
      measures: [...new Set(config.measures)] as AnalysisConfig["measures"],
      aggregation: config.aggregation,
      window: config.window,
      layout: config.layout,
      scale: config.scale,
      smoothing: config.smoothing,
      referenceYears: config.referenceYears,
      showReference: config.showReference,
      changeBasis: config.changeBasis ?? "previous",
      compareWindow: config.compareWindow,
      aggregationGroups: groups.map(
        (group: { id: string; selectionIds: string[] }) => ({
          id: group.id,
          selectionIds: [...group.selectionIds],
        }),
      ),
    };
  } catch {
    return null;
  }
}
