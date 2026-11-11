import type { AnalysisResult, Catalog } from '../components/charts/surveillance/domain/types.ts';

export interface ScopedSource { label: string; url?: string; }

/** Match authority metadata to the curves currently on screen, never the first
 * authorities in a worldwide disease catalogue. Unknown geography stays unknown. */
export function chartSourceScope(catalog: Catalog, result: AnalysisResult) {
  const geographies = [...new Set(result.lines.flatMap(line => {
    const id = catalog.entities[line.entityId]?.geographyId;
    return id ? [id.toUpperCase()] : [];
  }))];
  const dates = result.lines.flatMap(line => line.windowPoints
    .filter(point => point.value !== null).map(point => point.date)).sort();
  const provenance = result.lines.flatMap(line => {
    const { label, url } = line.provenance;
    return label && !['published', 'unknown'].includes(label.toLowerCase())
      ? [{ label, ...(safeSourceUrl(url) ? { url: safeSourceUrl(url) } : {}) }] : [];
  });
  return { geographies, dates, provenance };
}

function safeSourceUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return;
  try { return /^https?:$/.test(new URL(value).protocol) ? value : undefined; } catch { return; }
}

export function scopedChartSources(info: unknown, geographies: string[], provenance: ScopedSource[] = []): ScopedSource[] {
  if (!geographies.length) return [];
  const groups = Array.isArray(info) ? info : info && typeof info === 'object' ? [info] : [];
  const selected = new Set(geographies.map(code => code.toUpperCase()));
  const geographicGroups = groups.some(group => group?.country_code);
  const sources: ScopedSource[] = [...provenance];
  for (const group of groups) {
    if (!group || typeof group !== 'object') continue;
    if (geographicGroups && !selected.has(String(group.country_code ?? '').toUpperCase())) continue;
    const rows = Array.isArray(group.sources) && group.sources.length ? group.sources
      : [{ label: group.primary_label, url: group.primary_url }];
    for (const row of rows) {
      if (row.country_code && !selected.has(String(row.country_code).toUpperCase())) continue;
      const label = String(row.label || row.scope || group.primary_label || '').trim();
      if (!label) continue;
      const url = safeSourceUrl(row.url);
      sources.push({ label, ...(url ? { url } : {}) });
    }
  }
  return [...new Map(sources.map(source => [`${source.label}\0${source.url || ''}`, source])).values()];
}
