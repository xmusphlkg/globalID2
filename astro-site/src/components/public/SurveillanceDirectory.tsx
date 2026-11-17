import GIDSLoader from '../GIDSLoader';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import { COUNTRY_COVERAGE } from '../../lib/country-coverage.ts';
import { getFlagAssetPath } from '../../lib/country-flag';
import { COUNTRY_CLASSIFICATION_METADATA, getCountryClassification, WHO_REGION_LABELS, WORLD_BANK_REGION_LABELS, INCOME_GROUP_LABELS } from '../../lib/country-classifications.ts';

import { countryLabel } from '../../lib/surveillance-presentation.ts';
import GeographicAreaCards from './GeographicAreaCards';
import DirectoryPagination from './DirectoryPagination';
import { hasData, subdivisionRows, parentAreaCode } from '../../lib/surveillance-coverage';
import { directoryPage, directoryPageFromSearch, syncDirectoryPage } from '../../lib/directory-pagination';
import './surveillance-directory.css';
type Language = 'en' | 'zh' | 'fr';
type Row = Record<string, any>;
const normalize = (value: unknown) => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

function DirectoryIcon({ kind }: { kind: 'search' | 'arrow' | 'close' | 'reset' }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === 'search' ? <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></> : kind === 'arrow' ? <path d="M5 12h14m-5-5 5 5-5 5" /> : kind === 'close' ? <path d="m7 7 10 10M7 17 17 7" /> : <><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6" /></>}
  </svg>;
}

export default function SurveillanceDirectory({ language = 'en', parentCode }: { language?: Language; parentCode?: string }) {
  const publication = useSurveillanceSummary('/site-data/meta.json');
  const initial = typeof window === 'undefined' ? new URLSearchParams() : new URLSearchParams(window.location.search);
  const [query, setQuery] = useState(initial.get('q') ?? '');
  const [who, setWho] = useState(initial.get('who') ?? '');
  const [wb, setWb] = useState(initial.get('wb') ?? '');
  const [income, setIncome] = useState(initial.get('income') ?? '');
  const [status, setStatus] = useState(initial.get('status') ?? '');
  const pageScope = JSON.stringify([parentCode, query, who, wb, income, status]);
  const [pagination, setPagination] = useState(() => ({ scope: pageScope, page: directoryPageFromSearch(initial.toString()) }));
  const grid = useRef<HTMLDivElement>(null);
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  const prefix = language === 'en' ? '' : `/${language}`;
  const rows = useMemo<Row[]>(() => {
    const saved = new Map((publication.data?.countries ?? []).map((row: Row) => [String(row.code).toUpperCase(), row]));
    if (parentCode) return [...saved.values()].filter((row: any) => parentAreaCode(row) === parentCode.toUpperCase()) as Row[];
    const base = new Map(COUNTRY_COVERAGE.map(row => [row.code.toUpperCase(), { ...row, data_available: false } as Row]));
    for (const [code, row] of saved) {
      // Planned territories can already have data under a parent pipeline.
      // Merge their real snapshot instead of leaving a stale planned card.
      if (base.has(String(code)) || (!(row as Row).parent_code && (row as Row).location_type !== 'subdivision')) {
        base.set(String(code), { ...base.get(String(code)), ...(row as Row) });
      }
    }
    return [...base.values()].map(row => ({ ...row, classification: getCountryClassification(row.code) })).sort((a, b) => Number(hasData(b)) - Number(hasData(a)));
  }, [publication.data, parentCode]);
  const filtered = useMemo(() => rows.filter(row => {
    const active = hasData(row);
    return (!query.trim() || normalize([row.code, row.name_en, row.name_zh, countryLabel(row, 'fr'), row.name].join(' ')).includes(normalize(query.trim())))
      && (!who || row.classification?.who_region === who) && (!wb || row.classification?.wb_region === wb)
      && (!income || row.classification?.income_group === income)
      && (!status || (status === 'supported' ? active : !active));
  }), [rows, query, who, wb, income, status]);
  const page = directoryPage(filtered, pagination.scope === pageScope ? pagination.page : 1);
  useEffect(() => { setPagination(previous => previous.scope === pageScope ? previous : { scope: pageScope, page: 1 }); }, [pageScope]);
  useEffect(() => { if (publication.data) syncDirectoryPage(page.page); }, [page.page, publication.data]);
  const changePage = (number: number) => {
    setPagination({ scope: pageScope, page: number });
    grid.current?.scrollIntoView({ block: 'start' });
  };
  useEffect(() => {
    if (parentCode || !publication.data) return;
    const codes = filtered.map(row => row.code);
    (window as any).__globalIdCountryFilterCodes = codes;
    window.dispatchEvent(new CustomEvent('globalid:country-filter', { detail: { codes } }));
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries({ q: query, who, wb, income, status })) value ? url.searchParams.set(key, value) : url.searchParams.delete(key);
    window.history.replaceState(window.history.state, '', url);
  }, [filtered, query, who, wb, income, status, parentCode, publication.data]);
  if (!publication.data) return <div role="status" className="surveillance-directory">
    {publication.error ? <>
      <p className="figure-caption">{pick('Current coverage is unavailable.', '当前覆盖数据暂不可用。', 'La couverture actuelle est indisponible.')}</p>
      <button type="button" className="directory-reset" onClick={publication.retry}>{pick('Retry', '重试', 'Réessayer')}</button>
    </> : <GIDSLoader language={language} />}
  </div>;
  const regional = subdivisionRows(publication.data).map((row: Row) => ({ ...row, parent_code: parentAreaCode(row) }));
  const parents = [...new Set(regional.map((row: Row) => String(row.parent_code)))].map(code => ({
    ...((publication.data?.countries ?? []).find((row: Row) => row.code === code) || { code }),
    count: regional.filter((row: Row) => row.parent_code === code).length,
  })).sort((a, b) => countryLabel(a, language).localeCompare(countryLabel(b, language), language));
  const activeCount = rows.filter(hasData).length;
  const reset = () => { setQuery(''); setWho(''); setWb(''); setIncome(''); setStatus(''); };
  const activeFilters = [
    { key: 'q', value: query, label: query, clear: () => setQuery('') },
    { key: 'who', value: who, label: WHO_REGION_LABELS[who as keyof typeof WHO_REGION_LABELS]?.[language] ?? who, clear: () => setWho('') },
    { key: 'wb', value: wb, label: WORLD_BANK_REGION_LABELS[wb as keyof typeof WORLD_BANK_REGION_LABELS]?.[language] ?? wb, clear: () => setWb('') },
    { key: 'income', value: income, label: INCOME_GROUP_LABELS[income as keyof typeof INCOME_GROUP_LABELS]?.[language] ?? income, clear: () => setIncome('') },
    { key: 'status', value: status, label: status === 'supported' ? pick('Supported', '已支持', 'Disponible') : pick('Planned or unavailable', '规划中或暂不可用', 'Prévu ou indisponible'), clear: () => setStatus('') },
  ].filter(filter => filter.value);
  const options = (labels: Record<string, Record<string, string>>, values: string[]) => [...new Set(values.filter(Boolean))].sort().map(value => <option key={value} value={value}>{labels[value]?.[language] ?? value}</option>);
  return <div data-surveillance-version={publication.version} className="surveillance-directory">
    {!parentCode && <>
      <div className="directory-summary">
        <span className="directory-availability"><i aria-hidden="true" /><strong>{activeCount}</strong> {pick('active areas', '个区域已支持', 'zones disponibles')}</span>
        <span><strong>{rows.length - activeCount}</strong> {pick('planned or unavailable', '个规划中或暂不可用', 'prévues ou indisponibles')}</span>
      </div>
      {!!parents.length && <section className="directory-subdivisions" aria-label={pick('Subdivision datasets', '下级行政单位数据集', 'Jeux de données des subdivisions')}>
        <div className="directory-subdivisions-heading">
          <h3>{pick('Subdivision datasets by parent area', '按上级区域浏览分省／地区数据', 'Jeux régionaux par zone')}</h3>
          <span className="directory-subdivisions-total">{regional.length} {pick('datasets', '个数据集', 'jeux de données')}</span>
        </div>
        <p>{regional.length} {pick('subdivision datasets, counted separately from parent-area totals.', '个下级行政单位数据集，与上级区域总量分开统计。', 'jeux de données des subdivisions, comptés séparément des totaux de leur zone parente.')}</p>
        <div className="directory-parent-links">{parents.map(row => <a key={row.code} href={`${prefix}/countries/${row.code.toLowerCase()}/#subnational-data`}>
          <img src={getFlagAssetPath(row.code)} alt="" width="20" height="15" loading="lazy" />
          <span>{countryLabel(row, language)}</span><span className="directory-parent-count">{row.count}</span>
        </a>)}</div>
      </section>}
    </>}
    {!parentCode && <section className="directory-filters" aria-label={pick('Filter geographic areas', '筛选地理区域', 'Filtrer les zones géographiques')}>
      <div className="directory-search-row">
        <label className="directory-field directory-search"><span>{pick('Search geographic areas', '搜索地理区域', 'Rechercher une zone géographique')}</span>
          <span className="directory-search-control"><DirectoryIcon kind="search" /><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={pick('Search by name or code, e.g. Japan or JP', '搜索名称或代码，例如：日本或 JP', 'Nom ou code, par ex. Japon ou JP')} /></span>
        </label>
        <label className="directory-field"><span>{pick('Data status', '数据状态', 'État des données')}</span><select value={status} onChange={event => setStatus(event.target.value)}><option value="">{pick('All data statuses', '全部数据状态', 'Tous les états')}</option><option value="supported">{pick('Supported', '已支持', 'Disponible')}</option><option value="scheduled">{pick('Planned or unavailable', '规划中或暂不可用', 'Prévu ou indisponible')}</option></select></label>
      </div>
      <div className="directory-classifications">
        <label className="directory-field"><span>{pick('WHO region', 'WHO 区域', 'Région OMS')}</span><select value={who} onChange={event => setWho(event.target.value)}><option value="">{pick('All WHO regions', '全部 WHO 区域', 'Toutes les régions OMS')}</option>{options(WHO_REGION_LABELS, rows.map(row => row.classification?.who_region))}</select></label>
        <label className="directory-field"><span>{pick('World Bank region', '世界银行区域', 'Région Banque mondiale')}</span><select value={wb} onChange={event => setWb(event.target.value)}><option value="">{pick('All World Bank regions', '全部世界银行区域', 'Toutes les régions Banque mondiale')}</option>{options(WORLD_BANK_REGION_LABELS, rows.map(row => row.classification?.wb_region))}</select></label>
        <label className="directory-field"><span>{pick('Income group', '收入组', 'Groupe de revenu')}</span><select value={income} onChange={event => setIncome(event.target.value)}><option value="">{pick('All income groups', '全部收入组', 'Tous les groupes de revenu')}</option>{options(INCOME_GROUP_LABELS, rows.map(row => row.classification?.income_group))}</select></label>
      </div>
    </section>}
    <div className="directory-results-toolbar">
      <p role="status"><strong>{filtered.length.toLocaleString()}</strong> {pick(`of ${rows.length} geographic areas`, `/ ${rows.length} 个地理区域`, `sur ${rows.length} zones géographiques`)}</p>
      {!parentCode && <button type="button" className="directory-reset" disabled={!activeFilters.length} onClick={reset}><DirectoryIcon kind="reset" />{pick('Reset filters', '重置筛选', 'Réinitialiser les filtres')}</button>}
    </div>
    {!parentCode && !!activeFilters.length && <div className="directory-active-filters" aria-label={pick('Active filters', '已选筛选条件', 'Filtres actifs')}>
      {activeFilters.map(filter => <button key={filter.key} type="button" onClick={filter.clear} aria-label={`${pick('Remove filter', '移除筛选条件', 'Supprimer le filtre')}: ${filter.label}`}><span>{filter.label}</span><DirectoryIcon kind="close" /></button>)}
    </div>}
    <GeographicAreaCards rows={page.rows} language={language} gridRef={grid} />
    <DirectoryPagination page={page} language={language} onPage={changePage} />
    {!filtered.length && <div className="directory-empty"><DirectoryIcon kind="search" /><h3>{pick('No matching areas', '没有符合条件的区域', 'Aucune zone correspondante')}</h3><p>{pick('Try another name or remove a filter to broaden your search.', '试试其他名称，或移除部分筛选条件以扩大搜索范围。', 'Essayez un autre nom ou supprimez un filtre pour élargir la recherche.')}</p><button type="button" className="directory-reset" onClick={reset}>{pick('Clear all filters', '清除全部筛选', 'Effacer tous les filtres')}</button></div>}
    {!parentCode && <p className="directory-provenance">{pick(`WHO regions and World Bank FY${COUNTRY_CLASSIFICATION_METADATA.worldBankFiscalYear} regions/income groups; catalogue updated ${COUNTRY_CLASSIFICATION_METADATA.updatedAt}.`, `WHO 区域及世界银行 ${COUNTRY_CLASSIFICATION_METADATA.worldBankFiscalYear} 财年区域／收入组；分类目录更新于 ${COUNTRY_CLASSIFICATION_METADATA.updatedAt}。`, `Régions OMS et régions/groupes de revenu Banque mondiale, exercice ${COUNTRY_CLASSIFICATION_METADATA.worldBankFiscalYear} ; catalogue mis à jour le ${COUNTRY_CLASSIFICATION_METADATA.updatedAt}.`)}</p>}
  </div>;
}
