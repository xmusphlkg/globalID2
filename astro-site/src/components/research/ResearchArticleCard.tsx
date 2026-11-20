import type { ReactNode } from 'react';
import { articleHref, MAIN_ORIGIN } from '../../lib/papers';
import { normalizeDiseaseHref } from '../../lib/seo';
import { localizedDiseaseName } from '../../utils/diseaseNames';
import { localizedDynamicText, localizedRegionName, localizedResearchTopic, localizedStudyType, type Lang } from '../../utils/i18n';

interface Props {
  article: any;
  locale?: Lang;
  compact?: boolean;
  featured?: boolean;
  literatureRecord?: boolean;
  focusDiseaseId?: string;
  index?: number;
  initialVisible?: number;
}

// PubMed titles may contain inline scientific notation. Keep all other text escaped.
function publicationTitle(title: string): ReactNode {
  const parts: ReactNode[] = [];
  let start = 0;
  for (const match of title.matchAll(/<(sup|sub)>([^<>]*)<\/\1>/gi)) {
    const Tag = match[1].toLowerCase() === 'sup' ? 'sup' : 'sub';
    parts.push(title.slice(start, match.index), <Tag key={match.index}>{match[2]}</Tag>);
    start = match.index + match[0].length;
  }
  return parts.length ? [...parts, title.slice(start)] : title;
}

export default function ResearchArticleCard({ article, locale = 'en', compact, featured, literatureRecord, focusDiseaseId, index, initialVisible }: Props) {
  const zh = locale === 'zh';
  const fr = locale === 'fr';
  const text = (en: string, chinese: string, french: string) => zh ? chinese : fr ? french : en;
  const prefix = locale === 'en' ? '' : `/${locale}`;
  const mainLink = (path: string) => path.startsWith('/') ? `${import.meta.env.DEV ? '' : MAIN_ORIGIN}${prefix}${path}` : path;
  const external = { target: '_blank', rel: 'noopener noreferrer' };
  const doi = article.doi && `https://doi.org/${article.doi}`;
  const pubmed = article.pmid && `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(article.pmid)}/`;
  const openAccess = article.open_access_status === 'open';
  const fullText = openAccess && (article.open_access_url || article.source_urls?.publisher);
  const summary = article.detail_available !== false && article.slug ? articleHref(article.slug, locale) : null;
  const titleUrl = summary || doi || pubmed || article.open_access_url;
  const title = publicationTitle(String(article[`title_${locale}`] || article.title || ''));
  const publishedDate = article[`publication_date_label_${locale}`] || (article.published_at
    ? new Date(article.published_at).toLocaleDateString(text('en-US', 'zh-CN', 'fr-FR'), { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' })
    : text('Date unavailable', '日期暂缺', 'Date indisponible'));

  const diseases = article.diseases ?? [];
  const countries = article.countries ?? [];
  const topics = article.topics ?? [];
  const topicSlug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const focusMatch = focusDiseaseId && diseases.find((item: any) => String(item.disease_id).toLowerCase() === focusDiseaseId.toLowerCase());
  const visibleDiseases = focusMatch ? [focusMatch, ...diseases.filter((item: any) => item !== focusMatch)] : diseases;
  const confidence = Number(focusMatch?.confidence ?? 0);
  const authors: string[] = (article.authors ?? []).filter(Boolean);
  const authorPreview = authors.slice(0, 3).join(zh ? '、' : ', ');
  const remaining = authors.length - 3;
  const authorLine = remaining > 0 ? authorPreview + text(` +${remaining} more`, ` 等 ${authors.length} 位作者`, ` et ${remaining} autre${remaining > 1 ? 's' : ''}`) : authorPreview;
  const surveillance = article.related_surveillance?.[0];
  const disease = diseases[0];
  const surveillanceUrl = normalizeDiseaseHref(surveillance?.url || (disease?.slug ? `/diseases/${disease.slug}/` : null));
  const surveillanceName = surveillance ? { ...disease, name_en: surveillance.disease_name_en, name_zh: surveillance.disease_name_zh, name_fr: surveillance.disease_name_fr } : disease;
  const diseaseName = surveillanceName ? localizedDiseaseName(surveillanceName, locale) : '';
  const relevance = article.why_it_matters_en || article.why_it_matters_zh || article.why_it_matters_fr || article.summary?.fr?.public_health_relevance;
  const historical = article.source_kind === 'historical_seed' || article.historical_baseline;
  const reviewed = article.peer_review_status === 'peer_reviewed';
  const preprint = article.peer_review_status === 'preprint';
  const variant = compact ? 'compact' : featured ? 'featured' : 'stream';

  return <article className={`research-card research-card-${variant}`} hidden={index !== undefined && initialVisible !== undefined && index >= initialVisible}
    data-research-card="" data-literature-record={literatureRecord || undefined} data-card-index={index}
    data-disease={diseases.map((item: any) => `${item.disease_id} ${item.name_en} ${item.name_zh ?? ''} ${item.name_fr ?? ''}`).join(' ').toLowerCase()}
    data-disease-ids={diseases.map((item: any) => item.disease_id).join(' ').toLowerCase()}
    data-country={countries.map((item: any) => `${item.code} ${item.name_en} ${item.name_zh ?? ''} ${item.name_fr ?? ''}`).join(' ').toLowerCase()}
    data-country-codes={countries.map((item: any) => item.code).join(' ').toLowerCase()}
    data-topic={topics.map((item: any) => item.name).join(' ').toLowerCase()} data-topic-slugs={topics.map((item: any) => topicSlug(item.name)).join(' ')}
    data-study={article.study_type?.toLowerCase()} data-journal={article.journal?.toLowerCase()} data-publisher={article.publisher?.toLowerCase()}
    data-published-at={article.published_at} data-peer-review-status={article.peer_review_status} data-open-access={openAccess}>
    <div className="research-card-topline">
      <div className="research-card-badges">
        {(reviewed || preprint) && <span className="research-badge research-badge-peer">{reviewed ? text('Peer reviewed', '同行评议', 'Évalué par les pairs') : text('Preprint', '预印本', 'Prépublication')}</span>}
        {historical && <span className="research-badge research-badge-history">{text('Historical baseline', '历史基线', 'Référence historique')}</span>}
        {openAccess && <span className="research-badge research-badge-oa">{text('Open access', '开放获取', 'Accès ouvert')}</span>}
        {focusMatch && <span className="research-badge" title={text(`Disease-classifier match confidence: ${confidence.toFixed(2)}; this is not a study-quality score.`, `疾病分类器关联置信度 ${confidence.toFixed(2)}；这不是研究质量评分。`, `Confiance du classement de l’association à la maladie : ${confidence.toFixed(2)} ; ce n’est pas une évaluation de la qualité de l’étude.`)}>
          {confidence >= 0.78 ? text('Strong disease match', '疾病关联较强', 'Association forte à la maladie') : text('Possible disease match', '疾病关联待核对', 'Association à vérifier')}
        </span>}
        {visibleDiseases.slice(0, compact ? 1 : 2).map((item: any) => <span key={item.disease_id} className="research-badge">{localizedDiseaseName(item, locale)}</span>)}
        {topics.slice(0, compact ? 1 : 3).map((item: any) => <a key={item.name} className="research-badge research-badge-topic" href={mainLink(`/research/topics/${topicSlug(item.name)}/`)}>{localizedResearchTopic(locale, item.name)}</a>)}
      </div>
      {doi && <a className="research-card-doi" href={doi} {...external}>DOI {article.doi} ↗</a>}
    </div>
    <h3 className="research-card-title">{titleUrl ? <a href={titleUrl} {...(summary ? {} : external)}>{title}</a> : title}</h3>
    {authorLine && <p className="research-card-authors">{authorLine}</p>}
    <p className="research-card-source">
      <span>{article.journal || text('Journal unavailable', '期刊信息暂缺', 'Revue indisponible')}</span>
      <span aria-hidden="true">·</span><time dateTime={article.published_at}>{publishedDate}</time>
    </p>
    {(fullText || surveillanceUrl || (literatureRecord && (summary || pubmed))) && <div className="research-card-actions">
      {literatureRecord && summary && <a className="research-card-action" href={summary}>{text('Read evidence summary', '阅读证据摘要', 'Lire la synthèse')}</a>}
      {fullText && <a className="research-card-action research-card-action-oa" href={fullText} {...external}>{text('Open article ↗', '打开全文 ↗', 'Ouvrir l’article ↗')}</a>}
      {literatureRecord && pubmed && <a className="research-card-action" href={pubmed} {...external}>PubMed ↗</a>}
      {surveillanceUrl && <a className="research-card-action" href={mainLink(surveillanceUrl)}>{text(`${diseaseName} surveillance`, `${diseaseName}监测`, `${diseaseName} — surveillance`)} →</a>}
    </div>}
    {!compact && <>
      <dl className="research-card-facts">
        <div><dt>{text('Type', '类型', 'Type')}</dt><dd>{localizedStudyType(locale, article.study_type)}</dd></div>
        <div><dt>{text('Scope', '范围', 'Portée')}</dt><dd>{countries.length ? countries.slice(0, literatureRecord ? 4 : 3).map((item: any) => localizedRegionName(locale, item.code, { en: item.name_en, zh: item.name_zh, fr: item.name_fr })).join(zh ? '、' : ', ') : text('Not classified', '未分类', 'Non classé')}</dd></div>
      </dl>
      {article.detail_available === false
        ? <small className="research-card-source">{text('Bibliographic record · no GIDS summary', '书目记录，暂无 GIDS 证据摘要', 'Fiche bibliographique sans synthèse GIDS')}</small>
        : relevance && <p className="research-context"><strong>{text('Why it matters now', '为何现在值得关注', 'Pourquoi cette étude compte maintenant')}</strong>
          <span>{fr ? localizedDynamicText('fr', { en: article.why_it_matters_en ?? '', fr: article.why_it_matters_fr || article.summary?.fr?.public_health_relevance }, { context: `l’article ${article.article_id ?? article.slug}` }) : (zh && article.why_it_matters_zh) || article.why_it_matters_en}</span></p>}
    </>}
  </article>;
}
