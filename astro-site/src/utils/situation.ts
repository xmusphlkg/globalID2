export type SituationRow = Record<string, any>;

const RISK_RANK: Record<string, number> = {
  very_high: 4,
  high: 3,
  moderate: 2,
  low: 1,
};

export function rows(value: unknown): SituationRow[] {
  return Array.isArray(value)
    ? value.filter((row): row is SituationRow => Boolean(row) && typeof row === 'object')
    : [];
}

export function label(value: unknown, fallback = 'not assessed'): string {
  return String(value ?? fallback).replaceAll('_', ' ');
}

export function formatDate(value: unknown): string {
  if (typeof value !== 'string' || !value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

export function formatNumber(value: unknown, digits = 1): string {
  return typeof value === 'number'
    ? value.toLocaleString('en-US', { maximumFractionDigits: digits })
    : '-';
}

export function formatPercent(value: unknown, digits = 0): string {
  return typeof value === 'number' ? `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%` : '-';
}

export function unitSuffix(unit: unknown): string {
  if (unit === 'percent') return '%';
  if (!unit || unit === 'count') return '';
  return ` ${unit}`;
}

export function riskRank(level: unknown): number {
  return RISK_RANK[String(level ?? '').toLowerCase()] ?? 0;
}

export function riskTone(level: unknown): string {
  const rank = riskRank(level);
  if (rank >= 3) return 'risk-elevated';
  if (rank === 2) return 'risk-watch';
  if (rank === 1) return 'risk-low';
  return 'risk-unknown';
}

export function riskScore(row: SituationRow): number {
  return typeof row?.risk?.score === 'number' ? row.risk.score : 0;
}

export function detectorSummary(row: SituationRow): { votes: number; total: number; label: string } {
  const detectors = Object.values(row?.statistics?.detectors ?? {});
  const total = detectors.length;
  const votes = detectors.filter(Boolean).length;
  return {
    votes,
    total,
    label: total ? `${votes}/${total} detectors` : 'not enough history',
  };
}

export function diseaseHref(row: SituationRow): string {
  return row?.disease_slug ? `/diseases/${row.disease_slug}/` : '/diseases/';
}

export function placeLine(row: SituationRow): string {
  return [row.country_name ?? row.country_code, row.window?.label ?? row.cadence]
    .filter(Boolean)
    .join(' · ') || 'Latest eligible status';
}

export function currentMetric(row: SituationRow): string {
  const primary = row?.metrics?.[0];
  if (primary) {
    return `${primary.label}: ${formatNumber(primary.value)}${unitSuffix(primary.unit)}`;
  }

  const current = row?.window?.current;
  if (current != null) {
    return `${row.metric_label ?? 'current'}: ${formatNumber(current)}${unitSuffix(row.unit)}`;
  }

  return 'current data unavailable';
}

export function evidenceLinks(row: SituationRow): SituationRow[] {
  return rows(row?.evidence_links).filter(link => /^https?:\/\//.test(String(link.url ?? '')));
}

export function trendLine(row: SituationRow): string {
  const change = formatPercent(row?.window?.change_pct);
  const detector = detectorSummary(row).label;
  return change === '-' ? detector : `${change} · ${detector}`;
}

export function sortSignals(items: SituationRow[]): SituationRow[] {
  return [...items].sort((left, right) => {
    const riskDelta = riskRank(right?.risk?.level) - riskRank(left?.risk?.level);
    if (riskDelta) return riskDelta;
    const scoreDelta = riskScore(right) - riskScore(left);
    if (scoreDelta) return scoreDelta;
    return detectorSummary(right).votes - detectorSummary(left).votes;
  });
}

export function signalCount(snapshot: any): number {
  return rows(snapshot?.increasing).length + rows(snapshot?.unusual).length;
}

export function elevatedSignalCount(snapshot: any): number {
  return [...rows(snapshot?.increasing), ...rows(snapshot?.unusual)]
    .filter(row => riskRank(row?.risk?.level) >= 3)
    .length;
}
