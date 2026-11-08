import { useEffect, useState } from 'react';
import { surveillanceData } from '../../lib/surveillance-data.ts';
import type { Summary } from '../../lib/surveillance-presentation.ts';

export function useSurveillanceSummary(dataUrl?: string) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ data?: Summary; version?: string; error?: boolean }>({});
  useEffect(() => {
    let cancelled = false;
    setState({});
    if (!dataUrl) return;
    Promise.all([surveillanceData.read<Summary>(dataUrl), surveillanceData.manifest()]).then(([data, manifest]) => {
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid surveillance summary');
      if (!cancelled) setState({ data, version: manifest?.release_id });
    }).catch(() => { if (!cancelled) setState({ error: true }); });
    return () => { cancelled = true; };
  }, [dataUrl, attempt]);
  return { ...state, retry: () => setAttempt(value => value + 1) };
}
