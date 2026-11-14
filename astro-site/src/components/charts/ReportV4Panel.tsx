import { type Lang, type AnyRecord, type SparklineSeriesEntry } from './report-v4/types.ts';
import { asRecord, asArray, ui, localized, localizedList, fmtNumber, percent, attentionLabel, deathLabel, sectionLabel, trendLabel, trendDirection, categoryLabel, attentionRank, attentionClass, trendBackgroundClass, directionFromChange, attentionScore, currentYearLabel } from './report-v4/presentation.ts';
import { aggregateCurveByPeriod, currentYearCumulativeCases } from './report-v4/curve-model.ts';
import { MarkdownBlock, ChangeCurveCell, CasesMoMCell, Metric, DirectoryStat } from './report-v4/ReportCells.tsx';
import { lazy, Suspense, useEffect, useMemo, useState } from 'react';

import { loadCountryDataset } from './countryDataset';
import { localizedDiseaseName } from '../../utils/diseaseNames';
import { siteHref } from '../../lib/papers';

const ReportEvidenceFigures = lazy(() => import('./ReportEvidenceFigures'));

interface Props {
  report: AnyRecord;
  countryDataUrl?: string;
  sparklineSeries?: Record<string, SparklineSeriesEntry>;
  initialLanguage?: Lang;
}

function useLang(initialLanguage: Lang): Lang {
  return initialLanguage;
}

export default function ReportV4Panel({ report, countryDataUrl, sparklineSeries, initialLanguage = 'en' }: Props) {
  const lang = useLang(initialLanguage);
  const localePrefix = lang === 'zh' ? '/zh' : lang === 'fr' ? '/fr' : '';
  const document = asRecord(report.report_document_v4 || asRecord(report.metadata).report_document_v4 || report);
  const metrics = asRecord(document.metrics);
  const deathReporting = asRecord(document.death_reporting);
  const dataQuality = asRecord(document.data_quality);
  const attentionRanking = asArray(document.attention_ranking || document.risk_ranking).slice(0, 8);
  const diseaseDirectory = asArray(document.disease_directory);
  const [showAudit, setShowAudit] = useState(false);
  useEffect(() => {
    setShowAudit(new URLSearchParams(window.location.search).has('audit'));
  }, []);
  const sections = asArray(document.sections)
    .filter((section) => showAudit || section.type !== 'method_appendix')
    .sort((a, b) => Number(a.order || 0) - Number(b.order || 0));
  const findings = localizedList(document.key_findings, lang);
  const reportId = String(report.id || metrics.report_id || '');
  const countryCode = String(report.country_code || metrics.country_code || '').toLowerCase();
  const directoryRows = diseaseDirectory.length > 0
    ? diseaseDirectory
    : attentionRanking.map((row) => ({
        ...row,
        slug: String(row.name_en || row.disease_id || 'disease').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
        mom_change_pct: row.change_pct,
        trend: { zh: '待观察', en: 'Watch' },
      }));
  const [directoryQuery, setDirectoryQuery] = useState('');
  const [directoryAttentionFilter, setDirectoryAttentionFilter] = useState('all');
  const [directoryTrendFilter, setDirectoryTrendFilter] = useState('all');
  const [countrySeries, setCountrySeries] = useState<Record<string, SparklineSeriesEntry> | undefined>(sparklineSeries);
  useEffect(() => {
    if (sparklineSeries && Object.keys(sparklineSeries).length > 0) {
      setCountrySeries(sparklineSeries);
      return;
    }
    if (!countryDataUrl) return;
    let cancelled = false;
    loadCountryDataset(countryDataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setCountrySeries(dataset.disease_series);
      })
      .catch(() => {
        if (cancelled) return;
        setCountrySeries(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [countryDataUrl, sparklineSeries]);
  const directoryStats = useMemo(() => {
    const elevatedAttention = directoryRows.filter((row) => attentionRank(row.attention_level || row.risk_level) >= 3).length;
    const rising = directoryRows.filter((row) => trendDirection(row) === 'up').length;
    return { elevatedAttention, rising };
  }, [directoryRows]);
  const visibleDirectoryRows = useMemo(() => {
    const query = directoryQuery.trim().toLowerCase();
    return directoryRows
      .filter((row) => {
        const level = row.attention_level || row.risk_level;
        if (directoryAttentionFilter === 'elevated' && attentionRank(level) < 3) return false;
        if (directoryAttentionFilter === 'moderate' && String(level || '').toLowerCase() !== 'moderate') return false;
        if (directoryAttentionFilter === 'low' && String(level || '').toLowerCase() !== 'low') return false;

        const direction = trendDirection(row);
        if (directoryTrendFilter === 'rising' && direction !== 'up') return false;
        if (directoryTrendFilter === 'falling' && direction !== 'down') return false;
        if (directoryTrendFilter === 'stable' && direction !== 'stable') return false;
        if (directoryTrendFilter === 'watch' && !['watch', 'unknown'].includes(direction)) return false;

        if (!query) return true;
        const searchable = [
          row.disease_id,
          row.name_zh,
          row.name_en,
          row.category,
          categoryLabel(row.category, lang),
          attentionLabel(row.attention_level || row.risk_level, lang),
          trendLabel(row, lang),
        ].join(' ').toLowerCase();
        return searchable.includes(query);
      })
      .sort((left, right) => (
        Number(right.attention_score ?? right.risk_score ?? 0) - Number(left.attention_score ?? left.risk_score ?? 0)
        || attentionRank(right.attention_level || right.risk_level) - attentionRank(left.attention_level || left.risk_level)
        || Number(right.latest_cases || 0) - Number(left.latest_cases || 0)
      ));
  }, [directoryRows, directoryQuery, directoryAttentionFilter, directoryTrendFilter, lang]);

  return (
    <div className="space-y-8">
      <section className="figure-panel">
        <p className="figure-kicker">
          {ui(lang, 'Decision brief', '决策简报', 'Note décisionnelle')}
        </p>
        <h2 className="figure-title">
          {ui(lang, 'Current judgement and next actions', '当前判断与下一步动作', 'Évaluation actuelle et prochaines actions')}
        </h2>
        <div className="mt-5 grid gap-3 sm:grid-cols-4">
          <Metric label={ui(lang, 'Cases', '病例', 'Cas')} value={fmtNumber(metrics.total_cases, lang)} />
          <Metric label={ui(lang, 'Latest cases', '最新病例', 'Derniers cas')} value={fmtNumber(metrics.latest_cases, lang)} />
          <Metric label={ui(lang, 'Death scope', '死亡口径', 'Périmètre des décès')} value={deathLabel(deathReporting, lang)} />
          <Metric label={ui(lang, 'Data confidence', '数据置信度', 'Confiance des données')} value={percent(dataQuality.score, lang)} />
        </div>
        <div className="mt-5">
          <MarkdownBlock content={localized(document.summary, lang)} />
        </div>
        {findings.length > 0 && (
          <ul className="mt-5 space-y-2">
            {findings.map((finding, index) => (
              <li key={`${index}-${finding}`} className="flex gap-3 text-sm leading-6 text-[rgb(var(--text-muted))] dark:text-[rgb(var(--text-muted))]">
                <span className="mt-0.5 text-brand-500">→</span>
                <span>{finding}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {directoryRows.length > 0 && (
        <section className="figure-panel">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-2xl">
              <p className="figure-kicker">
                {ui(lang, 'Table of contents', '报告目录', 'Table des matières')}
              </p>
              <h2 className="figure-title">
                {ui(lang, 'Disease table of contents', '按疾病进入本期研判', 'Sommaire par maladie')}
              </h2>
            </div>
            <div className="flex w-full flex-wrap items-center justify-between gap-2 border border-[rgb(var(--border))] bg-[rgb(var(--surface)/.7)] px-3 py-2 lg:w-auto lg:min-w-[520px]">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[rgb(var(--text-muted))]">
                {ui(lang, 'Snapshot', '目录概览', 'Vue d’ensemble')}
              </div>
              <div className="flex flex-wrap items-center gap-3 text-xs text-[rgb(var(--text-muted))]">
                <DirectoryStat label={ui(lang, 'Shown', '当前', 'Affichées')} value={`${visibleDirectoryRows.length}/${directoryRows.length}`} />
                <DirectoryStat label={ui(lang, 'High attention+', '高关注', 'Attention élevée+')} value={fmtNumber(directoryStats.elevatedAttention, lang)} tone="risk" />
                <DirectoryStat label={ui(lang, 'Rising', '上升', 'En hausse')} value={fmtNumber(directoryStats.rising, lang)} tone="up" />
              </div>
            </div>
          </div>

          <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_180px_180px]">
            <label className="block">
              <span className="sr-only">{ui(lang, 'Search diseases', '搜索疾病', 'Rechercher des maladies')}</span>
              <input
                id="report-directory-search"
                name="report-directory-search"
                type="search"
                value={directoryQuery}
                onChange={(event) => setDirectoryQuery(event.target.value)}
                placeholder={ui(lang, 'Search disease, ID, category, trend...', '搜索疾病、编号、类别、趋势…', 'Rechercher une maladie, un identifiant, une catégorie ou une tendance…')}
                className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
            </label>
            <label className="block">
              <span className="sr-only">{ui(lang, 'Attention-priority filter', '监测关注优先级筛选', 'Filtre de priorité de surveillance')}</span>
              <select
                id="report-attention-filter"
                name="report-attention-filter"
                value={directoryAttentionFilter}
                onChange={(event) => setDirectoryAttentionFilter(event.target.value)}
                className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              >
                <option value="all">{ui(lang, 'All attention bands', '全部关注等级', 'Tous les niveaux d’attention')}</option>
                <option value="elevated">{ui(lang, 'High attention+', '高关注及以上', 'Attention élevée ou critique')}</option>
                <option value="moderate">{ui(lang, 'Moderate attention', '中等关注', 'Attention modérée')}</option>
                <option value="low">{ui(lang, 'Low attention', '低关注', 'Attention faible')}</option>
              </select>
            </label>
            <label className="block">
              <span className="sr-only">{ui(lang, 'Trend filter', '趋势筛选', 'Filtre de tendance')}</span>
              <select
                id="report-trend-filter"
                name="report-trend-filter"
                value={directoryTrendFilter}
                onChange={(event) => setDirectoryTrendFilter(event.target.value)}
                className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              >
                <option value="all">{ui(lang, 'All trends', '全部趋势', 'Toutes les tendances')}</option>
                <option value="rising">{ui(lang, 'Rising', '上升', 'En hausse')}</option>
                <option value="falling">{ui(lang, 'Falling', '下降', 'En baisse')}</option>
                <option value="stable">{ui(lang, 'Stable', '平稳', 'Stable')}</option>
                <option value="watch">{ui(lang, 'Watch', '待观察', 'À surveiller')}</option>
              </select>
            </label>
          </div>

          <p className="mt-3 text-xs leading-5 text-[rgb(var(--text-muted))] dark:text-[rgb(var(--text-muted))]">
            {ui(
              lang,
              'The 0–100 surveillance attention score only orders signal review. It combines reported burden, change, mortality signals when available, anomaly markers, historical position, and data quality; it is uncalibrated and is not infection, severity, mortality, or outbreak risk.',
              '监测关注分（0–100）只用于安排信号复核顺序，由报告病例负担、变化、可用死亡线索、异常标记、历史位置和数据质量组成；未经概率校准，不代表感染、重症、死亡或暴发风险。',
              'Le score d’attention de surveillance (0–100) sert uniquement à ordonner l’examen des signaux. Il combine la charge déclarée, les variations, les indices de mortalité disponibles, les anomalies, la position historique et la qualité des données ; il n’est pas étalonné comme une probabilité et ne mesure ni le risque d’infection, ni la gravité, ni la mortalité, ni le risque d’épidémie.',
            )}
          </p>

          <div className="mt-4 h-[560px] overflow-auto border border-[rgb(var(--border))] dark:border-[rgb(var(--border))]">
            <table className="w-[990px] min-w-[990px] table-fixed text-sm">
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[228px]" />
                <col className="w-[112px]" />
                <col className="w-[162px]" />
                <col className="w-[136px]" />
                <col className="w-[166px]" />
                <col className="w-[142px]" />
              </colgroup>
              <thead className="text-xs text-[rgb(var(--text-muted))] dark:text-[rgb(var(--text-muted))]">
                <tr>
                  <th className="sticky top-0 z-10 bg-[rgb(var(--bg-soft))] px-3 py-3 text-left">#</th>
                  <th className="sticky top-0 z-10 bg-[rgb(var(--bg-soft))] px-3 py-3 text-left">{ui(lang, 'Disease', '疾病目录', 'Maladie')}</th>
                  <th className="sticky top-0 z-10 bg-[rgb(var(--bg-soft))] px-3 py-3 text-left">{ui(lang, 'Attention band', '监测关注级', 'Niveau d’attention')}</th>
                  <th className="sticky top-0 z-10 bg-[rgb(var(--bg-soft))] px-3 py-3 text-right">{ui(lang, 'Latest cases (change)', '最新期病例（较上期）', 'Derniers cas (variation)')}</th>
                  <th className="sticky top-0 z-10 bg-[rgb(var(--bg-soft))] px-3 py-3 text-right">{ui(lang, 'Period cases', '报告期病例', 'Cas sur la période')}</th>
                  <th className="sticky top-0 z-10 bg-[rgb(var(--bg-soft))] px-3 py-3 text-right">{ui(lang, 'Latest year in window', '窗口内本年病例', 'Dernière année de la fenêtre')}</th>
                  <th className="sticky top-0 z-10 bg-[rgb(var(--bg-soft))] px-3 py-3 text-right">{ui(lang, 'YoY', '同比', 'Variation annuelle')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border))] text-[rgb(var(--text-strong))]">
                {visibleDirectoryRows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-sm text-[rgb(var(--text-muted))]">
                      {ui(lang, 'No diseases matched the current filters.', '没有匹配的疾病。', 'Aucune maladie ne correspond aux filtres actuels.')}
                    </td>
                  </tr>
                )}
                {visibleDirectoryRows.map((row, index) => {
                  const diseaseId = String(row.disease_id || '');
                  const rowSeries = countrySeries?.[diseaseId];
                  const monthlyCurve = aggregateCurveByPeriod(rowSeries, 'month');
                  const annualCurve = aggregateCurveByPeriod(rowSeries, 'year');
                  const ytdCases = currentYearCumulativeCases(rowSeries);
                  const diseaseName = lang === 'zh' ? (row.name_zh || row.name_en) : lang === 'fr' ? localizedDiseaseName({ disease_id: diseaseId, name_en: row.name_en, name_zh: row.name_zh, name_fr: row.name_fr }, 'fr') : (row.name_en || row.name_zh);
                  const href = countryCode && reportId && row.slug
                    ? siteHref(`${localePrefix}/countries/${countryCode}/reports/${reportId}/${row.slug}/`)
                    : undefined;
                  return (
                    <tr key={`${row.disease_id || index}`} className="align-top hover:bg-[rgb(var(--bg-soft)/.7)]">
                      <td className="px-3 py-3 text-[rgb(var(--text-muted))]">{index + 1}</td>
                      <td className="w-[228px] px-3 py-3">
                        <div className="w-[204px] whitespace-normal break-words font-medium leading-5 text-[rgb(var(--text-strong))]">
                          {href ? (
                            <a className="text-brand-600 hover:underline dark:text-brand-300" href={href}>
                              {diseaseName}
                            </a>
                          ) : (
                            diseaseName
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-2 text-xs text-[rgb(var(--text-muted))]">
                          {row.disease_id && <span>{row.disease_id}</span>}
                          <span>{categoryLabel(row.category, lang)}</span>
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <span className={`inline-flex items-center border px-2 py-1 text-xs font-semibold ${attentionClass(row.attention_level || row.risk_level)}`}>
                          {attentionLabel(row.attention_level || row.risk_level, lang)}
                          <span className="ml-1 font-normal opacity-75">{attentionScore(row.attention_score ?? row.risk_score)}</span>
                        </span>
                      </td>
                      <CasesMoMCell
                        cases={row.latest_cases}
                        change={row.mom_change_pct}
                        values={monthlyCurve}
                        direction={trendDirection(row)}
                        title={trendLabel(row, lang)}
                        lang={lang}
                        className={trendBackgroundClass(row)}
                      />
                      <td className="px-3 py-3 text-right tabular-nums" title={ui(lang, 'Cumulative cases within the report window', '当前报告窗口内累计病例', 'Cas cumulés sur la période du rapport')}>
                        {fmtNumber(row.total_cases, lang)}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums" title={currentYearLabel(rowSeries, lang)}>
                        <div>{fmtNumber(ytdCases, lang)}</div>
                        <div className="mt-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-[rgb(var(--text-muted))]">
                          {currentYearLabel(rowSeries, lang)}
                        </div>
                      </td>
                      <ChangeCurveCell
                        value={row.yoy_change_pct}
                        values={annualCurve}
                        direction={directionFromChange(row.yoy_change_pct)}
                        title={ui(lang, 'YoY background: annual cases curve', '同比背景：年度发病曲线', 'Contexte annuel : courbe des cas par année')}
                        tone="annual"
                        lang={lang}
                        className="bg-brand-500/[0.04]"
                      />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {sections.map((section) => (
        <section key={section.id || section.order} className="figure-panel">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="figure-kicker">{sectionLabel(section.type, lang)}</p>
              <h2 className="figure-title">{localized(section.title, lang)}</h2>
            </div>
            <span className="mt-2 text-xs uppercase tracking-[0.14em] text-[rgb(var(--text-muted))]">
              {section.order ? ui(lang, `Section ${section.order}`, `第${section.order}节`, `Section ${section.order}`) : ''}
            </span>
          </div>
          <div className="mt-5">
            <MarkdownBlock content={localized(section.body, lang)} />
          </div>
          {asArray(section.figures).length > 0 && <Suspense fallback={<div className="min-h-[340px]" />}>
            <ReportEvidenceFigures figures={asArray(section.figures)} data={asRecord(document.figure_data)} lang={lang} sectionType={String(section.type)} />
          </Suspense>}
        </section>
      ))}
    </div>
  );
}
