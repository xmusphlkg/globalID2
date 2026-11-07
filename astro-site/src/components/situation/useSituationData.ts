import { useEffect, useState } from 'react';
import { situationData } from '../../lib/situation-data';

export function useSituationData<T>(path: string) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ data?: T; version?: string; error?: boolean }>({});
  useEffect(() => {
    let live = true;
    setState({});
    Promise.all([situationData.read<T & { schema_version?: string }>(path), situationData.manifest()])
      .then(([data, manifest]) => {
        if (data?.schema_version !== 'gids.situation_alerts.v1') throw new Error('Invalid situation data');
        if (live) setState({ data, version: manifest?.release_id });
      }).catch(() => { if (live) setState({ error: true }); });
    return () => { live = false; };
  }, [path, attempt]);
  return { ...state, retry: () => setAttempt(value => value + 1) };
}
