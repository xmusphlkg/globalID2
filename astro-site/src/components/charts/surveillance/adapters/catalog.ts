import type {
  CountryDatasetSeriesEntry,
  SourceSeriesMetadata,
} from "../../countryDataset";
import { cadence, periodFor, validDate } from "../domain/calendar.ts";
import {
  fingerprint,
  type Catalog,
  type Entity,
  type Measure,
  type Observation,
  type SurveillanceSeries,
} from "../domain/types.ts";

export interface CatalogContext {
  id: string;
  kind: "country" | "disease" | "research" | "report";
  diseaseId?: string;
  geographyId?: string;
  allowedEntityIds?: string[];
  snapshot?: boolean;
  sourceVersion?: string;
}
export type LegacyEntries = Record<string, CountryDatasetSeriesEntry>;
function sourceIdentity(source: SourceSeriesMetadata): string {
  if (source.selection_key) return source.selection_key;
  const seriesCode = source.series_code ?? "unknown";
  const dimensionKey = source.dimension_key ?? "all";
  const dimensions = sourceDimensions(source);
  if (dimensionKey === "all" && Object.keys(dimensions).length === 0)
    return seriesCode;
  return `${seriesCode}::${dimensionKey}::${encodeURIComponent(JSON.stringify(dimensions))}`;
}
function sourceDimensions(source?: SourceSeriesMetadata): Record<string, string> {
  return Object.fromEntries(
    Object.entries(source?.dimensions ?? {})
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([key, value]) => [key, String(value)]),
  );
}
const nullable = (v: unknown) =>
  typeof v === "string" && v.trim() ? v.trim() : null;
function valueKind(
  metric: string | null,
  measure: Measure,
): SurveillanceSeries["valueKind"] {
  if (
    measure === "incidence_rates" ||
    /rate|prevalence|percent/i.test(metric ?? "")
  )
    return "rate";
  if (/cumulative/i.test(metric ?? "")) return "cumulative_count";
  if (/rolling|moving|average|mean|stock/i.test(metric ?? "")) return "unknown";
  return measure === "source_value" ? "unknown" : "interval_count";
}
function sourceMeasure(source: SourceSeriesMetadata): Measure {
  const metric = source.metric_type ?? "";
  if (/death|mortality_count/.test(metric)) return "deaths";
  if (/incidence_rate/.test(metric)) return "incidence_rates";
  if (
    /case|notification|infection_count/.test(metric) &&
    !/rate|percent/.test(metric)
  )
    return "cases";
  return "source_value";
}
function sourceRates(
  entry: CountryDatasetSeriesEntry,
  source: SourceSeriesMetadata,
) {
  if (source.incidence_rates?.some((value) => value != null))
    return {
      values: source.incidence_rates,
      method: source.population_values?.some((value) => value != null)
        ? ("explicit_population" as const)
        : ("published" as const),
    };
  const dates = source.dates ?? [],
    values = source.values ?? [];
  if (source.population_values?.length === dates.length)
    return {
      values: values.map((value, index) => {
        const population = source.population_values![index];
        return value != null && population != null && population > 0
          ? (value / population) * 100000
          : null;
      }),
      method: "explicit_population" as const,
    };
  // Compatibility only: recover the denominator of already-published computed
  // rates for this entity/year. Never borrow another geography or a stratum.
  if (source.dimension_key && source.dimension_key !== "all") return null;
  const byYear = new Map<string, number[]>();
  entry.dates.forEach((date, index) => {
    const cases = entry.cases[index],
      rate = entry.incidence_rates?.[index];
    if (
      cases == null ||
      rate == null ||
      cases <= 0 ||
      rate <= 0 ||
      !entry.incidence_sources?.[index]?.endsWith("_computed")
    )
      return;
    const year = date.slice(0, 4);
    byYear.set(year, [...(byYear.get(year) ?? []), (cases / rate) * 100000]);
  });
  const rates = dates.map((date, index) => {
    const population = (byYear.get(date.slice(0, 4)) ?? []).sort(
      (a, b) => a - b,
    );
    const value = values[index];
    if (!population.length || value == null) return null;
    const midpoint = Math.floor(population.length / 2);
    const denominator =
      population.length % 2
        ? population[midpoint]
        : (population[midpoint - 1] + population[midpoint]) / 2;
    return (value / denominator) * 100000;
  });
  return rates.some((value) => value != null)
    ? { values: rates, method: "legacy_inferred_population" as const }
    : null;
}
export function mergeSources(
  entries: LegacyEntries,
  details: Record<string, SourceSeriesMetadata[]>,
): LegacyEntries {
  return Object.fromEntries(
    Object.entries(entries).map(([id, entry]) => {
      const sources = new Map(
        (entry.source_series ?? []).map((source) => [
          sourceIdentity(source),
          source,
        ]),
      );
      for (const source of details[id] ?? []) {
        const identity = sourceIdentity(source);
        sources.set(identity, {
          ...sources.get(identity),
          ...source,
        });
      }
      return [id, { ...entry, source_series: [...sources.values()] }];
    }),
  );
}
export function adaptCatalog(
  entries: LegacyEntries,
  context: CatalogContext,
): Catalog {
  const catalog: Catalog = {
    contextId: context.id,
    version: "",
    recordedAtAsOf: null,
    recordedHistoryComplete: null,
    snapshot: Boolean(context.snapshot),
    sourceVersion: context.sourceVersion,
    entities: {},
    selections: {},
    series: {},
  };
  const allowed = context.allowedEntityIds
    ? new Set(context.allowedEntityIds)
    : null;
  const snapshotTimes = new Set(
    Object.values(entries)
      .map((entry) => entry.recorded_at_as_of)
      .filter((value): value is string => Boolean(value)),
  );
  const completeness = Object.values(entries)
    .map((entry) => entry.recorded_history_complete)
    .filter((value): value is boolean => typeof value === "boolean");
  catalog.recordedAtAsOf = snapshotTimes.size === 1 ? [...snapshotTimes][0] : null;
  catalog.recordedHistoryComplete = snapshotTimes.size > 1
    ? false
    : completeness.length ? completeness.every(Boolean) : null;
  for (const [entityId, entry] of Object.entries(entries)) {
    if ((allowed && !allowed.has(entityId)) || entry.category === "Summary")
      continue;
    const geographyId =
      context.kind === "country" || context.kind === "report"
        ? (context.geographyId ?? "unknown")
        : entityId;
    const diseaseId =
      context.kind === "country"
        ? entityId
        : (context.diseaseId ?? entry.disease_id);
    const entity: Entity = {
      id: entityId,
      diseaseId,
      geographyId,
      labels: {
        en: entry.name_en,
        zh: entry.name_zh || entry.name_en,
        fr: entry.name_fr || entry.name_en,
      },
      // Existing exports use ISO subdivision keys. Retain this compatibility mapping
      // at the adapter boundary; the analysis engine consumes explicit hierarchy.
      parentId: /^[A-Z]{2}-/.test(geographyId)
        ? geographyId.split("-")[0]
        : null,
      geographyLevel: /^[A-Z]{2}-/.test(geographyId)
        ? "subdivision"
        : /^[A-Z]{2}$/.test(geographyId)
          ? "country"
          : "unknown",
      category: entry.category,
    };
    catalog.entities[entityId] = entity;
    const chosenSources = (entry.source_series ?? []).filter((source) =>
      (source.dimension_key ?? "all") === "all" &&
      (entry.selected_series_codes ?? []).includes(source.series_code ?? ""),
    );
    const defaultSource =
      chosenSources.length === 1 ? chosenSources[0] : undefined;
    const add = (
      source: SourceSeriesMetadata | undefined,
      measure: Measure,
      values: Array<number | null>,
      dates: string[],
      isDefault: boolean,
    ) => {
      if (!dates.length && !source) return;
      const sourceId = isDefault
        ? "published"
        : (source ? sourceIdentity(source) : "unknown");
      const selectionId = `${encodeURIComponent(entityId)}::${encodeURIComponent(sourceId)}`;
      const id = `${selectionId}::${measure}`;
      const metadata = source ?? defaultSource;
      const dimensions = sourceDimensions(metadata);
      const pointGrains = [
        ...new Set((entry.point_granularities ?? []).filter(Boolean)),
      ];
      const displayGrain = cadence(
        isDefault
          ? (entry.period_granularity ??
              metadata?.temporal_granularity ??
              (pointGrains.length === 1 ? pointGrains[0] : null))
          : source?.temporal_granularity,
      );
      const grain = isDefault && entry.period_granularity_basis === "date_pattern"
        ? "unknown"
        : displayGrain;
      const definitionId = nullable(
        metadata?.definition_version ?? entry.definition_version,
      );
      const quality = isDefault
        ? (entry.point_quality_statuses ?? [])
        : (source?.point_quality_statuses ?? []);
      const boundary = source?.provisional_from ?? entry.provisional_from;
      const observations: Observation[] = dates
        .flatMap((date, index) => {
          if (!validDate(date)) return [];
          const raw = values[index];
          const value = raw != null && Number.isFinite(raw) ? raw : null;
          const period = periodFor(
            date,
            isDefault
              ? cadence(entry.point_granularities?.[index] ?? grain)
              : grain,
          );
          const start = (
              isDefault ? entry.period_starts : source?.period_starts
            )?.[index],
            end = (
              isDefault
                ? entry.period_ends_exclusive
                : source?.period_ends_exclusive
            )?.[index];
          if (
            start &&
            end &&
            validDate(start) &&
            validDate(end) &&
            start < end
          ) {
            period.start = start;
            period.endExclusive = end;
            period.precision = "exact";
            period.calendar =
              (isDefault ? entry.calendar : source?.calendar) === "iso-week"
                ? "iso-week"
                : (isDefault ? entry.calendar : source?.calendar) ===
                    "gregorian"
                  ? "gregorian"
                  : "source";
          }
          const statuses = quality[index]
            ? quality[index]
                .split(";")
                .filter((status) => Boolean(status) && status !== "raw")
            : boundary && date >= boundary
              ? ["provisional"]
              : [];
          return [
            {
              period,
              value,
              quality: statuses,
              missingReason: value === null ? "not_reported" : null,
              sourceId: isDefault
                ? (entry.point_series_codes?.[index] ?? sourceId)
                : sourceId,
              definitionId,
            },
          ];
        })
        .sort((a, b) => a.period.label.localeCompare(b.period.label));
      const cmp = metadata?.comparability ?? entry.comparability;
      const series: SurveillanceSeries = {
        id,
        selectionId,
        entityId,
        measure,
        unit:
          measure === "incidence_rates"
            ? "per_100k_source_period"
            : measure === "source_value"
              ? (metadata?.unit ?? "source_unit")
              : "count",
        valueKind: valueKind(metadata?.metric_type ?? null, measure),
        aggregationPolicy: nullable(
          metadata?.aggregation_policy ?? entry.aggregation_policy,
        ),
        cadence: grain,
        displayCadence: displayGrain,
        cadenceBasis: isDefault
          ? entry.period_granularity_basis
          : source?.temporal_granularity ? "source" : "unknown",
        dimensions: {
          ...dimensions,
          sourceDimension: metadata?.dimension_key ?? "all",
        },
        timeBasis: nullable(metadata?.time_basis ?? entry.time_basis),
        reportingBasis: nullable(
          metadata?.reporting_basis ?? entry.reporting_basis,
        ),
        definitionId,
        comparability:
          cmp === "direct" || cmp === "conditional" || cmp === "not_comparable"
            ? cmp
            : "unknown",
        observations,
        events: [],
        loading: !isDefault && !source?.dates ? "unloaded" : "ready",
        provenance: {
          sourceId,
          label: metadata?.source_label ?? sourceId,
          url: metadata?.case_definition_uri ?? null,
          dataLayer: isDefault
            ? (entry.data_layer ?? "legacy")
            : "source_series",
          policy: isDefault
            ? (entry.projection_policy ?? null)
            : (metadata?.aggregation_policy ?? null),
          denominatorMethod: measure === "incidence_rates" ? "published" : null,
          snapshotVersion: (entry as CountryDatasetSeriesEntry & { snapshot_version?: string }).snapshot_version,
        },
      };
      if (metadata?.definition_effective_from)
        series.events.push({
          date: metadata.definition_effective_from,
          label: "definition_change",
        });
      if (isDefault) {
        let previous: string | null = null;
        for (const observation of observations) {
          if (previous && previous !== observation.sourceId)
            series.events.push({
              date: observation.period.label,
              label: "source_change",
            });
          previous = observation.sourceId;
        }
      }
      catalog.series[id] = series;
      catalog.selections[selectionId] ??= {
        id: selectionId,
        entityId,
        sourceLabel: isDefault
          ? null
          : [
              source?.source_label ?? sourceId,
              ...Object.entries(dimensions).map(([key, value]) => `${key}: ${value}`),
            ].join(" · "),
        isDefault,
        seriesIds: [],
      };
      catalog.selections[selectionId].seriesIds.push(id);
    };
    for (const measure of ["cases", "deaths", "incidence_rates"] as const) {
      const values = entry[measure] ?? [];
      if (
        values.length === entry.dates.length &&
        values.some((value) => value != null)
      )
        add(undefined, measure, values, entry.dates, true);
    }
    for (const source of entry.source_series ?? []) {
      if (!source.series_code) continue;
      const measure = sourceMeasure(source);
      add(source, measure, source.values ?? [], source.dates ?? [], false);
      if (measure === "cases") {
        const rates = sourceRates(entry, source);
        if (rates || source.has_incidence_rates) {
          add(
            source,
            "incidence_rates",
            rates?.values ?? [],
            source.dates ?? [],
            false,
          );
          const key = `${encodeURIComponent(entityId)}::${encodeURIComponent(source.series_code)}::incidence_rates`;
          catalog.series[key].provenance.denominatorMethod =
            rates?.method ?? "published";
        }
      }
    }
  }
  catalog.version = fingerprint({
    series: catalog.series,
    recordedAtAsOf: catalog.recordedAtAsOf,
    recordedHistoryComplete: catalog.recordedHistoryComplete,
  });
  return catalog;
}
