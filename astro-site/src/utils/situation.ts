import type { components } from '../generated/api';

export type SituationReportV3 = components['schemas']['SituationReportV3'];
export type SituationSignalV3 = components['schemas']['SituationSignalV3'];
export type SituationEventV3 = components['schemas']['SituationEventClusterV3'];
export type RecentPoint = components['schemas']['RecentPoint'];

export function isSituationReportV3(value: unknown): value is SituationReportV3 {
  if (!value || typeof value !== 'object') return false;
  return (value as { schema_version?: unknown }).schema_version === 'situation_room.v3';
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-US', { maximumFractionDigits: digits })
    : '—';
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
    : '—';
}

export function label(value: string | null | undefined, fallback = 'not assessed'): string {
  return (value || fallback).replaceAll('_', ' ');
}

const ZH_LABELS: Record<string, string> = {
  active: '活跃', alert: '告警', assessed: '已评估', completed: '已完成',
  context_only_missing_denominator: '仅作背景（缺少分子分母）', corrected: '已更正',
  daily: '每日', failed: '失败', fresh: '新鲜', high: '高', increasing: '上升',
  merged: '已合并', mixed: '混合频率', monthly: '每月', new: '新增',
  not_assessed: '未评估', not_checked: '未检查', not_modeled: '未建模',
  partial: '部分可用', passed: '通过', persistent: '持续', published: '已发布',
  respiratory: '呼吸道', resolved: '消退', routine: '常规', severity: '严重度',
  standard: '标准', stale: '陈旧', strong: '强异常', suppressed: '已抑制',
  unusual: '异常', weekly: '每周', watch: '关注', official_match: '匹配官方事件',
  current: '当前可用', held_back: '暂缓最新周期', delayed: '来源延迟',
  common_count: '常见计数', rare_count: '稀有计数', rate: '率值', context_only: '仅作背景',
  lagged: '有延迟', historical: '历史信号', unreviewed: '未复核', under_review: '复核中',
  verified: '已验证', rejected: '已驳回', statistical_signal: '统计信号',
  not_verified: '未验证', automated_policy: '受控自动策略', analyst_review: '分析员复核',
  officially_correlated_signal: '官方事件关联信号', non_converged: '拟合未收敛',
  degraded: '降级可发布',
};

export function labelZh(value: string | null | undefined, fallback = '未评估'): string {
  return ZH_LABELS[value || ''] || fallback;
}

export function unitSuffix(unit: string | null | undefined): string {
  if (unit === 'percent') return '%';
  return !unit || unit === 'count' ? '' : ` ${unit}`;
}

export function signalTone(signal: SituationSignalV3): string {
  return `state-${signal.anomaly.state}`;
}

export function lifecycleLabel(signal: SituationSignalV3): string {
  const status = signal.lifecycle?.status ?? 'routine';
  return status === 'routine' ? 'active' : label(status);
}

export function lifecycleLabelZh(signal: SituationSignalV3): string {
  const status = signal.lifecycle?.status ?? 'routine';
  return status === 'routine' ? ZH_LABELS.active : labelZh(status);
}

export function riskLabel(signal: SituationSignalV3): string {
  const risk = signal.assessment.public_health_risk;
  return risk?.status === 'assessed' && risk.level ? label(risk.level) : 'not assessed';
}

export function riskLabelZh(signal: SituationSignalV3): string {
  const risk = signal.assessment.public_health_risk;
  return risk?.status === 'assessed' && risk.level ? labelZh(risk.level) : ZH_LABELS.not_assessed;
}

export function diseaseHref(signal: SituationSignalV3): string {
  return signal.identity.disease_slug
    ? `/diseases/${signal.identity.disease_slug}/`
    : '/diseases/';
}

export function geographyLabel(signal: SituationSignalV3): string {
  return signal.identity.country_name
    || signal.identity.country_code
    || signal.identity.canonical_geography_key;
}

export function sourceEvidenceUrls(report: SituationReportV3): string[] {
  const urls = [
    ...report.signals.flatMap(signal => (signal.evidence_links ?? []).map(link => link.url)),
    ...report.events.flatMap(event => event.updates.map(update => update.url)),
    ...report.context_panels.flatMap(panel => panel.metrics.map(metric => metric.source_url).filter(Boolean)),
  ].filter((url): url is string => typeof url === 'string' && /^https?:\/\//.test(url));
  return [...new Set(urls)];
}

export function chartGeometry(points: RecentPoint[], width = 520, height = 150): {
  actual: string;
  expected: string;
  upper: string;
  min: number;
  max: number;
} {
  const values = points.flatMap(point => [point.value, point.expected, point.predictive_upper_95])
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const min = values.length ? Math.min(...values) : 0;
  const rawMax = values.length ? Math.max(...values) : 1;
  const max = rawMax === min ? min + 1 : rawMax;
  const x = (index: number) => points.length <= 1 ? width / 2 : index / (points.length - 1) * width;
  const y = (value: number) => height - ((value - min) / (max - min)) * (height - 12) - 6;
  const path = (selector: (point: RecentPoint) => number | null | undefined): string => {
    let output = '';
    let drawing = false;
    points.forEach((point, index) => {
      const value = selector(point);
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        drawing = false;
        return;
      }
      output += `${drawing ? ' L' : 'M'}${x(index).toFixed(2)},${y(value).toFixed(2)}`;
      drawing = true;
    });
    return output;
  };
  return {
    actual: path(point => point.value),
    expected: path(point => point.expected),
    upper: path(point => point.predictive_upper_95),
    min,
    max,
  };
}
