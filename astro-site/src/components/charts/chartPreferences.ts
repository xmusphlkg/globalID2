import { useEffect, useState } from 'react';

export type ChartLanguage = 'en' | 'zh';
export type ChartTheme = 'light' | 'dark';

function readTheme(): ChartTheme {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

export function useChartLanguage(initialLanguage: ChartLanguage = 'en'): ChartLanguage {
  return initialLanguage;
}

export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>('light');

  useEffect(() => {
    const root = document.documentElement;
    const updateTheme = () => setTheme(readTheme());
    updateTheme();
    const observer = new MutationObserver(updateTheme);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  return theme;
}
