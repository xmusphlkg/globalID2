import GIDSLoader from '../GIDSLoader';
import { useEffect, useMemo, useState } from 'react';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import { COUNTRY_COVERAGE } from '../../lib/country-coverage.ts';
import { getFlagAssetPath } from '../../lib/country-flag';
import { COUNTRY_CLASSIFICATION_METADATA, getCountryClassification, WHO_REGION_LABELS, WORLD_BANK_REGION_LABELS, INCOME_GROUP_LABELS } from '../../lib/country-classifications.ts';

import { countryLabel, cadence, safeUrl } from '../../lib/surveillance-presentation.ts';
import { hasData, subdivisionRows } from '../../lib/surveillance-coverage';
type Language = 'en' | 'zh' | 'fr';
type Row = Record<string, any>;
const normalize = (value: unknown) => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

export default function SurveillanceDirectory({ language = 'en', parentCode }: { language?: Language; parentCode?: string }) {
  const publication = useSurveillanceSummary('/site-data/meta.json');
  const initial = typeof window === 'undefined' ? new URLSearchParams() : new URLSearchParams(window.location.search);
  const [query, setQuery] = useState(initial.get('q') ?? '');
  const [who, setWho] = useState(initial.get('who') ?? '');
  const [wb, setWb] = useState(initial.get('wb') ?? '');
  const [income, setIncome] = useState(initial.get('income') ?? '');
  const [status, setStatus] = useState(initial.get('status') ?? '');
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  const prefix = language === 'en' ? '' : `/${language}`;
  const rows = useMemo<Row[]>(() => {
    const saved = new Map((publication.data?.countries ?? []).map((row: Row) => [String(row.code).toUpperCase(), row]));
    if (parentCode) return [...saved.values()].filter((row: any) => String(row.parent_code ?? '').toUpperCase() === parentCode.toUpperCase()) as Row[];
    const base = new Map(COUNTRY_COVERAGE.map(row => [row.code.toUpperCase(), { ...row, data_available: false } as Row]));
    for (const [code, row] of saved) if (!(row as Row).parent_code && (row as Row).location_type !== 'subdivision') base.set(String(code), { ...base.get(String(code)), ...(row as Row) });
    return [...base.values()].map(row => ({ ...row, classification: getCountryClassification(row.code) })).sort((a, b) => Number(hasData(b)) - Number(hasData(a)));
  }, [publication.data, parentCode]);
  const filtered = useMemo(() => rows.filter(row => {
    const active = hasData(row);
    return (!query || normalize([row.code, row.name_en, row.name_zh, countryLabel(row, 'fr'), row.name].join(' ')).includes(normalize(query)))
      && (!who || row.classification?.who_region === who) && (!wb || row.classification?.wb_region === wb)
      && (!income || row.classification?.income_group === income)
      && (!status || (status === 'supported' ? active : !active));
  }), [rows, query, who, wb, income, status]);
  useEffect(() => {
    if (parentCode || !publication.data) return;
    const codes = filtered.map(row => row.code);
    (window as any).__globalIdCountryFilterCodes = codes;
    window.dispatchEvent(new CustomEvent('globalid:country-filter', { detail: { codes } }));
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries({ q: query, who, wb, income, status })) value ? url.searchParams.set(key, value) : url.searchParams.delete(key);
    window.history.replaceState({}, '', url);
  }, [filtered, query, who, wb, income, status, parentCode, publication.data]);
  if (!publication.data) return <p role="status" className="figure-caption">{publication.error ? pick('Current coverage is unavailable.', '当前覆盖数据暂不可用。', 'La couverture actuelle est indisponible.') : <GIDSLoader language={language} />}</p>;
  const regional = subdivisionRows(publication.data);
  const parents = [...new Set(regional.map((row: Row) => String(row.parent_code)))].map(code => ({
    ...((publication.data?.countries ?? []).find((row: Row) => row.code === code) || { code }),
    count: regional.filter((row: Row) => row.parent_code === code).length,
  }));
  const activeCount = rows.filter(hasData).length;
  const reset = () => { setQuery(''); setWho(''); setWb(''); setIncome(''); setStatus(''); };
  const options = (labels: Record<string, Record<string, string>>, values: string[]) => [...new Set(values.filter(Boolean))].sort().map(value => <option key={value} value={value}>{labels[value]?.[language] ?? value}</option>);
  return <div data-surveillance-version={publication.version} className="space-y-5">
    {!parentCode && <>
      <p className="figure-caption">{activeCount} {pick('active', '个已支持', 'actifs')} · {rows.length - activeCount} {pick('planned or unavailable', '个规划中或暂不可用', 'prévus ou indisponibles')}</p>
      {!!parents.length && <div className="border border-[rgb(var(--border))] p-4">
        <h3 className="font-semibold">{pick('Regional datasets by parent country', '按所属国家浏览分省／地区数据', 'Jeux régionaux par pays')}</h3>
        <p className="figure-caption mt-2">{regional.length} {pick('regional datasets, counted separately from national totals.', '个地区数据集，与国家级总量分开统计。', 'jeux régionaux, comptés séparément des totaux nationaux.')}</p>
        <div className="flex flex-wrap gap-2 mt-3">{parents.map(row => <a key={row.code} className="site-action-secondary border px-3 py-2 text-sm" href={`${prefix}/countries/${row.code.toLowerCase()}/#subnational-data`}>{countryLabel(row, language)} · {row.count}</a>)}</div>
      </div>}
    </>}
    {!parentCode && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <label className="coverage-filter"><span>{pick('Country or region', '国家或地区', 'Pays ou région')}</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={pick('Search name or code', '搜索名称或代码', 'Rechercher un nom ou code')} /></label>
      <label className="coverage-filter"><span>{pick('WHO region', 'WHO 区域', 'Région OMS')}</span><select value={who} onChange={event => setWho(event.target.value)}><option value="">{pick('All', '全部', 'Toutes')}</option>{options(WHO_REGION_LABELS, rows.map(row => row.classification?.who_region))}</select></label>
      <label className="coverage-filter"><span>{pick('World Bank region', '世界银行区域', 'Région Banque mondiale')}</span><select value={wb} onChange={event => setWb(event.target.value)}><option value="">{pick('All', '全部', 'Toutes')}</option>{options(WORLD_BANK_REGION_LABELS, rows.map(row => row.classification?.wb_region))}</select></label>
      <label className="coverage-filter"><span>{pick('Income group', '收入组', 'Groupe de revenu')}</span><select value={income} onChange={event => setIncome(event.target.value)}><option value="">{pick('All', '全部', 'Tous')}</option>{options(INCOME_GROUP_LABELS, rows.map(row => row.classification?.income_group))}</select></label>
      <label className="coverage-filter"><span>{pick('Data status', '数据状态', 'État des données')}</span><select value={status} onChange={event => setStatus(event.target.value)}><option value="">{pick('All', '全部', 'Tous')}</option><option value="supported">{pick('Supported', '已支持', 'Disponible')}</option><option value="scheduled">{pick('Scheduled', '规划中', 'Prévu')}</option></select></label>
      <button type="button" className="site-action-secondary border px-3 py-2 text-sm" onClick={reset}>{pick('Reset filters', '重置筛选', 'Réinitialiser les filtres')}</button>
    </div>}
    <p className="figure-caption" aria-live="polite">{filtered.length.toLocaleString()} {pick('countries and regions', '个国家和地区', 'pays et régions')}</p>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {filtered.map(row => { const active = hasData(row);
        const name = countryLabel(row, language);
        const content = <><div className="flex items-center gap-3"><img src={getFlagAssetPath(row.code) ?? undefined} alt="" width="32" height="24" loading="lazy" /><div><strong>{name}</strong><span className="block text-xs text-[rgb(var(--text-muted))]">{row.code}</span></div></div>
          <p className="mt-3 text-xs text-[rgb(var(--text-muted))]">{active ? `${Number(row.disease_count ?? 0).toLocaleString()} ${pick('diseases', '种疾病', 'maladies')} · ${[row.date_range?.start, row.date_range?.end].filter(Boolean).join(' → ')}` : row.status === 'Supported' ? pick('Data temporarily unavailable', '数据暂不可用', 'Données temporairement indisponibles') : pick('Planned source', '规划中的数据源', 'Source prévue')}</p></>;
        return active ? <a key={row.code} className="site-action-secondary border p-4 text-sm" href={`${prefix}/countries/${String(row.code).toLowerCase()}/`}>{content}</a> : <div key={row.code} className="border border-[rgb(var(--border))] p-4 text-sm">{content}
          {row.cadence && <p className="mt-2 text-xs">{pick('Reporting cadence', '报告频率', 'Fréquence de déclaration')}: {cadence(row.cadence, language)}</p>}
          {safeUrl(row.source_url) && <a className="underline mt-2 block text-xs" href={safeUrl(row.source_url)} target="_blank" rel="noopener noreferrer">{pick('Official source', '官方数据源', 'Source officielle')}: {row.source_name}</a>}
        </div>;
      })}
    </div>
    {!parentCode && <p className="figure-caption">{pick(`WHO regions and World Bank FY${COUNTRY_CLASSIFICATION_METADATA.worldBankFiscalYear} regions/income groups; catalogue updated ${COUNTRY_CLASSIFICATION_METADATA.updatedAt}.`, `WHO 区域及世界银行 ${COUNTRY_CLASSIFICATION_METADATA.worldBankFiscalYear} 财年区域／收入组；分类目录更新于 ${COUNTRY_CLASSIFICATION_METADATA.updatedAt}。`, `Régions OMS et régions/groupes de revenu Banque mondiale, exercice ${COUNTRY_CLASSIFICATION_METADATA.worldBankFiscalYear} ; catalogue mis à jour le ${COUNTRY_CLASSIFICATION_METADATA.updatedAt}.`)}</p>}
    {!filtered.length && <p className="figure-caption">{pick('No matching countries or regions.', '没有符合筛选条件的国家或地区。', 'Aucun pays ou région correspondant.')}</p>}
  </div>;
}
