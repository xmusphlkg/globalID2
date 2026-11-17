import { useEffect, useMemo, useState } from 'react';
import { situationData } from '../../lib/situation-data.ts';
import type { MapPoint, SituationAlertRecord } from '../../utils/situationTypes.ts';
import type { Filters } from './model.ts';
import { loadOverview, overviewPaths, type OverviewFile } from './overview-data.ts';

export function useSituationOverview(enabled: boolean, points: MapPoint[], filters: Filters, asOf: string) {
  const paths = useMemo(() => overviewPaths(points, filters, asOf), [points, filters, asOf]);
  const legacyPaths = useMemo(() => overviewPaths(points, filters, asOf, false), [points, filters, asOf]);
  const scope = JSON.stringify([paths, legacyPaths]);
  const [state, setState] = useState({ scope: '', alerts: [] as SituationAlertRecord[], loading: false, failed: 0 });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setState({ scope, alerts: [], loading: paths.length > 0, failed: 0 });
    void (async () => {
      if (!paths.length) return;
      const publication = await situationData.manifest({ signal: controller.signal });
      if (controller.signal.aborted) return;
      // Entity shards belong to independent publications. Legacy Astro routes
      // expose outbreak shards only; never escape a pinned release on failure.
      const useEntities = publication && (publication.manifest || paths.every(path => publication.files[path.slice('/situation/'.length)]));
      await loadOverview(useEntities ? paths : legacyPaths,
        (path, signal) => situationData.read<OverviewFile>(path, { signal }), controller.signal,
        (alerts, failed) => setState({ scope, alerts, failed, loading: true }));
    })()
      .catch(() => {
        if (!controller.signal.aborted) setState({ scope, alerts: [], failed: 1, loading: false });
      })
      .finally(() => {
        if (!controller.signal.aborted) setState(previous => ({ ...previous, loading: false }));
      });
    return () => controller.abort();
    // Identical shard sets reuse their pinned responses while filters update the view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, scope, attempt]);
  return { ...(state.scope === scope ? state : { alerts: [], failed: 0, loading: enabled && paths.length > 0 }),
    retry: () => setAttempt(value => value + 1) };
}
