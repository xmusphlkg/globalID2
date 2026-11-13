import { groupResearchSignals } from '../../../astro-site/src/lib/research-signal-groups.ts';

/** Versioned renderer for public structured paper records; no private fields or raw HTML. */
// Bump on any renderer behavior change so deployed response caches cannot reuse old HTML.
export const PROJECTION_RENDERER_REVISION = 'gids-evidence-v1.20261113.1';
export type ProjectedPaper = { schema: 3; slug: string; article_id: string; content: Record<string, unknown> };
export type ProjectionTemplate = {
  schema: 3; content_schema: 3; renderer: 'gids-evidence-v1'; locale: 'en' | 'zh' | 'fr';
  shell: { schema: 1; chunks: Array<string | { field: 'slug' | 'main' } | { field: 'head'; index: number }> };
  scope_attributes: string[];
};
export type ProjectionAssets = { schema: 1; items: Array<{ path: string; key: string; sha256: string; bytes: number }> };
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const string = (value: unknown): string => typeof value === 'string' ? value : '';
const escape = (value: unknown) => string(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);
const rows = (value: unknown) => Array.isArray(value) ? value.filter(object) : [];
function safeUrl(value: unknown): string | null {
  if (typeof value !== 'string' || /[\u0000-\u0020\\]/.test(value)) return null;
  if (/^\/(?!\/)/.test(value)) return value;
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null; } catch { return null; }
}
const labels = {
  en: ['Research question', 'Study design', 'Population and setting', 'Main findings', 'Public-health relevance', 'Important limitations', 'GIDS interpretation', 'Structured evidence summary', 'Sources', 'Related research', 'Editorially approved AI-assisted summary.', 'AI-generated summary; not editorially approved. Verify the original source.', 'Evidence summary pending. Consult the original article.', 'Research Radar', 'Integrity status', 'Publication history'],
  zh: ['研究问题', '研究设计', '人群与场景', '主要发现', '公共卫生意义', '重要局限', 'GIDS 解读', '结构化证据摘要', '来源', '相关研究', 'AI 辅助生成的摘要已通过编辑审核。', '此摘要由 AI 生成，尚未通过编辑审核。请核对原始来源。', '证据摘要待完善，请查阅原始文献。', '研究雷达', '更正与撤稿状态', '发表历史'],
  fr: ['Question de recherche', 'Plan d’étude', 'Population et contexte', 'Résultats principaux', 'Pertinence pour la santé publique', 'Limites importantes', 'Interprétation GIDS', 'Résumé structuré des preuves', 'Sources', 'Publications associées', 'Résumé assisté par IA et approuvé par la rédaction.', 'Résumé généré par IA, non approuvé par la rédaction. Vérifiez la source originale.', 'Résumé en attente. Consultez l’article original.', 'Radar de recherche', 'Intégrité', 'Historique de publication'],
};
const sections = ['research_question', 'study_design', 'population_setting', 'main_findings', 'public_health_relevance', 'limitations', 'gids_interpretation'];

export function projectionFragments(template: ProjectionTemplate, record: ProjectedPaper, recordSha: string): { head: string[]; main: string } {
  if (!object(template) || template.schema !== 3 || template.content_schema !== 3 || template.renderer !== 'gids-evidence-v1'
      || !['en', 'zh', 'fr'].includes(template.locale) || !Array.isArray(template.scope_attributes)
      || template.scope_attributes.some(value => !/^data-astro-cid-[a-z0-9]{4,32}$/.test(value))
      || !object(record) || record.schema !== 3 || !/^[a-z0-9][a-z0-9-]{0,199}$/.test(record.slug)
      || !record.article_id || typeof record.article_id !== 'string' || !object(record.content)
      || !/^[a-f0-9]{64}$/.test(recordSha)) throw new Error('Invalid paper projection contract');
  const data = record.content, locale = template.locale, text = labels[locale];
  if (typeof data.title !== 'string' || !data.title.trim() || data.editorial_status !== 'published') throw new Error('Paper projection is not public');
  const summaryMap = object(data.summary) ? data.summary : {};
  const summary = object(summaryMap[locale]) ? summaryMap[locale] : {};
  const provenance = object(summary.provenance) ? summary.provenance : {};
  const prefix = locale === 'en' ? '' : `/${locale}`;
  const canonical = `https://papers.globalinfectiousdisease.com${prefix}/research/articles/${record.slug}/`;
  const description = string(data[`why_it_matters_${locale}`]) || string(summary.public_health_relevance) || data.title;
  const authors = Array.isArray(data.authors) ? data.authors.filter(value => typeof value === 'string') : [];
  const sourceUrls = object(data.source_urls) ? data.source_urls : {};
  const pick = (en: string, zh: string, fr: string) => locale === 'zh' ? zh : locale === 'fr' ? fr : en;
  const localName = (value: Record<string, unknown>) => string(value[`name_${locale}`]) || string(value.name_en) || string(value.name) || string(value.label) || string(value.code);
  const language = locale === 'zh' ? 'zh-CN' : locale === 'fr' ? 'fr-FR' : 'en';
  const socialImage = `https://globalinfectiousdisease.com/og-default.png`;
  const structuredArticle = { '@type': 'ScholarlyArticle', headline: data.title,
    identifier: string(data.doi) || record.article_id, url: canonical, inLanguage: locale,
    datePublished: string(data.published_at) || undefined, dateModified: string(data.updated_at) || undefined,
    author: authors.map(name => ({ '@type': 'Person', name })),
    sameAs: safeUrl(sourceUrls.doi) || safeUrl(sourceUrls.publisher) || undefined,
    about: rows(data.diseases).map(value => ({ '@type': 'MedicalCondition', name: localName(value) })),
    isPartOf: data.journal ? { '@type': 'Periodical', name: string(data.journal) } : undefined };
  const breadcrumb = { '@type': 'BreadcrumbList', itemListElement: [
    { '@type': 'ListItem', position: 1, name: pick('Home', '首页', 'Accueil'), item: `https://globalinfectiousdisease.com${prefix}/` },
    { '@type': 'ListItem', position: 2, name: text[13], item: `https://globalinfectiousdisease.com${prefix}/research/` },
    { '@type': 'ListItem', position: 3, name: pick('Evidence summary', '证据摘要', 'Résumé des preuves'), item: canonical },
  ] };
  const structured = { '@context': 'https://schema.org', '@graph': [
    { '@type': 'MedicalWebPage', '@id': `${canonical}#webpage`, url: canonical, name: data.title,
      description, inLanguage: language, isPartOf: { '@type': 'WebSite', name: 'GIDS', url: 'https://globalinfectiousdisease.com/' } },
    structuredArticle, breadcrumb,
  ] };
  const json = JSON.stringify(structured).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
  const alternates = ([['en', 'en'], ['zh', 'zh'], ['zh-CN', 'zh'], ['fr', 'fr'], ['fr-FR', 'fr']] as const)
    .map(([hreflang, lang]) => `<link rel="alternate" hreflang="${hreflang}" href="https://papers.globalinfectiousdisease.com${lang === 'en' ? '' : `/${lang}`}/research/articles/${record.slug}/">`).join('');
  const head = `<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(data.title)} | GIDS</title><meta name="description" content="${escape(description.slice(0, 240))}"><link rel="canonical" href="${canonical}">`
    + alternates + `<link rel="alternate" hreflang="x-default" href="https://papers.globalinfectiousdisease.com/research/articles/${record.slug}/">`
    + `<meta name="robots" content="${data.indexable === false ? 'noindex,follow' : 'index,follow'}"><meta property="og:type" content="article"><meta property="og:site_name" content="GIDS"><meta property="og:locale" content="${locale === 'zh' ? 'zh_CN' : locale === 'fr' ? 'fr_FR' : 'en_US'}"><meta property="og:title" content="${escape(data.title)}"><meta property="og:description" content="${escape(description.slice(0, 240))}"><meta property="og:image" content="${socialImage}"><meta property="og:image:alt" content="${escape(data.title)}"><meta property="og:url" content="${canonical}">`
    + `<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="${escape(data.title)}"><meta name="twitter:description" content="${escape(description.slice(0, 240))}"><meta name="twitter:image" content="${socialImage}"><meta name="twitter:image:alt" content="${escape(data.title)}">`
    + (data.published_at ? `<meta property="article:published_time" content="${escape(data.published_at)}">` : '')
    + (data.updated_at ? `<meta property="article:modified_time" content="${escape(data.updated_at)}">` : '')
    + `<script type="application/ld+json">${json}</script>`;
  const allSources = { ...sourceUrls, ...(safeUrl(data.open_access_url) ? { 'Open access': data.open_access_url } : {}), ...(safeUrl(data.license_url) ? { License: data.license_url } : {}) };
  const sourceLinks = Object.entries(allSources).flatMap(([name, value]) => { const url = safeUrl(value); return url ? [`<li><a href="${escape(url)}" rel="noopener noreferrer">${escape(name)}</a></li>`] : []; }).join('');
  const siteLink = (value: unknown) => { const url = safeUrl(value); return url?.startsWith('/') ? `https://globalinfectiousdisease.com${prefix}${url}` : url; };
  const link = (url: string | null, label: string) => url ? `<a href="${escape(url)}">${escape(label)}</a>` : escape(label);
  const integrityLabel: Record<string, string> = { current: pick('No known notices', '未发现完整性通知', 'Aucune notification connue'), corrected: pick('Corrected', '已更正', 'Corrigé'), retracted: pick('Retracted', '已撤稿', 'Rétracté'), expression_of_concern: pick('Expression of concern', '已发布关注声明', 'Expression de préoccupation') };
  const peerReview = data.peer_review_status === 'peer_reviewed' ? pick('Peer reviewed', '同行评议', 'Évalué par les pairs') : data.peer_review_status === 'preprint' ? pick('Preprint', '预印本', 'Prépublication') : pick('Peer review status unknown', '同行评议状态未知', 'Statut d’évaluation inconnu');
  const metadata = [['DOI', data.doi], ['PMID', data.pmid], ['PMCID', data.pmcid], ['OpenAlex', data.openalex_id], [text[14], integrityLabel[string(data.integrity_status)] || data.integrity_status], [text[1], data.study_type], [pick('Publisher', '出版商', 'Éditeur'), data.publisher], [pick('Article type', '文章类型', 'Type de publication'), data.article_type]];
  const integrity = rows(data.integrity_events).map(event => `<li><strong>${escape(event.event_type)}</strong> <time>${escape(event.effective_at || event.recorded_at)}</time> ${escape(event.source)}${safeUrl(event.source_url) ? ` <a href="${escape(safeUrl(event.source_url))}">${escape(text[8])}</a>` : ''}</li>`).join('');
  const related = rows(data.related_articles).map(article => /^[a-z0-9][a-z0-9-]{0,199}$/.test(string(article.slug)) ? `<li><a href="https://papers.globalinfectiousdisease.com${prefix}/research/articles/${escape(article.slug)}/">${escape(article.title)}</a><p>${escape(article.journal)} · ${escape(article.study_type)}</p><p>${escape((Array.isArray(article[`reasons_${locale}`]) ? article[`reasons_${locale}`] as unknown[] : Array.isArray(article.reasons_en) ? article.reasons_en : []).filter(value => typeof value === 'string').join(' · '))}</p></li>` : '').join('');
  const topicSlug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  const panels = ([['topics', pick('Topics', '主题', 'Thèmes')], ['pathogens', pick('Pathogens', '病原体', 'Agents pathogènes')], ['populations', pick('Populations', '研究人群', 'Populations')], ['countries', pick('Geographic scope', '地理范围', 'Zone géographique')]] as const).map(([name, title]) => {
    const items = rows(data[name]);
    return items.length ? `<section class="evidence-aside-block"><p class="evidence-kicker">${title}</p><ul>${items.map(value => `<li>${link(name === 'topics' && (string(value.slug) || topicSlug(string(value.name) || string(value.name_en))) ? siteLink(`/research/topics/${string(value.slug) || topicSlug(string(value.name) || string(value.name_en))}/`) : name === 'countries' && /^[a-z]{2}(?:-[a-z0-9]+)?$/i.test(string(value.code)) ? siteLink(`/research/countries/${string(value.code).toLowerCase()}/`) : null, localName(value))}</li>`).join('')}</ul></section>` : '';
  }).join('');
  const surveillance = rows(data.related_surveillance).map(value => `<li>${link(siteLink(value.url), string(value[`disease_name_${locale}`]) || string(value.disease_name_en))} ${escape(value[`country_name_${locale}`] || value.country_name_en)}</li>`).join('')
    || rows(data.diseases).map(value => `<li>${link(/^[a-z0-9][a-z0-9-]*$/.test(string(value.slug)) ? siteLink(`/diseases/${value.slug}/`) : null, localName(value))}</li>`).join('');
  const publicSignals = rows(data.related_signals).map(value => ({
    signal_id: string(value.signal_id), disease_id: string(value.disease_id) || undefined, disease_name_en: string(value.disease_name_en),
    disease_name_zh: string(value.disease_name_zh), disease_name_fr: string(value.disease_name_fr),
    relation_level: string(value.relation_level) || 'disease_context', data_through: string(value.data_through),
    situation_url: string(value.situation_url),
    geographies: rows(value.geographies).map(place => ({ code: string(place.code), name_en: string(place.name_en), name_zh: string(place.name_zh), name_fr: string(place.name_fr) })),
    change_pct: object(value.window) && typeof value.window.change_pct === 'number' && Number.isFinite(value.window.change_pct) ? value.window.change_pct : null,
  }));
  const placeName = (place: { code?: string; name_en?: string; name_zh?: string; name_fr?: string }) => string(place[`name_${locale}`]) || string(place.name_en) || string(place.code);
  const signals = groupResearchSignals(publicSignals).map(group => {
    const places = [...group.places.values()], preview = places.slice(0, 4).map(placeName).join(' · ') || pick('Geography unavailable', '地理范围暂缺', 'Zone géographique indisponible');
    const extra = places.length > 4 ? pick(` +${places.length - 4} more`, ` 等另 ${places.length - 4} 个地区`, ` +${places.length - 4} autres`) : '';
    const relation = group.relation === 'exact_disease_geography' ? pick('Exact disease + geography', '疾病 + 地区精确匹配', 'Maladie + zone géographique exacte')
      : group.relation === 'historical_disease_geography_context' ? pick('Historical disease + geography context', '疾病 + 地区历史背景', 'Contexte historique de maladie et de zone géographique')
      : pick('Disease context only', '仅疾病背景', 'Contexte de maladie uniquement');
    return `<details class="signal-group"><summary><span class="signal-group-heading"><strong>${escape(group.disease[`disease_name_${locale}`] || group.disease.disease_name_en)}</strong><em>${relation}</em></span><span class="signal-group-places">${escape(preview + extra)}</span><span class="signal-group-meta">${group.signals.length} ${pick('signals', '条信号', 'signaux')}${group.latest ? ` · ${pick('Latest', '最新', 'Dernière date')} <time datetime="${escape(group.latest)}">${escape(group.latest)}</time>` : ''} · <span class="signal-expand">${pick('Details', '查看详情', 'Détails')}</span></span></summary><div class="signal-group-details">${group.signals.map(signal => {
      const url = siteLink(signal.situation_url), label = signal.geographies.map(placeName).join(' · ') || pick('Geography unavailable', '地理范围暂缺', 'Zone géographique indisponible');
      const contents = `<span>${escape(label)}</span><time datetime="${escape(signal.data_through)}">${escape(signal.data_through) || '—'}</time>${signal.change_pct == null ? '' : `<strong>${signal.change_pct > 0 ? '+' : ''}${signal.change_pct}%</strong>`}<span aria-hidden="true">→</span>`;
      return url ? `<a href="${escape(url)}">${contents}</a>` : `<span>${contents}</span>`;
    }).join('')}</div></details>`;
  }).join('');
  const graph = object(data.knowledge_graph) ? data.knowledge_graph : {};
  const nodes = new Map(rows(graph.nodes).map(value => [value.id, value]));
  const relationLabels: Record<string, string> = { ABOUT_DISEASE: pick('Disease', '疾病', 'Maladie'), ADDRESSES_TOPIC: pick('Topic', '研究主题', 'Thème'), STUDIED_IN: pick('Location', '研究地区', 'Lieu'), USES_STUDY_DESIGN: text[1], STUDIES_PATHOGEN: pick('Pathogen', '病原体', 'Agent pathogène'), HAS_PATHOGEN_TYPE: pick('Pathogen type', '病原体类型', 'Type d’agent pathogène'), STUDIED_POPULATION_SETTING: pick('Study setting', '研究场景', 'Cadre de l’étude'), STUDIES_POPULATION: pick('Population', '研究人群', 'Population') };
  const relationships = rows(graph.edges).map(value => { const target = nodes.get(value.target); if (!target) return ''; const label = string(target[`name_${locale}`]) || string(target.label); return `<li><small>${escape(relationLabels[string(value.relation)] || pick('Related', '关联', 'Relation'))}</small> ${link(siteLink(target.url), label)}</li>`; }).join('');
  const versions = rows(data.version_relations).map(value => { const preprint = data.peer_review_status === 'preprint'; const doi = string(value[preprint ? 'peer_reviewed_doi' : 'preprint_doi']); const url = siteLink(value[preprint ? 'peer_reviewed_url' : 'preprint_url']) || safeUrl(`https://doi.org/${doi}`); return doi ? `<li>${link(url, `${preprint ? pick('Peer-reviewed version', '同行评议版本', 'Version évaluée par les pairs') : pick('Linked preprint', '关联预印本', 'Prépublication associée')}: ${doi}`)}</li>` : ''; }).join('');
  const context = `<section class="evidence-surveillance"><div class="section-title"><span>02</span><h2>${pick('Related GIDS surveillance', '相关 GIDS 监测', 'Surveillance GIDS associée')}</h2></div><ul class="surveillance-links">${surveillance}</ul>${signals ? `<div class="related-signal-context" data-context-version="2"><p class="evidence-kicker">${pick('Active Situation Room context', '当前全球态势室背景', 'Contexte de la salle de situation')}</p><div class="related-signal-list">${signals}</div></div>` : ''}<p class="evidence-disclaimer">${pick('Literature links provide context, not evidence of a signal’s cause. Disease-only matches do not establish geographic relevance.', '文献关联仅供背景参考，不代表信号成因证据；仅疾病匹配不代表地区相关。', 'Les liens bibliographiques apportent du contexte, sans établir la cause du signal. Une maladie commune ne suffit pas à établir un lien géographique.')}</p></section>`;
  const correctionNotice = data.integrity_status === 'corrected' && integrity ? `<section class="integrity-correction-notice" aria-labelledby="correction-notice-title"><div><p class="evidence-kicker">${pick('Integrity update', '完整性更新', 'Mise à jour d’intégrité')}</p><h2 id="correction-notice-title">${pick('This public record has a correction history', '此公开记录存在更正历史', 'Ce dossier public a un historique de corrections')}</h2></div><p>${pick('Review the dated events and verify the linked source record before using the findings.', '使用研究结论前，请查看带日期的事件并核对所链接的来源记录。', 'Consultez les événements datés et vérifiez la source liée avant d’utiliser ces résultats.')}</p><a href="https://globalinfectiousdisease.com${prefix}/research/integrity/">${pick('All integrity alerts →', '查看全部完整性通知 →', 'Toutes les alertes d’intégrité →')}</a></section>` : '';
  let main = `<main id="main-content" tabindex="-1" class="flex-1 academic-shell"><article class="evidence-page" data-research-article-id="${escape(record.article_id)}" data-research-article-sha256="${recordSha}"><nav aria-label="${pick('Breadcrumb', '面包屑', 'Fil d’Ariane')}"><a href="https://globalinfectiousdisease.com${prefix}/research/">${text[13]}</a> <span aria-hidden="true">/</span> <span>${pick('Evidence summary', '证据摘要', 'Résumé des preuves')}</span></nav><header class="academic-hero evidence-header"><div class="evidence-badges"><span>${peerReview}</span>${data.open_access_status === 'open' ? `<span class="oa">${pick('Open access', '开放获取', 'Accès ouvert')}</span>` : ''}<span>${escape(integrityLabel[string(data.integrity_status)] || data.integrity_status)}</span>${rows(data.diseases).map(value => `<span>${escape(localName(value))}</span>`).join('')}</div><h1>${escape(data.title)}</h1><p class="evidence-citation">${escape(data.journal)} · <time datetime="${escape(data.published_at)}">${escape(data[`publication_date_label_${locale}`] || data.published_at)}</time></p><p class="evidence-authors">${escape(authors.join(', '))}</p><dl class="evidence-register">${metadata.map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value) || '—'}</dd></div>`).join('')}</dl></header>${correctionNotice}${versions ? `<section class="version-link-notice" aria-labelledby="version-link-title"><div><p class="evidence-kicker">${pick('Publication version', '发表版本', 'Version de publication')}</p><h2 id="version-link-title">${pick('Related publication version', '关联发表版本', 'Version de publication associée')}</h2><ul>${versions}</ul></div></section>` : ''}<div class="evidence-layout"><div><section class="evidence-context"><p>${escape(description)}</p></section>${integrity ? `<section class="article-integrity-history"><h2>${text[15]}</h2><ol>${integrity}</ol></section>` : ''}<section class="evidence-sections"><div class="section-title"><span>01</span><h2>${text[7]}</h2></div>${sections.map((key, index) => `<div class="evidence-section"><h3>${text[index]}</h3><p>${escape(summary[key]) || text[12]}</p></div>`).join('')}</section>${surveillance || signals ? context : ''}${relationships ? `<section class="evidence-surveillance evidence-graph"><div class="section-title"><span>03</span><h2>${pick('Evidence relationships', '证据关系', 'Relations de preuves')}</h2></div><ul class="relationship-list">${relationships}</ul></section>` : ''}${related ? `<section class="evidence-surveillance related-research"><div class="section-title"><span>04</span><h2>${text[9]}</h2></div><ol>${related}</ol></section>` : ''}</div><aside>${panels}<section class="evidence-aside-block"><h2>${text[8]}</h2><ul class="source-links">${sourceLinks}</ul></section><section class="evidence-aside-block evidence-score"><p class="evidence-kicker">GIDS Discovery Score</p><strong>${Math.round((typeof data.discovery_score === 'number' ? data.discovery_score : 0) * 100)}</strong><p>${pick('A transparent discovery ranking, not a scientific quality or credibility score.', '该分数用于排列相关文献，不代表研究质量或可信度。', 'Un classement de découverte, pas une note de qualité scientifique ou de crédibilité.')}</p></section><section class="evidence-aside-block evidence-provenance"><p class="evidence-kicker">${pick('Summary transparency', '摘要透明度', 'Transparence du résumé')}</p><p>${provenance.editorially_approved === true ? text[10] : text[11]}</p>${provenance.quality_checked === true ? `<p>${pick('Automated quality checks passed before publication.', '发布前已通过自动质量检查。', 'Les contrôles qualité automatisés ont été réussis avant publication.')}</p>` : ''}</section></aside></div></article></main>`;
  if (template.scope_attributes.length) main = main.replace(/<([a-z][a-z0-9]*)(?=[\s>])/g, `<$1 ${template.scope_attributes.join(' ')}`);
  return { head: [head], main };
}
