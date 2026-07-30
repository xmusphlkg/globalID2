import { useCallback, useEffect, useState } from 'react';
import {
  invalidateCountryDataset,
  loadCountryDataset,
  type CountryDataset,
} from './countryDataset';

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

interface CountryDatasetState {
  data: CountryDataset | null;
  status: LoadStatus;
}

export function useCountryDataset(dataUrl?: string | null, enabled = true) {
  const [retryKey, setRetryKey] = useState(0);
  const [state, setState] = useState<CountryDatasetState>({
    data: null,
    status: enabled && dataUrl ? 'loading' : 'idle',
  });

  useEffect(() => {
    if (!enabled || !dataUrl) {
      setState({ data: null, status: 'idle' });
      return;
    }

    let cancelled = false;
    setState((current) => ({ data: current.data, status: 'loading' }));

    loadCountryDataset(dataUrl)
      .then((data) => {
        if (!cancelled) setState({ data, status: 'ready' });
      })
      .catch(() => {
        if (!cancelled) setState((current) => ({ data: current.data, status: 'error' }));
      });

    return () => {
      cancelled = true;
    };
  }, [dataUrl, enabled, retryKey]);

  const retry = useCallback(() => {
    if (dataUrl) invalidateCountryDataset(dataUrl);
    setRetryKey((current) => current + 1);
  }, [dataUrl]);

  return {
    data: state.data,
    isLoading: state.status === 'loading',
    loadError: state.status === 'error',
    retry,
    status: state.status,
  };
}
