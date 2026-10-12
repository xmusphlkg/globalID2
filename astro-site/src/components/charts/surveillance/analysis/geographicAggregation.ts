import {
  fingerprint,
  type AnalysisConfig,
  type Catalog,
  type Diagnostic,
  type Period,
  type ResultLine,
  type ResultPoint,
} from "../domain/types.ts";
import { addDiagnostic, finite } from "./periodTransforms.ts";

function geographyIsOverlapping(catalog: Catalog, selectionIds: string[]): boolean {
  const entities = selectionIds
    .map((id) => catalog.entities[catalog.selections[id]?.entityId])
    .filter(Boolean);
  if (entities.length !== selectionIds.length) return true;
  if (new Set(entities.map((entity) => entity.geographyId)).size !== entities.length)
    return true;
  if (entities.some((entity) => entity.geographyLevel === "unknown")) return true;

  const selectedGeographies = new Set(entities.map((entity) => entity.geographyId));
  return entities.some((entity) => {
    const visited = new Set<string>();
    let parentId = entity.parentId;
    while (parentId && !visited.has(parentId)) {
      if (selectedGeographies.has(parentId)) return true;
      visited.add(parentId);
      parentId = catalog.entities[parentId]?.parentId ?? null;
    }
    return false;
  });
}

function periodIdentity(period: Period): string | null {
  if (
    !period.start ||
    !period.endExclusive ||
    period.calendar === "unknown" ||
    period.precision === "unknown"
  )
    return null;
  return JSON.stringify([period.start, period.endExclusive, period.calendar]);
}

function dimensionsIdentity(dimensions: Record<string, string>): string {
  return JSON.stringify(Object.entries(dimensions).sort(([a], [b]) => a.localeCompare(b)));
}

function compatibleGroup(
  catalog: Catalog,
  members: ResultLine[],
): boolean {
  if (members.length < 2) return false;
  const first = members[0];
  const firstSeries = catalog.series[first.seriesId];
  if (
    !firstSeries ||
    !["cases", "deaths"].includes(first.measure) ||
    firstSeries.valueKind !== "interval_count" ||
    firstSeries.comparability !== "direct" ||
    !firstSeries.reportingBasis ||
    !firstSeries.timeBasis ||
    !firstSeries.definitionId
  )
    return false;
  if (
    members.some((line) =>
      line.points.some((point) => periodIdentity(point.period) === null),
    )
  )
    return false;
  const dimensions = dimensionsIdentity(firstSeries.dimensions);
  return members.every((line) => {
    const series = catalog.series[line.seriesId];
    return Boolean(
      series &&
        line.measure === first.measure &&
        line.unit === first.unit &&
        line.cadence === first.cadence &&
        series.valueKind === "interval_count" &&
        series.comparability === "direct" &&
        series.reportingBasis === firstSeries.reportingBasis &&
        series.timeBasis === firstSeries.timeBasis &&
        series.definitionId === firstSeries.definitionId &&
        dimensionsIdentity(series.dimensions) === dimensions,
    );
  });
}

function aggregateLine(
  catalog: Catalog,
  groupId: string,
  members: ResultLine[],
  diagnostics: Diagnostic[],
): ResultLine {
  const first = members[0];
  const memberIndexes = members.map(
    (line) =>
      new Map(
        line.points.map((point) => [periodIdentity(point.period), point]),
      ),
  );
  const periodById = new Map<string, Period>();
  for (const line of members)
    for (const point of line.points) {
      const key = periodIdentity(point.period);
      if (key) periodById.set(key, point.period);
    }

  let incomplete = false;
  const points: ResultPoint[] = [...periodById.entries()]
    .sort(([, a], [, b]) => (a.start ?? "").localeCompare(b.start ?? ""))
    .map(([key, period]) => {
      const rows = memberIndexes.map((index) => index.get(key));
      const quality = [...new Set(rows.flatMap((point) => point?.quality ?? []))];
      const complete = rows.every(
        (point) =>
          point &&
          finite(point.value) &&
          !point.quality.some((status) =>
            ["partial_report_window", "period_scope_mismatch"].includes(status),
          ),
      );
      if (!complete) {
        incomplete = true;
        quality.push("aggregate_incomplete");
      }
      return {
        date: period.start ?? period.label,
        period,
        value: complete
          ? rows.reduce((sum, point) => sum + point!.value!, 0)
          : null,
        quality: [...new Set(quality)],
      };
    });

  if (incomplete)
    for (const line of members)
      addDiagnostic(diagnostics, "aggregate_incomplete", line.seriesId, "warning");

  const series = catalog.series[first.seriesId];
  const entity = catalog.entities[first.entityId];
  const memberNames = members.map(
    (line) => catalog.entities[line.entityId]?.labels.en ?? line.entityId,
  );
  return {
    ...first,
    id: `aggregate:${groupId}:${first.measure}`,
    labelSuffix: "",
    points,
    windowPoints: [],
    additive: members.every((line) => line.additive),
    aggregateMembers: members.map((line) => line.selectionId),
    provenance: {
      sourceId: "derived:geographic_sum",
      label: `Sum of ${memberNames.join(", ")}`,
      url: null,
      dataLayer: "derived",
      policy: "sum_complete_non_overlapping_geographies",
      denominatorMethod: null,
      ...(members.every(
        (line) =>
          line.provenance.snapshotVersion ===
          members[0].provenance.snapshotVersion,
      ) && members[0].provenance.snapshotVersion
        ? { snapshotVersion: members[0].provenance.snapshotVersion }
        : {}),
    },
    events: members.flatMap((line) =>
      line.events.map((event) => ({
        date: event.date,
        label: `${catalog.entities[line.entityId]?.labels.en ?? line.entityId}: ${event.label}`,
      })),
    ),
    // Keep the catalog-backed representative identity for method/provenance
    // lookup. The derived label and membership remain explicit on the line.
    entityId: entity?.id ?? first.entityId,
    seriesId: series?.id ?? first.seriesId,
  };
}

/** Replace valid disjoint geography selections with complete-period sums. */
export function applyGeographicAggregation(
  catalog: Catalog,
  config: AnalysisConfig,
  lines: ResultLine[],
  diagnostics: Diagnostic[],
): ResultLine[] {
  if (!config.aggregationGroups.length) return lines;
  if (
    config.task !== "burden" ||
    config.smoothing !== 1 ||
    config.measures.some((measure) => !["cases", "deaths"].includes(measure))
  ) {
    for (const selectionId of config.aggregationGroups.flatMap(
      (group) => group.selectionIds,
    ))
      addDiagnostic(
        diagnostics,
        "aggregate_method_unsupported",
        selectionId,
        "info",
      );
    return lines;
  }

  let output = [...lines];
  for (const group of config.aggregationGroups) {
    const sources = group.selectionIds.flatMap((selectionId) =>
      (catalog.selections[selectionId]?.seriesIds ?? [])
        .map((id) => catalog.series[id])
        .filter((series) => config.measures.includes(series.measure)),
    );
    const groupLines = lines.filter((line) =>
      group.selectionIds.includes(line.selectionId),
    );
    if (geographyIsOverlapping(catalog, group.selectionIds)) {
      for (const source of sources)
        addDiagnostic(
          diagnostics,
          "aggregate_geography_overlap",
          source.id,
          "error",
        );
      continue;
    }

    let groupValid = true;
    for (const measure of config.measures) {
      const members = groupLines.filter((line) => line.measure === measure);
      const expected = group.selectionIds.length;
      if (members.length !== expected) {
        groupValid = false;
        for (const source of sources.filter((item) => item.measure === measure))
          addDiagnostic(
            diagnostics,
            "aggregate_member_unavailable",
            source.id,
            "error",
            measure,
          );
        continue;
      }
      if (!compatibleGroup(catalog, members)) {
        groupValid = false;
        for (const member of members)
          addDiagnostic(
            diagnostics,
            "aggregate_incompatible",
            member.seriesId,
            "error",
            measure,
          );
        continue;
      }
      if (members.some((line) => line.aggregateMembers?.length)) {
        groupValid = false;
        continue;
      }
    }
    if (!groupValid) continue;

    const aggregates = config.measures.map((measure) =>
      aggregateLine(
        catalog,
        fingerprint(group.selectionIds.slice().sort()),
        groupLines.filter((line) => line.measure === measure),
        diagnostics,
      ),
    );
    output = [
      ...output.filter((line) => !group.selectionIds.includes(line.selectionId)),
      ...aggregates,
    ];
  }
  return output;
}
