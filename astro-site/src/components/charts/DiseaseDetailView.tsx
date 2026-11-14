import { type DiseaseSection, type DiseaseMeta, type DiseaseSeries, type ReportMeta } from './disease-detail/types.ts';
import { ui, fmtNum, fmtDate, CATEGORY_STYLES, categoryLabel } from './disease-detail/presentation.ts';
import { MonthlyDistributionChart } from './disease-detail/MonthlyDistributionChart.tsx';
import { SectionBlock } from './disease-detail/ReportSection.tsx';
import { Figure } from './disease-detail/Figure.tsx';
import ReportSurveillanceCurve from './surveillance/components/ReportSurveillanceCurve';
// src/components/charts/DiseaseDetailView.tsx
// Journal-publication-style disease detail view for individual disease pages.
// Sections: Summary → Highlights → Key Findings → Trend Analysis
// Charts: Epidemic Curve (cases+deaths dual axis) + Monthly Distribution (cases & deaths)

import { useEffect, useMemo, useState } from 'react';

import { localizedDiseaseName } from '../../utils/diseaseNames';
import { siteHref } from '../../lib/papers';

interface Props {
  diseaseMeta: DiseaseMeta;
  sections: DiseaseSection[];
  series: DiseaseSeries | null;
  latestSeries?: DiseaseSeries | null;
  reportMeta: ReportMeta;
  globalDiseasePath?: string | null;
  initialLanguage?: 'en' | 'zh' | 'fr';
}

// ─── Theme ───────────────────────────────────────────────────────────────────

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'light';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const update = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

function useLang(initialLanguage: 'en' | 'zh' | 'fr') {
  return initialLanguage;
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function DiseaseDetailView({ diseaseMeta, sections, series, latestSeries, reportMeta, globalDiseasePath = null, initialLanguage = 'en' }: Props) {
  const theme = useTheme();
  const lang = useLang(initialLanguage);
  const localePrefix = lang === 'zh' ? '/zh' : lang === 'fr' ? '/fr' : '';
  const isLight = theme === 'light';

  const bgPage   = isLight ? '#f8fafc' : '#060d1b';
  const bgCard   = isLight ? '#ffffff' : '#0c1526';
  const border   = isLight ? '#e2e8f0' : '#1a2744';
  const textHead = isLight ? '#0f172a' : '#f1f5f9';
  const textBody = isLight ? '#475569' : '#94a3b8';
  const textMute = isLight ? '#94a3b8' : '#475569';

  // Get section by type
  const getSection = (type: string) =>
    sections.find(s => s.section_type === type) ?? null;

  const summarySection    = getSection('summary');
  const highlightsSection = getSection('highlights');
  const keyFindingsSection = getSection('key_findings');
  const trendSection      = getSection('trend_analysis');

  const catStyle = CATEGORY_STYLES[diseaseMeta.category] ?? {
    bg: 'rgba(100,116,139,0.1)', text: '#94a3b8', border: 'rgba(100,116,139,0.25)'
  };

  // Exports carry every published name so the strip reads in any language.
  const exportNames = useMemo(() => {
    let french = diseaseMeta.name_fr?.trim() ?? '';
    if (!french) {
      try { french = localizedDiseaseName(diseaseMeta, 'fr'); } catch { french = ''; }
    }
    return JSON.stringify({ en: diseaseMeta.name_en, zh: diseaseMeta.name_zh, fr: french });
  }, [diseaseMeta]);

  // Compute aggregate stats
  const totalCases  = series?.total_cases ?? 0;
  const totalDeaths = series?.total_deaths ?? 0;
  const cfr = totalCases > 0 ? ((totalDeaths / totalCases) * 100) : 0;
  const hasDeathsData = [...(series?.deaths ?? []), ...(latestSeries?.deaths ?? [])].some((value) => (value ?? 0) > 0) || totalDeaths > 0;
  const [showDeathMetrics, setShowDeathMetrics] = useState(hasDeathsData);

  useEffect(() => {
    setShowDeathMetrics(hasDeathsData);
  }, [hasDeathsData]);

  const hasSeries = !!series && series.dates.length > 0;
  const curveCaption: [string, string, string] = reportMeta.snapshot_version
    ? latestSeries?.dates.length
      ? [
          'Archived report observations are compared with current published data over the report period. Cases and deaths have separate panels.',
          '将报告归档观察值与报告时段内的当前发布数据对照。病例与死亡使用独立面板。',
          'Les observations archivées sont comparées aux données publiées actuelles sur la période du rapport. Cas et décès en panneaux séparés.',
        ]
      : [
          'Archived observations from this report. Cases and deaths have separate panels.',
          '本报告归档的观察值。病例与死亡使用独立面板。',
          'Observations archivées avec ce rapport. Cas et décès en panneaux séparés.',
        ]
    : [
        'Reported observations within this report’s period, using the currently published dataset. Cases and deaths have separate panels; this is not an archived data snapshot.',
        '使用当前发布数据，查看报告期间内的观察值。病例与死亡使用独立面板；此视图并非归档数据快照。',
        'Observations de la période du rapport issues des données actuellement publiées. Cas et décès en panneaux séparés ; il ne s’agit pas d’un instantané archivé.',
      ];

  // Fix highlights: replace bullet "• " and <br/> with proper HTML
  function renderHighlights(html: string) {
    // The highlights content may be plain "• ... <br/>" or proper markdown  
    if (!html.includes('<')) {
      // Plain text bullets
      return '<ul>' + html.split('•').filter(Boolean).map(b =>
        `<li>${b.trim().replace(/<br\s*\/?>/gi, '').trim()}</li>`
      ).join('') + '</ul>';
    }
    return html;
  }

  return (
    <div style={{ background: bgPage, minHeight: '100vh' }} data-disease-names={exportNames}>
      {/* ── Journal-style page wrapper ── */}
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-8">

        {/* ── HEADER ── */}
        <header
          style={{ background: bgCard, border: `1px solid ${border}`, borderRadius: 0 }}
          className="p-7 md:p-10"
        >
          {/* Top row: category + back link */}
          <div className="flex items-start justify-between flex-wrap gap-4 mb-6">
            <div className="flex items-center gap-3">
              <span
                style={{ background: catStyle.bg, color: catStyle.text, border: `1px solid ${catStyle.border}` }}
                className="text-xs font-bold uppercase tracking-[0.12em] px-3 py-1 rounded-none"
              >
                {categoryLabel(diseaseMeta.category, lang)}
              </span>
              <span style={{ color: textMute }} className="text-sm">{reportMeta.country_name}</span>
              <span style={{ color: textMute }} className="text-xs opacity-60">
                {ui(lang, `Report #${reportMeta.id}`, `报告 #${reportMeta.id}`, `Rapport n° ${reportMeta.id}`)}
              </span>
            </div>
            <a
              href={siteHref(`${localePrefix}/countries/${reportMeta.country_code}/reports/${reportMeta.id}/`)}
              style={{ color: textMute }}
              className="text-xs hover:text-brand-400 transition-colors flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
              {ui(lang, 'Back to report', '返回报告', 'Retour au rapport')}
            </a>
          </div>

          {hasDeathsData && (
            <div className="mb-5 flex justify-end">
              <button
                type="button"
                onClick={() => setShowDeathMetrics((current) => !current)}
                className={`chart-toggle ${showDeathMetrics ? 'chart-toggle-active' : ''}`}
              >
                {showDeathMetrics
                  ? ui(lang, 'Hide deaths / CFR', '隐藏死亡数/病死率', 'Masquer les décès / létalité')
                  : ui(lang, 'Show deaths / CFR', '显示死亡数/病死率', 'Afficher les décès / létalité')}
              </button>
            </div>
          )}

          {/* Disease title */}
          <div className="mb-6">
            <h1 style={{ color: textHead }} className="text-3xl md:text-4xl font-bold tracking-tight leading-tight mb-1">
              {localizedDiseaseName(diseaseMeta, lang)}
            </h1>
            {(diseaseMeta.name_zh || diseaseMeta.name_fr) && (
              <p style={{ color: textMute }} className="text-base mt-1">
                {lang === 'zh' ? `英文名：${diseaseMeta.name_en}` : lang === 'fr' ? `Nom anglais : ${diseaseMeta.name_en}` : diseaseMeta.name_zh}
              </p>
            )}
          </div>

          {/* Stats row */}
          {series && (
            <div
              style={{ background: isLight ? '#f8fafc' : 'rgba(255,255,255,0.03)', borderRadius: 0, border: `1px solid ${border}` }}
              className="grid grid-cols-2 sm:grid-cols-4 gap-0"
            >
              {[
                { label: ui(lang, 'Total Cases', '累计病例', 'Cas cumulés'),  value: fmtNum(totalCases),  color: isLight ? '#2563eb' : '#60a5fa' },
                ...(showDeathMetrics ? [
                  { label: ui(lang, 'Total Deaths', '累计死亡', 'Décès cumulés'), value: fmtNum(totalDeaths), color: isLight ? '#dc2626' : '#f87171' },
                  { label: ui(lang, 'Case Fatality', '病死率', 'Létalité'), value: cfr.toFixed(3) + '%', color: isLight ? '#d97706' : '#fbbf24' },
                ] : []),
                {
                  label: ui(lang, 'Data Span', '数据跨度', 'Période couverte'),
                  value: series.dates[0]
                    ? `${series.dates[0].substring(0,7)} – ${series.dates[series.dates.length-1].substring(0,7)}`
                    : '—',
                  color: textBody,
                },
              ].map(({ label, value, color }, i) => (
                <div key={i} className="p-4 text-center" style={{ borderColor: border }}>
                  <div style={{ color, fontWeight: 700, fontSize: '18px', fontVariantNumeric: 'tabular-nums' }}>
                    {value}
                  </div>
                  <div style={{ color: textMute }} className="text-xs mt-1">{label}</div>
                </div>
              ))}
            </div>
          )}

          {/* Period row */}
          <div className="mt-5 flex flex-wrap gap-6">
            <div className="flex items-center gap-2" style={{ color: textBody }}>
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              <span className="text-sm">
                <span style={{ color: textMute }}>{ui(lang, 'Report period', '报告周期', 'Période du rapport')}: </span>
                <span style={{ color: textHead }}>
                  {fmtDate(reportMeta.period_start, lang)} → {fmtDate(reportMeta.period_end, lang)}
                </span>
              </span>
            </div>
          </div>
        </header>

        {/* ── DIVIDER: journal-style ── */}
        <div className="flex items-center gap-4">
          <div style={{ flex: 1, height: '1px', background: `linear-gradient(to right, ${border}, transparent)` }} />
          <span style={{ color: textMute, fontSize: '10px', letterSpacing: '0.2em', fontWeight: 600 }} className="uppercase">
            {ui(lang, 'Surveillance Analysis', '疾病监测报告', 'Analyse de surveillance')}
          </span>
          <div style={{ flex: 1, height: '1px', background: `linear-gradient(to left, ${border}, transparent)` }} />
        </div>

        {/* ── SUMMARY ── */}
        {summarySection && (
          <section>
            <SectionBlock section={summarySection} theme={theme} lang={lang} />
          </section>
        )}

        {/* ── HIGHLIGHTS ── */}
        {highlightsSection && (
          <section>
            <SectionBlock
              section={{
                ...highlightsSection,
                content_html: highlightsSection.content_html
                  ? renderHighlights(highlightsSection.content_html)
                  : undefined,
              }}
              theme={theme}
              lang={lang}
            />
          </section>
        )}

        {/* ── KEY FINDINGS ── */}
        {keyFindingsSection && (
          <section>
            <SectionBlock section={keyFindingsSection} theme={theme} lang={lang} />
          </section>
        )}

        {/* ── FIGURES ── */}
        {hasSeries && (
          <section>
            {/* Section heading */}
            <div className="flex items-center gap-3 mb-5">
              <div style={{ width: '3px', height: '20px', background: isLight ? '#0891b2' : '#38bdf8', borderRadius: 0 }} />
              <h2 style={{ color: textHead }} className="text-sm font-bold uppercase tracking-[0.15em]">
                {ui(lang, 'Figures', '图表分析', 'Figures')}
              </h2>
            </div>

            {/* Figure 1: Epidemic Curve */}
            <Figure
              number={1}
              caption={ui(lang, ...curveCaption)}
              theme={theme}
              lang={lang}
              actions={false}
            >
              <ReportSurveillanceCurve series={series!} latestSeries={latestSeries} disease={diseaseMeta} report={reportMeta} lang={lang} showDeaths={showDeathMetrics && hasDeathsData} />
            </Figure>

            {/* Figures 2 & 3: Monthly Distribution side by side */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
              <Figure
                number={2}
                caption={ui(lang, 'Monthly distribution of cases by year.', '月度病例分布（按年分组）', 'Répartition mensuelle des cas par année.')}
                theme={theme}
                lang={lang}
                pngFilename="gids-monthly-cases.png"
              >
                <MonthlyDistributionChart series={series!} metric="cases" theme={theme} lang={lang} />
              </Figure>
              {showDeathMetrics && hasDeathsData && (
                <Figure
                  number={3}
                  caption={ui(lang, 'Monthly distribution of deaths by year.', '月度死亡分布（按年分组）', 'Répartition mensuelle des décès par année.')}
                  theme={theme}
                  lang={lang}
                  pngFilename="gids-monthly-deaths.png"
                >
                  <MonthlyDistributionChart series={series!} metric="deaths" theme={theme} lang={lang} />
                </Figure>
              )}
            </div>
          </section>
        )}

        {/* ── TREND ANALYSIS ── */}
        {trendSection && (
          <section>
            <SectionBlock section={trendSection} theme={theme} lang={lang} />
          </section>
        )}

        {/* ── FOOTER NAV ── */}
        <div
          style={{ borderTop: `1px solid ${border}`, paddingTop: '24px' }}
          className="flex items-center justify-between flex-wrap gap-4"
        >
          <a
              href={siteHref(`${localePrefix}/countries/${reportMeta.country_code}/reports/${reportMeta.id}/`)}
            style={{ color: textBody }}
            className="flex items-center gap-2 text-sm hover:text-brand-400 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            {ui(lang, '← Back to overview', '← 返回区域总览', '← Retour à la vue de la zone')}
          </a>
          {globalDiseasePath && <a
            href={`${localePrefix}${globalDiseasePath}`}
            style={{ color: textBody }}
            className="flex items-center gap-2 text-sm hover:text-brand-400 transition-colors"
          >
            {ui(lang, 'View global disease page →', '查看全球历史数据 →', 'Voir les données mondiales →')}
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>}
        </div>

      </div>
    </div>
  );
}
