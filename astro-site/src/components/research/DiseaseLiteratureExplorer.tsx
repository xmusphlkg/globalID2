import { useEffect, useMemo, useRef, useState } from 'react';
import GIDSLoader from '../GIDSLoader';
import { articleHref, siteHref } from '../../lib/papers';
import { emptyLiteratureFilters, filterLiterature, literatureTrend, literatureYearRangeError, literatureIndexRecord, literaturePage, type LiteratureFilters, type LiteratureLocale } from '../../lib/research-literature';
import { localizedRegionName, localizedResearchTopic, localizedStudyType } from '../../utils/i18n';
import { createLiteraturePageClient, type LiteratureIndex } from '../../lib/research-literature-client';
import './disease-literature.css';

interface Props {
  diseaseId: string; initialArticles: any[]; total: number; locale: LiteratureLocale;
  initialTrend: Array<{ year: string; count: number }>;
  facets: { countries: any[]; studies: string[]; topics: string[] };
}
const filterKeys = Object.keys(emptyLiteratureFilters) as Array<keyof LiteratureFilters>;
export default function DiseaseLiteratureExplorer({ diseaseId, initialArticles, total, locale, initialTrend, facets }: Props) {
  const text = (en: string, zh: string, fr: string) => locale === 'zh' ? zh : locale === 'fr' ? fr : en;
  const [index, setIndex] = useState<LiteratureIndex | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [filters, setFilters] = useState<LiteratureFilters>(emptyLiteratureFilters);
  const [page, setPage] = useState(1);
  const [pageAttempt, setPageAttempt] = useState(0);
  const [cardState, setCardState] = useState<{ key: string; status: 'loading' | 'ready' | 'error'; articles: any[] }>({ key: '', status: 'ready', articles: initialArticles });
  const client = useRef(createLiteraturePageClient());
  const loaded = Boolean(index);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    setFilters(Object.fromEntries(filterKeys.map(key => [key, params.get(key === 'query' ? 'q' : key) ?? emptyLiteratureFilters[key]])) as unknown as LiteratureFilters);
    const requestedPage = Number(params.get('page') || '1');
    setPage(Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    let active = true;
    setError(false); setIndex(null);
    fetch(siteHref(`/research/catalogue/diseases/${diseaseId.toLowerCase()}.json`), { cache: 'no-cache', signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('Literature index unavailable');
        const payload = await response.json();
        if (payload.schema_version !== 2 || payload.disease_id !== diseaseId.toLowerCase() || !Array.isArray(payload.records) || payload.total !== payload.records.length || !Array.isArray(payload.shards)) throw new Error('Incomplete literature index');
        if (active) setIndex(payload);
      }).catch(() => { if (active) setError(true); }).finally(() => window.clearTimeout(timeout));
    return () => { active = false; controller.abort(); window.clearTimeout(timeout); };
  }, [diseaseId, attempt]);
  const records = useMemo(() => index?.records ?? initialArticles.map(article => literatureIndexRecord(article, 'preview')), [index, initialArticles]);
  const filtered = useMemo(() => filterLiterature(records, filters), [records, filters]);
  const selectedPage = useMemo(() => literaturePage(filtered, page), [filtered, page]);
  const pageKey = loaded ? JSON.stringify(selectedPage.records.map(record => [record.literature_id, record.shard])) : 'preview';
  useEffect(() => {
    if (!index) return;
    let active = true;
    setCardState({ key: pageKey, status: 'loading', articles: [] });
    const timer = window.setTimeout(() => {
      void client.current.read(index, selectedPage.records).then(articles => {
        if (active) setCardState({ key: pageKey, status: 'ready', articles });
      }).catch(() => { if (active) setCardState({ key: pageKey, status: 'error', articles: [] }); });
    }, 120);
    return () => { active = false; window.clearTimeout(timer); };
  }, [index, pageKey, pageAttempt]);
  useEffect(() => {
    if (!loaded) return;
    const url = new URL(location.href);
    for (const key of filterKeys) {
      const name = key === 'query' ? 'q' : key;
      if (filters[key] && filters[key] !== emptyLiteratureFilters[key]) url.searchParams.set(name, filters[key]); else url.searchParams.delete(name);
    }
    if (selectedPage.page > 1) url.searchParams.set('page', String(selectedPage.page)); else url.searchParams.delete('page');
    history.replaceState(null, '', url);
  }, [filters, loaded, selectedPage.page]);
  const yearError = literatureYearRangeError(filters);
  const trend = loaded ? literatureTrend(filtered) : initialTrend;
  const max = Math.max(1, ...trend.map(row => row.count));
  const set = (key: keyof LiteratureFilters, value: string) => { setFilters(current => ({ ...current, [key]: value })); setPage(1); };
  const goToPage = (next: number) => {
    setPage(next);
    requestAnimationFrame(() => document.querySelector<HTMLElement>('#literature-results-title')?.focus());
  };
  const loadingPage = loaded && (cardState.key !== pageKey || cardState.status === 'loading');
  const pageError = loaded && cardState.key === pageKey && cardState.status === 'error';
  const previewAllowed = page === 1 && filterKeys.every(key => filters[key] === emptyLiteratureFilters[key]);
  const cards = !loaded ? previewAllowed ? initialArticles : [] : cardState.key === pageKey && cardState.status === 'ready' ? cardState.articles : [];
  const sourceUrl = (article: any) => article.doi ? `https://doi.org/${article.doi}` : article.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${article.pmid}/` : article.open_access_url;
  const range = trend.length ? `${trend[0].year}–${trend.at(-1)!.year}` : '—';
  return <div className="disease-literature-explorer" data-literature-explorer data-catalogue-ready={loaded ? 'true' : 'false'}>
    <section className="literature-trends" aria-labelledby="literature-trend-title">
      <div className="literature-section-heading"><div><p className="academic-kicker">{text('Research activity', '研究动态', 'Activité de recherche')}</p><h2 id="literature-trend-title">{text('Publication trends', '发文趋势', 'Évolution des publications')}</h2></div><span>{range}</span></div>
      <p className="literature-note">{text('Annual publication counts in the GIDS catalogue. Filters update this chart; counts do not represent all worldwide publications or disease risk.', '按发表年份统计本站已收录文献，筛选条件会同步更新图表。数量不代表全球全部发文量，也不代表疾病风险。', 'Publications annuelles du catalogue GIDS. Les filtres actualisent ce graphique ; ces volumes ne représentent ni toute la littérature mondiale ni le risque de maladie.')}</p>
      {trend.length ? <><div className="literature-trend-chart" role="img" aria-label={text(`Publication counts, ${range}`, `${range} 年发文数量`, `Nombre de publications, ${range}`)}><svg viewBox="0 0 960 190" width="960" height="190" aria-hidden="true">
        <line x1="12" x2="948" y1="154" y2="154" stroke="currentColor" opacity=".2" />
        {trend.map((row, index) => { const step = 936 / trend.length; const height = row.count / max * 132; return <g key={row.year}><title>{`${row.year}: ${row.count}`}</title><rect x={12 + step * index + step * .15} y={154 - height} width={Math.max(1, step * .7)} height={Math.max(1, height)} rx="1" fill="currentColor" />{(index % Math.max(1, Math.ceil(trend.length / 10)) === 0 || index === trend.length - 1) && <text x={12 + step * index + step / 2} y="177" textAnchor="middle">{row.year}</text>}</g>; })}
      </svg></div><details className="literature-trend-table"><summary>{text('View publication counts', '查看各年数量', 'Voir les volumes annuels')}</summary><div><table><caption className="sr-only">{text('Annual publication counts', '各年发文数量', 'Volumes annuels')}</caption><thead><tr><th scope="col">{text('Year', '发表年份', 'Année')}</th><th scope="col">{text('Publications', '文献数', 'Publications')}</th></tr></thead><tbody>{trend.map(row => <tr key={row.year}><th scope="row">{row.year}</th><td>{row.count.toLocaleString(locale)}</td></tr>)}</tbody></table></div></details></> : <p>{text('No dated publications match these filters.', '当前筛选下没有带发表日期的文献。', 'Aucune publication datée ne correspond aux filtres.')}</p>}
    </section>
    <section className="literature-results" aria-labelledby="literature-results-title">
      <div className="literature-section-heading"><div><p className="academic-kicker">{text('Evidence stream', '证据流', 'Flux de preuves')}</p><h2 id="literature-results-title" tabIndex={-1}>{text('Published literature', '已发布文献', 'Publications disponibles')}</h2></div><span>{total.toLocaleString(locale)} {text('catalogued', '篇已收录', 'répertoriées')}</span></div>
      {!loaded && !error && <GIDSLoader language={locale} label={text('Loading the complete literature catalogue…', '正在读取完整文献目录…', 'Chargement du catalogue complet…')} compact />}
      {error && <p className="literature-load-error" role="status">{text('The search index could not be loaded. Please try again.', '文献筛选索引暂时无法读取，请重新加载。', 'L’index de recherche est indisponible. Réessayez.')} <button type="button" onClick={() => setAttempt(value => value + 1)}>{text('Retry', '重新加载', 'Réessayer')}</button></p>}
      <fieldset className="literature-filters" disabled={!loaded}>
        <legend className="sr-only">{text('Filter this disease’s literature', '筛选该疾病的相关文献', 'Filtrer les publications de cette maladie')}</legend>
        <label className="literature-query"><span>{text('Search within this disease', '在该疾病文献中搜索', 'Rechercher dans ces publications')}</span><input type="search" value={filters.query} placeholder={text('Title, author, journal, DOI or PMID', '标题、作者、期刊、DOI 或 PMID', 'Titre, auteur, revue, DOI ou PMID')} onChange={event => set('query', event.target.value)} /></label>
        <label><span>{text('Country or region', '国家或地区', 'Pays ou région')}</span><select value={filters.country} onChange={event => set('country', event.target.value)}><option value="">{text('All countries and regions', '全部国家和地区', 'Tous les pays et régions')}</option>{facets.countries.map(row => <option key={row.code} value={row.code}>{localizedRegionName(locale, row.code, { en: row.name_en, zh: row.name_zh, fr: row.name_fr })}</option>)}</select></label>
        <label><span>{text('Study type', '研究类型', 'Type d’étude')}</span><select value={filters.study} onChange={event => set('study', event.target.value)}><option value="">{text('All study types', '全部研究类型', 'Tous les types')}</option>{facets.studies.map(value => <option key={value} value={value}>{localizedStudyType(locale, value)}</option>)}</select></label>
        <label><span>{text('Public health topic', '公共卫生主题', 'Thème de santé publique')}</span><select value={filters.topic} onChange={event => set('topic', event.target.value)}><option value="">{text('All topics', '全部主题', 'Tous les thèmes')}</option>{facets.topics.map(value => <option key={value} value={value}>{localizedResearchTopic(locale, value)}</option>)}</select></label>
        <label><span>{text('From year', '起始年份', 'Depuis')}</span><input type="number" min="1800" max="2199" placeholder="1900" value={filters.from} onChange={event => set('from', event.target.value)} /></label>
        <label><span>{text('Through year', '截止年份', 'Jusqu’à')}</span><input type="number" min="1800" max="2199" placeholder={String(new Date().getUTCFullYear())} value={filters.to} onChange={event => set('to', event.target.value)} /></label>
        <label><span>{text('Full text', '全文获取', 'Texte intégral')}</span><select value={filters.access} onChange={event => set('access', event.target.value)}><option value="">{text('All access types', '全部获取方式', 'Tous les accès')}</option><option value="open">{text('Open access', '开放获取', 'Accès ouvert')}</option><option value="other">{text('Other or unknown', '其他或暂未确认', 'Autre ou inconnu')}</option></select></label>
        <label><span>{text('Record type', '记录类型', 'Type de fiche')}</span><select value={filters.evidence} onChange={event => set('evidence', event.target.value)}><option value="">{text('All records', '全部记录', 'Toutes les fiches')}</option><option value="summary">{text('With GIDS evidence summary', '附 GIDS 证据摘要', 'Avec synthèse GIDS')}</option><option value="bibliographic">{text('Bibliographic records', '仅书目信息', 'Fiches bibliographiques')}</option></select></label>
        <label><span>{text('Publication status', '发表状态', 'Statut de publication')}</span><select value={filters.publication} onChange={event => set('publication', event.target.value)}><option value="">{text('All publication statuses', '全部发表状态', 'Tous les statuts')}</option><option value="peer_reviewed">{text('Peer reviewed', '同行评议', 'Évalué par les pairs')}</option><option value="preprint">{text('Reviewed preprints', '已审核预印本', 'Prépublications vérifiées')}</option></select></label>
        <label><span>{text('Sort by', '排序', 'Trier par')}</span><select value={filters.sort} onChange={event => set('sort', event.target.value)}><option value="newest">{text('Newest first', '最新发表优先', 'Les plus récentes')}</option><option value="oldest">{text('Oldest first', '最早发表优先', 'Les plus anciennes')}</option><option value="title">{text('Title A–Z', '标题 A–Z', 'Titre A–Z')}</option></select></label>
        <button type="button" className="site-action-secondary literature-reset" onClick={() => { setFilters({ ...emptyLiteratureFilters }); setPage(1); }}>{text('Reset filters', '重置筛选', 'Réinitialiser')}</button>
      </fieldset>
      {yearError && <p className="literature-load-error" role="alert">{yearError === 'reversed' ? text('The starting year must not be later than the ending year.', '起始年份不能晚于截止年份。', 'L’année de début ne doit pas dépasser l’année de fin.') : text('Enter four-digit years between 1800 and 2199.', '请输入 1800 至 2199 之间的四位年份。', 'Saisissez des années à quatre chiffres entre 1800 et 2199.')}</p>}
      <p className="literature-result-count" role="status" aria-live="polite">{loaded ? text(`${filtered.length.toLocaleString(locale)} matching records · page ${selectedPage.page} of ${selectedPage.totalPages}`, `符合条件 ${filtered.length.toLocaleString(locale)} 篇 · 第 ${selectedPage.page} / ${selectedPage.totalPages} 页`, `${filtered.length.toLocaleString(locale)} publications · page ${selectedPage.page} sur ${selectedPage.totalPages}`) : text(`Previewing ${initialArticles.length} of ${total.toLocaleString(locale)} records`, `先展示 ${total.toLocaleString(locale)} 篇中的 ${initialArticles.length} 篇`, `Aperçu de ${initialArticles.length} publications sur ${total.toLocaleString(locale)}`)}</p>
      {loadingPage && <GIDSLoader language={locale} label={text('Loading this page…', '正在读取本页文献…', 'Chargement de cette page…')} compact />}
      {pageError && <p className="literature-load-error" role="status">{text('This page could not be loaded.', '本页文献暂时无法读取。', 'Cette page est indisponible.')} <button type="button" onClick={() => setPageAttempt(value => value + 1)}>{text('Retry page', '重试本页', 'Réessayer cette page')}</button></p>}
      <div className="literature-list" id="disease-literature-list" aria-busy={loadingPage}>{cards.map(article => {
        const hasSummary = article.detail_available !== false;
        const href = hasSummary && article.slug ? articleHref(article.slug, locale) : sourceUrl(article);
        const title = locale === 'zh' ? article.title_zh || article.title : locale === 'fr' ? article.title_fr || article.title : article.title;
        return <article className="research-card literature-card" key={article.article_id || article.doi || article.slug} data-literature-record>
          <div className="literature-card-meta"><span>{localizedStudyType(locale, article.study_type)}</span><time dateTime={article.published_at}>{String(article.published_at ?? '').slice(0, 10) || text('Date unavailable', '发表日期暂缺', 'Date indisponible')}</time>{article.peer_review_status === 'preprint' && <span>{text('Preprint · not peer reviewed', '预印本 · 尚未经同行评议', 'Prépublication · sans évaluation par les pairs')}</span>}{article.open_access_status === 'open' && <span className="literature-open">{text('Open access', '开放获取', 'Accès ouvert')}</span>}</div>
          <h3 className="research-card-title">{href ? <a href={href} {...(!hasSummary ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>{title}</a> : title}</h3>
          <p className="literature-authors">{(article.authors ?? []).slice(0, 3).join(locale === 'zh' ? '、' : ', ')}{(article.authors ?? []).length > 3 && text(' et al.', ' 等', ' et al.')}<span>{article.journal}</span></p>
          <p className="literature-locations">{(article.countries ?? []).slice(0, 4).map((row: any) => localizedRegionName(locale, row.code, { en: row.name_en, zh: row.name_zh, fr: row.name_fr })).join(' · ')}</p>
          <div className="literature-card-links">{hasSummary && article.slug && <a href={articleHref(article.slug, locale)}>{text('Read evidence summary', '阅读证据摘要', 'Lire la synthèse')}</a>}{article.doi && <a href={`https://doi.org/${article.doi}`} target="_blank" rel="noopener noreferrer">DOI ↗</a>}{article.pmid && <a href={`https://pubmed.ncbi.nlm.nih.gov/${article.pmid}/`} target="_blank" rel="noopener noreferrer">PubMed ↗</a>}{article.open_access_status === 'open' && article.open_access_url && <a href={article.open_access_url} target="_blank" rel="noopener noreferrer">{text('Full text', '查看全文', 'Texte intégral')} ↗</a>}{!hasSummary && <small>{text('Bibliographic record · no GIDS summary', '书目记录，暂无 GIDS 证据摘要', 'Fiche bibliographique sans synthèse GIDS')}</small>}</div>
        </article>;
      })}</div>
      {loaded && !filtered.length && <div className="literature-empty"><h3>{text('No matching publications', '没有符合条件的文献', 'Aucune publication correspondante')}</h3><p>{text('Try fewer filters or another search term.', '可减少筛选条件，或尝试其他关键词。', 'Essayez moins de filtres ou un autre terme.')}</p></div>}
      {loaded && <nav className="literature-pagination" aria-label={text('Literature pagination', '文献分页', 'Pagination des publications')}>
        <button type="button" disabled={selectedPage.page <= 1 || loadingPage} onClick={() => goToPage(selectedPage.page - 1)} aria-controls="disease-literature-list">{text('Previous page', '上一页', 'Page précédente')}</button>
        <span>{text(`Page ${selectedPage.page} of ${selectedPage.totalPages}`, `第 ${selectedPage.page} / ${selectedPage.totalPages} 页`, `Page ${selectedPage.page} sur ${selectedPage.totalPages}`)}</span>
        <button type="button" disabled={selectedPage.page >= selectedPage.totalPages || loadingPage} onClick={() => goToPage(selectedPage.page + 1)} aria-controls="disease-literature-list">{text('Next page', '下一页', 'Page suivante')}</button>
        {selectedPage.totalPages > 1 && <label><span>{text('Go to page', '跳转到页', 'Aller à la page')}</span><select aria-label={text('Go to page', '跳转到页', 'Aller à la page')} value={selectedPage.page} disabled={loadingPage} onChange={event => goToPage(Number(event.target.value))}>{Array.from({ length: selectedPage.totalPages }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select></label>}
      </nav>}
    </section>
  </div>;
}
