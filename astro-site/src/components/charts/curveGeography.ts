import type { DiseaseDatasetSeriesEntry } from './diseaseDataset';

export type CurveGeographyScope = 'national' | 'mixed' | `subdivisions:${string}`;

export function subdivisionParent(code: string): string | null {
  return /^([A-Z]{2})-[A-Z0-9]+$/i.exec(code)?.[1].toUpperCase() ?? null;
}

export function geographyScopeIds(
  series: Record<string, DiseaseDatasetSeriesEntry>,
  scope: CurveGeographyScope,
  allowedIds?: string[],
): string[] {
  const allowed = allowedIds ? new Set(allowedIds) : null;
  return Object.keys(series).filter((id) => {
    if (allowed && !allowed.has(id)) return false;
    if (scope === 'mixed') return true;
    const parent = subdivisionParent(id);
    return scope === 'national' ? !parent : parent === scope.slice('subdivisions:'.length);
  });
}

export function availableSubdivisionParents(
  series: Record<string, DiseaseDatasetSeriesEntry>,
  allowedIds?: string[],
): string[] {
  return [...new Set(geographyScopeIds(series, 'mixed', allowedIds)
    .map(subdivisionParent).filter((parent): parent is string => parent != null))].sort();
}
