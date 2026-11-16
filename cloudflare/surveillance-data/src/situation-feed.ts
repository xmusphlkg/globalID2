/** RSS is a view of published official notices, with stable alert identities. */
export type FeedLanguage = 'en' | 'zh' | 'fr';
export interface SituationFeedSelection {
  language: FeedLanguage;
  country?: string;
  disease?: string;
}

export const SITUATION_FEED_PATH = '/site-data/situation/rss.xml';
export const SITUATION_FEED_LIMIT = 100;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function text(value: unknown, limit = 2000): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

/** XML 1.0 cannot contain most control characters, even in escaped strings. */
function xml(value: string): string {
  return value.replace(/[^\u0009\u000A\u000D\u0020-\uD7FF\uE000-\uFFFD\u{10000}-\u{10FFFF}]/gu, '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

export function situationFeedSelection(url: URL): SituationFeedSelection | null {
  if ([...url.searchParams.keys()].some(key => !['lang', 'country', 'disease'].includes(key)
    || url.searchParams.getAll(key).length !== 1)) return null;
  const language = url.searchParams.get('lang') ?? 'en';
  const country = url.searchParams.get('country')?.toUpperCase();
  const disease = url.searchParams.get('disease')?.toUpperCase();
  if (!['en', 'zh', 'fr'].includes(language) || country !== undefined && !/^[A-Z]{2}(?:-[A-Z0-9]{1,8})?$/.test(country)
    || disease !== undefined && !/^D\d{3,6}$/.test(disease) || country && disease) return null;
  return { language: language as FeedLanguage, ...(country ? { country } : {}), ...(disease ? { disease } : {}) };
}

export function situationFeedUrl(origin: string, selection: SituationFeedSelection): URL {
  const url = new URL(SITUATION_FEED_PATH, origin);
  url.searchParams.set('lang', selection.language);
  if (selection.country) url.searchParams.set('country', selection.country);
  if (selection.disease) url.searchParams.set('disease', selection.disease);
  return url;
}

function publicUrl(value: unknown): string | null {
  try {
    const url = new URL(text(value));
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function publishedDate(value: unknown): string {
  const day = text(value, 30);
  const date = new Date(`${day}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== day) {
    throw new Error('Invalid notice publication date');
  }
  return date.toUTCString();
}

export function renderSituationFeed(payload: unknown, origin: string, selection: SituationFeedSelection): string {
  if (!record(payload) || payload.schema_version !== 'gids.situation_alerts.v1' || !Array.isArray(payload.alerts)) {
    throw new Error('Invalid published Situation feed');
  }
  const lang = selection.language;
  const pick = (en: string, zh: string, fr: string) => lang === 'zh' ? zh : lang === 'fr' ? fr : en;
  const prefix = lang === 'en' ? '' : `/${lang}`;
  const siteUrl = new URL(`${prefix}/situation/`, origin).href;
  const selected = payload.alerts.map(value => {
    if (!record(value)) throw new Error('Invalid published Situation notice');
    return value;
  }).filter(row => row.status !== 'routine'
    && (!selection.country || row.country_code === selection.country)
    && (!selection.disease || row.disease_id === selection.disease));
  selected.sort((a, b) => text(b.published_date).localeCompare(text(a.published_date)) || text(b.alert_id).localeCompare(text(a.alert_id)));
  const identities = new Set<string>();
  const entries = selected.filter(row => {
    const identity = text(row.alert_id, 200);
    if (!identity) throw new Error('Missing published notice identity');
    if (identities.has(identity)) return false;
    identities.add(identity);
    return true;
  }).slice(0, SITUATION_FEED_LIMIT);
  const labels: Record<string, string> = {
    new: pick('New report', '新发通报', 'Nouveau signalement'),
    imported: pick('Imported case report', '输入病例通报', 'Signalement de cas importé'),
    ongoing: pick('Ongoing event', '持续事件', 'Événement en cours'),
    ended: pick('Ended event', '已结束事件', 'Événement terminé'),
  };
  const items = entries.map(row => {
    const identity = text(row.alert_id, 200);
    const disease = text(lang === 'zh' ? row.disease_name_zh : lang === 'fr' ? row.disease_name_fr : row.disease_name_en, 300)
      || text(row.disease_name_en || row.disease_label, 300);
    const country = text(lang === 'zh' ? row.country_name_zh : lang === 'fr' ? row.country_name_fr : row.country_name_en, 200)
      || text(row.country_name_en || row.country_code, 200);
    const status = labels[text(row.status)] || pick('Official report', '官方通报', 'Rapport officiel');
    const host = row.host_type === 'human' ? pick('Human', '人类', 'Humain')
      : row.host_type === 'animal' ? pick('Animal', '动物', 'Animal') : pick('Host not specified', '宿主未明确', 'Hôte non précisé');
    const hostDetail = text(row.host_species, 200);
    const title = [disease, country, text(row.admin_area, 200), status].filter(Boolean).join(' · ');
    const summary = text(lang === 'zh' ? row.summary_zh : lang === 'fr' ? row.summary_fr : row.summary_en)
      || text(row.summary_en) || text(row.title_en, 400);
    const source = text(lang === 'zh' ? row.source_label_zh : row.source_label_en, 300) || text(row.source_id, 200);
    const description = [summary, [host, hostDetail].filter(Boolean).join(': '), source].filter(Boolean).join('\n');
    return `<item><title>${xml(title)}</title><link>${xml(publicUrl(row.url) || siteUrl)}</link>`
      + `<guid isPermaLink="false">${xml(`gids:situation:${identity}`)}</guid><pubDate>${publishedDate(row.published_date)}</pubDate>`
      + `<description>${xml(description)}</description><category>${xml(status)}</category><category>${xml(host)}</category></item>`;
  }).join('');
  const qualifier = selection.country || selection.disease;
  const title = `${pick('GIDS official outbreak reports', 'GIDS 官方疫情通报', 'GIDS — rapports officiels de flambée')}${qualifier ? ` · ${qualifier}` : ''}`;
  const description = pick(
    'The latest 100 published event notices, excluding routine monitoring. Publication dates come from the source reports. These notices do not independently confirm an outbreak or quantify individual risk.',
    '最新 100 条已发布事件通报，不含常规监测。发布日期来自原始报告；本站收录不构成对疫情的独立确认或个人风险评估。',
    'Les 100 derniers signalements publiés, hors surveillance de routine. Les dates proviennent des rapports sources ; leur présence ici ne constitue pas une confirmation indépendante ni une estimation du risque individuel.',
  );
  return '<?xml version="1.0" encoding="UTF-8"?>'
    + '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
    + `<title>${xml(title)}</title><link>${xml(siteUrl)}</link><description>${xml(description)}</description>`
    + `<language>${lang === 'zh' ? 'zh-CN' : lang}</language>`
    + `<atom:link href="${xml(situationFeedUrl(origin, selection).href)}" rel="self" type="application/rss+xml"/>`
    + '<ttl>5</ttl>' + items + '</channel></rss>';
}
