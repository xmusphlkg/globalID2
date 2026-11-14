import type { DataSourceFlow, OntologySeries } from "@/features/operations/sources/api";

export interface SeriesMappingRisk {
  key: string;
  conceptId: string;
  sourceId: string;
  frequency: string;
  measure: string;
  rows: OntologySeries[];
  labels: string[];
}

export function flowRowKey(flow: DataSourceFlow) {
  return [
    flow.country_code || flow.country_name || "unknown",
    flow.source_scope || "default",
    flow.data_source,
  ].join("::");
}

export function buildSeriesMappingRisks(rows: OntologySeries[]): SeriesMappingRisk[] {
  const groups = new Map<string, OntologySeries[]>();
  for (const row of rows) {
    const conceptId = row.concept_id || row.target?.id || "unmapped";
    const key = [
      conceptId,
      row.source_id,
      row.frequency,
      row.measure,
      row.reporting_basis,
    ].join("|");
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }

  const risks: SeriesMappingRisk[] = [];
  for (const [key, groupedRows] of groups) {
    if (groupedRows.length < 2) continue;
    const labels = Array.from(
      new Set(
        groupedRows
          .flatMap((row) => row.local_labels ?? [])
          .map((label) => label.trim())
          .filter(Boolean),
      ),
    );
    const normalizedLabels = new Set(labels.map((label) => label.toLocaleLowerCase()));
    if (normalizedLabels.size < 2) continue;
    const first = groupedRows[0];
    risks.push({
      key,
      conceptId: first.concept_id || first.target?.id || "unmapped",
      sourceId: first.source_id,
      frequency: first.frequency,
      measure: first.measure,
      rows: groupedRows,
      labels,
    });
  }

  return risks.sort((left, right) => {
    const leftNonCase = left.measure === "registered_diagnoses" ? 1 : 0;
    const rightNonCase = right.measure === "registered_diagnoses" ? 1 : 0;
    return leftNonCase - rightNonCase || left.conceptId.localeCompare(right.conceptId);
  });
}
