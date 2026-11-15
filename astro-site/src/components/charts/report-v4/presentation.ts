import { marked } from 'marked';
import { type Lang, type AnyRecord, type SparklineSeriesEntry } from './types.ts';

export function asRecord(value: unknown): AnyRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as AnyRecord : {};
}

export function asArray(value: unknown): any[] {
  return Array.isArray(value) ? value : [];
}

export function ui(lang: Lang, en: string, zh: string, fr: string): string {
  return lang === 'zh' ? zh : lang === 'fr' ? fr : en;
}

export function localized(value: unknown, lang: Lang, fallback = ''): string {
  const record = asRecord(value);
  const direct = record[lang];
  if (typeof direct === 'string' && direct.trim()) return direct;
  if (typeof value === 'string' && value.trim()) return value;
  if (fallback) return fallback;
  return ui(lang, 'Translation pending.', '翻译待补充。', 'Traduction française en attente.');
}

export function localizedList(value: unknown, lang: Lang): string[] {
  const direct = asRecord(value)[lang];
  if (Array.isArray(direct)) return direct.map(String);
  if (Array.isArray(value)) return value.map(String);
  const record = asRecord(value);
  return Object.keys(record).length > 0
    ? [ui(lang, 'Translation pending.', '翻译待补充。', 'Traduction française en attente.')]
    : [];
}

export function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function markdownHtml(value: string): string {
  return marked.parse(escapeHtml(value || ''), {
    async: false,
    breaks: false,
    gfm: true,
  }) as string;
}

export function fmtNumber(value: unknown, lang: Lang = 'en'): string {
  const parsed = value == null || value === '' ? NaN : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return parsed.toLocaleString(lang === 'zh' ? 'zh-CN' : lang === 'fr' ? 'fr-FR' : 'en-US');
}

export function percent(value: unknown, lang: Lang = 'en'): string {
  const parsed = value == null || value === '' ? NaN : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return new Intl.NumberFormat(lang === 'zh' ? 'zh-CN' : lang === 'fr' ? 'fr-FR' : 'en-US', {
    style: 'percent',
    maximumFractionDigits: 0,
  }).format(parsed);
}

export function changePct(value: unknown, lang: Lang = 'en'): string {
  const parsed = value == null || value === '' ? NaN : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  const digits = Math.abs(parsed) >= 10 ? 0 : 1;
  const formatted = parsed.toLocaleString(lang === 'zh' ? 'zh-CN' : lang === 'fr' ? 'fr-FR' : 'en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${parsed > 0 ? '+' : ''}${formatted}${lang === 'fr' ? ' ' : ''}%`;
}

export function attentionLabel(value: unknown, lang: Lang): string {
  const key = String(value || 'low').toLowerCase();
  if (lang === 'en') return key;
  const labels = lang === 'fr'
    ? { critical: 'critique', high: 'élevée', moderate: 'modérée', low: 'faible' }
    : { critical: '极高', high: '高', moderate: '中等', low: '低' };
  return (labels as Record<string, string>)[key] || ui(lang, key, '未知', 'inconnue');
}

export function deathLabel(deathReporting: AnyRecord, lang: Lang): string {
  const status = String(deathReporting.status || 'unknown');
  if (status === 'not_reported') return ui(lang, 'Deaths not reported', '死亡数未提供', 'Décès non déclarés');
  if (status === 'unknown') return ui(lang, 'Death scope unknown', '死亡口径未知', 'Périmètre des décès inconnu');
  if (status === 'partial') return ui(lang, 'Deaths partially available', '死亡数部分可用', 'Données partielles sur les décès');
  const total = deathReporting.total_deaths;
  return `${ui(lang, 'Deaths', '死亡', 'Décès')} ${fmtNumber(total, lang)}`;
}

export function sectionLabel(value: unknown, lang: Lang): string {
  const key = String(value || '');
  const labels: Record<string, Record<Lang, string>> = {
    decision_summary: { zh: '当前判断', en: 'Current judgement', fr: 'Évaluation actuelle' },
    peer_reviewed_interpretation: { zh: 'AI 同行评审解读', en: 'Peer-reviewed AI interpretation', fr: 'Interprétation IA évaluée par les pairs' },
    priority_actions: { zh: '建议动作', en: 'Priority actions', fr: 'Actions prioritaires' },
    signal_evidence: { zh: '关键证据', en: 'Signal evidence', fr: 'Preuves relatives aux signaux' },
    disease_context: { zh: '疾病背景', en: 'Disease context', fr: 'Contexte des maladies' },
    data_interpretation_notes: { zh: '数据口径', en: 'Data notes', fr: 'Notes sur les données' },
    method_appendix: { zh: '方法附录', en: 'Method appendix', fr: 'Annexe méthodologique' },
  };
  return labels[key]?.[lang] || ui(lang, key || 'Report section', '报告章节', 'Section du rapport');
}

export function trendLabel(row: AnyRecord, lang: Lang): string {
  const trend = asRecord(row.trend);
  const direct = trend[lang];
  if (typeof direct === 'string' && direct.trim()) return direct;
  return ui(lang, 'Watch', '待观察', 'À surveiller');
}

export function trendDirection(row: AnyRecord): string {
  const direction = String(asRecord(row.trend).direction || 'watch').toLowerCase();
  return direction === 'flat' ? 'stable' : direction;
}

export function categoryLabel(value: unknown, lang: Lang): string {
  const key = String(value || 'Other');
  if (lang === 'en') return key || 'Other';
  const labels = lang === 'fr'
    ? { Viral: 'Virale', Bacterial: 'Bactérienne', Parasitic: 'Parasitaire', Fungal: 'Fongique', Prion: 'Prion', Other: 'Autre' }
    : { Viral: '病毒性', Bacterial: '细菌性', Parasitic: '寄生虫性', Fungal: '真菌性', Prion: '朊病毒', Other: '其他' };
  return (labels as Record<string, string>)[key] || ui(lang, 'Other', '其他', 'Autre');
}

export function attentionRank(value: unknown): number {
  const key = String(value || 'low').toLowerCase();
  return ({ critical: 4, high: 3, moderate: 2, low: 1 } as Record<string, number>)[key] || 1;
}

export function attentionClass(value: unknown): string {
  const key = String(value || 'low').toLowerCase();
  if (key === 'critical') return 'border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300';
  if (key === 'high') return 'border-orange-500/50 bg-orange-500/10 text-orange-700 dark:text-orange-300';
  if (key === 'moderate') return 'border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300';
  return 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300';
}

export function trendBackgroundClass(row: AnyRecord): string {
  const direction = trendDirection(row);
  if (direction === 'up') return 'bg-red-500/10';
  if (direction === 'down') return 'bg-emerald-500/10';
  if (direction === 'stable') return 'bg-sky-500/10';
  return 'bg-[rgb(var(--surface))]';
}

export function changeClass(value: unknown): string {
  const parsed = value == null || value === '' ? NaN : Number(value);
  if (!Number.isFinite(parsed)) return 'text-[rgb(var(--text-muted))]';
  if (parsed >= 50) return 'text-red-700 dark:text-red-300';
  if (parsed > 0) return 'text-orange-700 dark:text-orange-300';
  if (parsed < 0) return 'text-emerald-700 dark:text-emerald-300';
  return 'text-[rgb(var(--text-muted))] dark:text-[rgb(var(--text-muted))]';
}

export function directionFromChange(value: unknown): string {
  const parsed = value == null || value === '' ? NaN : Number(value);
  if (!Number.isFinite(parsed)) return 'watch';
  if (parsed > 0) return 'up';
  if (parsed < 0) return 'down';
  return 'stable';
}

export function attentionScore(value: unknown): string {
  const parsed = value == null || value === '' ? NaN : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return parsed.toFixed(0);
}

export function currentYearLabel(record: SparklineSeriesEntry | undefined, lang: Lang): string {
  const year = record?.current_year || (Array.isArray(record?.dates) ? String(record?.dates.at(-1) || '').slice(0, 4) : '');
  if (!year || year.length !== 4) return ui(lang, 'YTD cumulative', '当年累计', 'Cumul annuel');
  return ui(lang, `${year} YTD`, `${year}累计`, `Cumul ${year}`);
}

export function curveColorClass(direction?: string, tone: 'monthly' | 'annual' = 'monthly'): string {
  if (tone === 'annual') return 'text-brand-600 dark:text-brand-300';
  if (direction === 'down') return 'text-emerald-600 dark:text-emerald-300';
  if (direction === 'stable') return 'text-sky-600 dark:text-sky-300';
  if (direction === 'up') return 'text-red-600 dark:text-red-300';
  return 'text-[rgb(var(--text-muted))] dark:text-[rgb(var(--text-muted))]';
}
