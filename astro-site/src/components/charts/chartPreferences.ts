import { useEffect, useState } from 'react';

export type ChartLanguage = 'en' | 'zh';
export type ChartTheme = 'light' | 'dark';

function readLanguage(): ChartLanguage {
  if (typeof document === 'undefined') return 'en';

  const documentLanguage = document.documentElement.getAttribute('data-lang');
  if (documentLanguage === 'zh' || documentLanguage === 'en') {
    return documentLanguage;
  }

  try {
    return localStorage.getItem('lang') === 'zh' ? 'zh' : 'en';
  } catch {
    return 'en';
  }
}

function readTheme(): ChartTheme {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

export function useChartLanguage(): ChartLanguage {
  // Keep the server and first client render deterministic so charts can use
  // Astro's visibility-based hydration without a language mismatch.
  const [language, setLanguage] = useState<ChartLanguage>('en');

  useEffect(() => {
    const updateLanguage = () => setLanguage(readLanguage());
    updateLanguage();
    document.addEventListener('globalid:language-change', updateLanguage);
    window.addEventListener('storage', updateLanguage);
    return () => {
      document.removeEventListener('globalid:language-change', updateLanguage);
      window.removeEventListener('storage', updateLanguage);
    };
  }, []);

  return language;
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
