import { useMemo } from 'react';
import { type Lang } from './types.ts';
import { markdownHtml, fmtNumber, changePct, changeClass, curveColorClass } from './presentation.ts';

export function MarkdownBlock({ content }: { content: string }) {
  const html = useMemo(() => markdownHtml(content), [content]);
  return <div className="report-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function BackgroundCurve({
  values,
  direction,
  tone = 'monthly',
}: {
  values: number[];
  direction?: string;
  tone?: 'monthly' | 'annual';
}) {
  const trendValues = values
    .map((value) => Number(value || 0))
    .filter((value) => Number.isFinite(value));

  if (trendValues.length < 2) return null;

  const width = 180;
  const height = 42;
  const innerWidth = width - 4;
  const min = Math.min(...trendValues);
  const max = Math.max(...trendValues);
  const range = Math.max(1, max - min);
  const denominator = Math.max(trendValues.length - 1, 1);
  const pointPairs = trendValues.map((value, index) => {
    const x = 2 + (index / denominator) * innerWidth;
    const y = height - 4 - ((value - min) / range) * (height - 10);
    return { x, y };
  });
  const points = pointPairs.map(({ x, y }) => `${x},${y}`).join(' ');
  const areaPoints = `2,${height - 2} ${points} ${width - 2},${height - 2}`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`pointer-events-none absolute inset-0 h-full w-full ${curveColorClass(direction, tone)}`}
      aria-hidden="true"
    >
      <polygon points={areaPoints} className="fill-current opacity-[0.08]" />
      <polyline
        points={points}
        className="fill-none stroke-current opacity-30"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ChangeCurveCell({
  value,
  values,
  direction,
  title,
  tone,
  lang,
  className = '',
}: {
  value: unknown;
  values: number[];
  direction?: string;
  title?: string;
  tone: 'monthly' | 'annual';
  lang: Lang;
  className?: string;
}) {
  return (
    <td
      className={`relative overflow-hidden px-3 py-3 text-right font-medium tabular-nums ${changeClass(value)} ${className}`}
      title={title}
    >
      <BackgroundCurve values={values} direction={direction} tone={tone} />
      <span className="relative z-10 inline-flex min-w-[4.5rem] justify-end bg-[rgb(var(--surface)/.65)] px-1.5 py-0.5 backdrop-blur-[1px]">
        {changePct(value, lang)}
      </span>
    </td>
  );
}

export function CasesMoMCell({
  cases,
  change,
  values,
  direction,
  title,
  lang,
  className = '',
}: {
  cases: unknown;
  change: unknown;
  values: number[];
  direction?: string;
  title?: string;
  lang: Lang;
  className?: string;
}) {
  return (
    <td
      className={`relative overflow-hidden px-3 py-3 text-right tabular-nums ${className}`}
      title={title}
    >
      <BackgroundCurve values={values} direction={direction} tone="monthly" />
      <span className="relative z-10 inline-flex max-w-full items-baseline justify-end gap-1.5 bg-[rgb(var(--surface)/.65)] px-1.5 py-0.5 backdrop-blur-[1px]">
        <span className="font-semibold text-[rgb(var(--text-strong))]">{fmtNumber(cases, lang)}</span>
        <span className={`text-xs font-medium ${changeClass(change)}`}>{changePct(change, lang)}</span>
      </span>
    </td>
  );
}

export function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-[rgb(var(--border))] bg-[rgb(var(--surface)/.7)] px-4 py-3">
      <div className="text-xs text-[rgb(var(--text-muted))]">{label}</div>
      <div className="mt-1 truncate text-lg font-semibold text-[rgb(var(--text-strong))]">{value}</div>
    </div>
  );
}

export function DirectoryStat({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'risk' | 'up' }) {
  const valueClass = tone === 'risk'
    ? 'text-orange-700 dark:text-orange-300'
    : tone === 'up'
      ? 'text-red-700 dark:text-red-300'
      : 'text-[rgb(var(--text-strong))]';

  return (
    <div className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-[11px] uppercase tracking-[0.12em] text-[rgb(var(--text-muted))]">{label}</span>
      <span className={`font-serif text-lg font-semibold leading-none tabular-nums ${valueClass}`}>
        {value}
      </span>
    </div>
  );
}
