import type { MapPoint, OutbreakFile, SituationAlertRecord } from '../../utils/situationTypes.ts';
import { filterPoints, type Filters } from './model.ts';

/** Load only the shards that can contain results, within the page's pinned publication. */
export function overviewPaths(points: MapPoint[], filters: Filters, asOf: string, preferEntityShards = true): string[] {
  const matching = filterPoints(points, filters, asOf);
  if (!matching.length) return [];
  if (preferEntityShards && /^D\d+$/.test(filters.disease)) return [`/situation/by-disease/${filters.disease}.json`];
  if (preferEntityShards && filters.country) return [`/situation/by-country/${encodeURIComponent(filters.country)}.json`];
  return [...new Set(matching.map(point => point.outbreak_id
    ? `/situation/outbreaks/${encodeURIComponent(point.outbreak_id)}.json`
    : `/situation/by-country/${encodeURIComponent(point.country_code)}.json`))].sort();
}

export type OverviewFile = Pick<OutbreakFile, 'alerts'>;

/** A small worker pool bounds network work and lets stale filter requests stop promptly. */
export async function loadOverview(
  paths: string[],
  read: (path: string, signal: AbortSignal) => Promise<OverviewFile>,
  signal: AbortSignal,
  onProgress: (alerts: SituationAlertRecord[], failed: number) => void,
): Promise<void> {
  let cursor = 0;
  let failed = 0;
  const alerts = new Map<string, SituationAlertRecord>();
  const worker = async () => {
    while (!signal.aborted && cursor < paths.length) {
      const path = paths[cursor++];
      try {
        const file = await read(path, signal);
        if (!Array.isArray(file.alerts)) throw new Error('Invalid situation timeline');
        if (signal.aborted) return;
        for (const alert of file.alerts) alerts.set(alert.alert_id, alert);
      } catch {
        if (signal.aborted) return;
        failed += 1;
      }
      onProgress([...alerts.values()], failed);
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, paths.length) }, worker));
}
