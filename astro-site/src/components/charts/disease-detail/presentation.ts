

export function ui(lang: 'en' | 'zh' | 'fr', en: string, zh: string, fr: string): string {
  return lang === 'zh' ? zh : lang === 'fr' ? fr : en;
}

// ─── Chart color tokens ───────────────────────────────────────────────────────

export function chartTokens(theme: 'light' | 'dark') {
  return theme === 'light' ? {
    font: '#475569',
    grid: '#e2e8f0',
    line: '#cbd5e1',
    tick: '#94a3b8',
    bg: 'transparent',
    sliderBg: '#f8fafc',
    legendBg: 'rgba(255,255,255,0.92)',
    legendBorder: '#cbd5e1',
    tooltipBg: '#ffffff',
    tooltipBorder: '#e2e8f0',
    tooltipFont: '#0f172a',
    casesColor: '#2563eb',    // blue-600
    casesAreaA: 'rgba(37,99,235,0.18)',
    casesAreaB: 'rgba(37,99,235,0.02)',
    deathsColor: '#dc2626',   // red-600
    barColor: '#3b82f6',
    barColorAlt: '#0d9488',
  } : {
    font: '#94a3b8',
    grid: '#1e293b',
    line: '#334155',
    tick: '#475569',
    bg: 'transparent',
    sliderBg: '#0f172a',
    legendBg: 'rgba(15,23,42,0.85)',
    legendBorder: '#334155',
    tooltipBg: '#1e293b',
    tooltipBorder: '#334155',
    tooltipFont: '#e2e8f0',
    casesColor: '#60a5fa',    // blue-400
    casesAreaA: 'rgba(96,165,250,0.18)',
    casesAreaB: 'rgba(96,165,250,0.02)',
    deathsColor: '#f87171',   // red-400
    barColor: '#3b82f6',
    barColorAlt: '#2dd4bf',
  };
}

// ─── Number formatter ─────────────────────────────────────────────────────────

export function fmtNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K';
  return n.toLocaleString();
}

export function fmtDate(d: string, lang: 'en' | 'zh' | 'fr') {
  return new Date(d).toLocaleDateString(lang === 'zh' ? 'zh-CN' : lang === 'fr' ? 'fr-FR' : 'en-US', { year: 'numeric', month: 'short' });
}

// ─── Category badge ───────────────────────────────────────────────────────────

export const CATEGORY_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  Viral:     { bg: 'rgba(37,99,235,0.1)',   text: '#60a5fa', border: 'rgba(37,99,235,0.25)' },
  Bacterial: { bg: 'rgba(245,158,11,0.1)',  text: '#fbbf24', border: 'rgba(245,158,11,0.25)' },
  Parasitic: { bg: 'rgba(16,185,129,0.1)',  text: '#34d399', border: 'rgba(16,185,129,0.25)' },
  Fungal:    { bg: 'rgba(167,139,250,0.1)', text: '#a78bfa', border: 'rgba(167,139,250,0.25)' },
};

export function categoryLabel(value: string, lang: 'en' | 'zh' | 'fr') {
  if (lang === 'en') return value || 'Unclassified';
  const labels = lang === 'fr'
    ? { Viral: 'Virale', Bacterial: 'Bactérienne', Parasitic: 'Parasitaire', Fungal: 'Fongique' }
    : { Viral: '病毒性', Bacterial: '细菌性', Parasitic: '寄生虫性', Fungal: '真菌性' };
  return (labels as Record<string, string>)[value] || ui(lang, 'Unclassified', '未分类', 'Non classée');
}
