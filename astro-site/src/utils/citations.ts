/**
 * Inline citation rendering utilities.
 */

export type CitationSource = {
  id?: number | string | null;
  source_id?: number | string | null;
  citation_index?: number | string | null;
  source_name?: string | null;
  source_type?: string | null;
  title?: string | null;
  url?: string | null;
  resolved_url?: string | null;
  license?: string | null;
  fetched_at?: string | null;
  accessed_at?: string | null;
  updated_at?: string | null;
  publication_date?: string | null;
  published_at?: string | null;
  [key: string]: unknown;
};

const CITATION_RE = /\[(\d+)\]/g;
const CITATION_GROUP_RE = /(?:\[\d+\])+/g;

function toInt(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && /^\d+$/.test(value.trim())) return Number(value.trim());
  return null;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function cleanText(value: unknown): string | null {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const text = String(value).replace(/\s+/g, ' ').trim();
  return text || null;
}

function stripTrailingPeriod(value: string): string {
  return value.replace(/[.。]+$/g, '').trim();
}

function sentence(value: string | null | undefined): string | null {
  const text = cleanText(value);
  if (!text) return null;
  return /[.!?。！？]$/.test(text) ? text : `${stripTrailingPeriod(text)}.`;
}

function metadataValue(source: CitationSource, key: string): string | null {
  const direct = cleanText(source[key]);
  if (direct) return direct;
  const metadata = source.metadata;
  if (metadata && typeof metadata === 'object' && !Array.isArray(metadata)) {
    return cleanText((metadata as Record<string, unknown>)[key]);
  }
  return null;
}

function referenceLookup(source: CitationSource): string {
  return [
    source.source_type,
    source.source_name,
    metadataValue(source, 'provider'),
    metadataValue(source, 'content_kind'),
    referenceUrl(source),
  ]
    .map((value) => cleanText(value)?.toLowerCase())
    .filter(Boolean)
    .join(' ');
}

function doiFromUrl(url: string | null): string | null {
  if (!url) return null;
  let decoded = url;
  try {
    decoded = decodeURIComponent(url);
  } catch {
    decoded = url;
  }
  const match = decoded.match(/(?:doi\.org\/|doi:\s*)(10\.\d{4,9}\/[^\s?#]+)/i);
  return match ? stripTrailingPeriod(match[1].replace(/[),;]+$/g, '')) : null;
}

function pmidFromUrl(url: string | null): string | null {
  if (!url) return null;
  const match = url.match(/pubmed\.ncbi\.nlm\.nih\.gov\/(\d+)/i) ?? url.match(/\bPMID:?\s*(\d+)\b/i);
  return match ? match[1] : null;
}

function referenceDoi(source: CitationSource): string | null {
  const doi = metadataValue(source, 'doi') ?? doiFromUrl(referenceUrl(source));
  if (!doi) return null;
  return doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '').trim();
}

function referencePmid(source: CitationSource): string | null {
  return metadataValue(source, 'pmid') ?? pmidFromUrl(referenceUrl(source));
}

function isPubMedSource(source: CitationSource): boolean {
  const lookup = referenceLookup(source);
  return lookup.includes('pubmed') || Boolean(referencePmid(source));
}

function isScholarlySource(source: CitationSource): boolean {
  if (isPubMedSource(source)) return false;
  const lookup = referenceLookup(source);
  return Boolean(referenceDoi(source))
    || lookup.includes('crossref')
    || lookup.includes('scholarly_metadata')
    || Boolean(metadataValue(source, 'container_title'))
    || Boolean(metadataValue(source, 'journal'));
}

function authorPart(firstAuthor: string | null): string | null {
  if (!firstAuthor) return null;
  const author = stripTrailingPeriod(firstAuthor);
  return /\bet\s+al\.?$/i.test(author) ? `${author}.` : `${author} et al.`;
}

function normalizedSourceName(source: CitationSource): string {
  const rawType = cleanText(source.source_type)?.toLowerCase() ?? '';
  const rawName = cleanText(source.source_name);
  const lookup = `${rawName ?? rawType}`.toLowerCase();
  if (lookup === 'who' || rawType === 'who' || rawType === 'who_don') return 'World Health Organization';
  if (lookup.includes('wikidata') || rawType === 'wikidata') return 'Wikidata contributors';
  if (lookup.includes('wikipedia') || rawType === 'wikipedia') return 'Wikipedia contributors';
  if (lookup.includes('pubmed') || rawType === 'pubmed') return 'National Library of Medicine';
  if (lookup.includes('msd') || rawType === 'msd') return 'MSD Manual';
  return rawName ?? cleanText(source.source_type) ?? 'Unknown source';
}

function sourceContainer(source: CitationSource): string | null {
  const rawType = cleanText(source.source_type)?.toLowerCase() ?? '';
  if (rawType === 'pubmed') return 'PubMed';
  if (rawType === 'wikidata') return 'Wikidata';
  if (rawType === 'wikipedia') return 'Wikipedia';
  if (rawType === 'who_don') return 'Disease Outbreak News';
  return null;
}

function formatReferenceDate(source: CitationSource): string | null {
  const raw = cleanText(source.publication_date)
    ?? cleanText(source.published_at)
    ?? cleanText(source.fetched_at)
    ?? cleanText(source.accessed_at)
    ?? cleanText(source.updated_at);
  if (!raw) return null;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: 'numeric' });
}

function sourceKey(source: CitationSource, index: number): string {
  const sourceId = toInt(source.source_id) ?? toInt(source.id);
  if (sourceId != null) return `id:${sourceId}`;
  const url = String(source.resolved_url ?? source.url ?? '').trim();
  if (url) return `url:${url}`;
  const title = String(source.title ?? source.source_name ?? '').trim();
  if (title) return `title:${title}`;
  return `index:${index}`;
}

function extractMarkers(texts: Array<string | null | undefined>): number[] {
  const markers: number[] = [];
  for (const text of texts) {
    if (!text) continue;
    let match: RegExpExecArray | null;
    while ((match = CITATION_RE.exec(text)) !== null) {
      markers.push(Number(match[1]));
    }
  }
  return markers;
}

function resolveMarkerMode(markers: number[], sources: CitationSource[]): 'position' | 'source_id' {
  if (!markers.length) return 'position';
  const sourceIds = new Set(sources.map((source) => toInt(source.source_id) ?? toInt(source.id)).filter((value): value is number => value != null));
  const sourceCount = sources.length;
  const uniqueMarkers = Array.from(new Set(markers));

  if (uniqueMarkers.some((marker) => sourceIds.has(marker) && !(marker >= 1 && marker <= sourceCount))) {
    return 'source_id';
  }
  if (uniqueMarkers.some((marker) => marker >= 1 && marker <= sourceCount && !sourceIds.has(marker))) {
    return 'position';
  }
  if (uniqueMarkers.every((marker) => marker >= 1 && marker <= sourceCount)) {
    return 'position';
  }
  return 'source_id';
}

function resolveMarker(marker: number, sources: CitationSource[], mode: 'position' | 'source_id'): string | null {
  if (mode === 'position') {
    const positional = sources[marker - 1];
    if (positional) return sourceKey(positional, marker - 1);
    const byCitationIndex = sources.find((source) => toInt(source.citation_index) === marker);
    return byCitationIndex ? sourceKey(byCitationIndex, sources.indexOf(byCitationIndex)) : null;
  }

  const bySourceId = sources.find((source) => (toInt(source.source_id) ?? toInt(source.id)) === marker);
  if (bySourceId) return sourceKey(bySourceId, sources.indexOf(bySourceId));
  const byCitationIndex = sources.find((source) => toInt(source.citation_index) === marker);
  return byCitationIndex ? sourceKey(byCitationIndex, sources.indexOf(byCitationIndex)) : null;
}

export function normalizeCitationSources(
  sources: CitationSource[],
  texts: Array<string | null | undefined> = [],
): CitationSource[] {
  const cleanSources = sources.filter((source) => source && typeof source === 'object');
  const markers = extractMarkers(texts);
  const mode = resolveMarkerMode(markers, cleanSources);
  const orderedKeys: string[] = [];
  const sourceByKey = new Map<string, CitationSource>();

  cleanSources.forEach((source, index) => {
    sourceByKey.set(sourceKey(source, index), source);
  });

  for (const marker of markers) {
    const key = resolveMarker(marker, cleanSources, mode);
    if (key && !orderedKeys.includes(key)) orderedKeys.push(key);
  }

  cleanSources.forEach((source, index) => {
    const key = sourceKey(source, index);
    if (!orderedKeys.includes(key)) orderedKeys.push(key);
  });

  const normalized: CitationSource[] = [];
  orderedKeys.forEach((key, index) => {
    const source = sourceByKey.get(key);
    if (source) {
      normalized.push({ ...source, citation_index: index + 1 });
    }
  });
  return normalized;
}

export function referenceTooltip(source: CitationSource, fallbackIndex: number): string {
  const title = formatReferenceCitation(source, fallbackIndex);
  const url = referenceUrl(source);
  return [title, url].filter(Boolean).join(' · ');
}

export function referenceUrl(source: CitationSource): string | null {
  return cleanText(source.resolved_url) ?? cleanText(source.url);
}

export function referenceLinkLabel(source: CitationSource): string {
  if (isPubMedSource(source)) return 'PubMed';
  if (referenceDoi(source)) return 'DOI';
  return 'Available from';
}

export function formatReferenceCitation(source: CitationSource, fallbackIndex = 1): string {
  if (isPubMedSource(source)) {
    const firstAuthor = metadataValue(source, 'first_author') ?? metadataValue(source, 'author');
    const title = cleanText(source.title) ?? `PubMed record ${fallbackIndex}`;
    const journal = metadataValue(source, 'journal') ?? metadataValue(source, 'container_title');
    const pubDate = metadataValue(source, 'pub_date')
      ?? metadataValue(source, 'publication_date')
      ?? metadataValue(source, 'published_at');
    const pmid = referencePmid(source);
    const doi = referenceDoi(source);
    return [
      authorPart(firstAuthor),
      sentence(title),
      !firstAuthor && !journal ? 'PubMed indexed record.' : null,
      sentence(journal),
      sentence(pubDate),
      pmid ? `PMID: ${pmid}.` : null,
      doi ? `doi: ${doi}.` : null,
    ].filter(Boolean).join(' ');
  }

  if (isScholarlySource(source)) {
    const title = cleanText(source.title) ?? `Scholarly reference ${fallbackIndex}`;
    const container = metadataValue(source, 'container_title') ?? metadataValue(source, 'journal');
    const year = metadataValue(source, 'year')
      ?? metadataValue(source, 'pub_date')
      ?? metadataValue(source, 'publication_date')
      ?? metadataValue(source, 'published_at');
    const doi = referenceDoi(source);
    return [
      sentence(title),
      container ? sentence(container) : 'Scholarly DOI record.',
      sentence(year),
      doi ? `doi: ${doi}.` : null,
    ].filter(Boolean).join(' ');
  }

  const author = normalizedSourceName(source);
  const title = cleanText(source.title);
  const container = sourceContainer(source);
  const date = formatReferenceDate(source);
  const label = title && title.toLowerCase() !== author.toLowerCase()
    ? stripTrailingPeriod(title)
    : `Reference ${fallbackIndex}`;
  const parts = [`${stripTrailingPeriod(author)}. ${label} [Internet]`];
  if (container && container.toLowerCase() !== label.toLowerCase()) {
    parts.push(stripTrailingPeriod(container));
  }
  if (date) {
    parts.push(`cited ${date}`);
  }
  return `${parts.filter(Boolean).join('. ')}.`;
}

/**
 * Convert citation markers like [1], [2][3] in text to clickable superscript links.
 */
export function renderCitations(text: string, sources: CitationSource[]): string {
  if (!text) return '';

  const normalizedSources = sources.some((source) => toInt(source.citation_index) != null)
    ? [...sources].sort((a, b) => (toInt(a.citation_index) ?? Number.MAX_SAFE_INTEGER) - (toInt(b.citation_index) ?? Number.MAX_SAFE_INTEGER))
    : normalizeCitationSources(sources, [text]);
  const byNumber = new Map<number, CitationSource>();
  normalizedSources.forEach((source, index) => {
    const citationIndex = toInt(source.citation_index) ?? index + 1;
    byNumber.set(citationIndex, source);
    const sourceId = toInt(source.source_id) ?? toInt(source.id);
    if (sourceId != null && !byNumber.has(sourceId)) byNumber.set(sourceId, source);
  });

  const rendered = escapeHtml(text).replace(CITATION_GROUP_RE, (group) => {
    const displayNumbers: number[] = [];
    for (const marker of extractMarkers([group])) {
      const source = byNumber.get(marker);
      if (!source) continue;
      const citationIndex = toInt(source.citation_index) ?? normalizedSources.indexOf(source) + 1;
      if (!displayNumbers.includes(citationIndex)) displayNumbers.push(citationIndex);
    }
    return displayNumbers
      .map((displayNumber) => {
        const source = normalizedSources.find((entry, index) => (toInt(entry.citation_index) ?? index + 1) === displayNumber);
        const tooltip = escapeHtml(referenceTooltip(source ?? {}, displayNumber));
        return `<sup class="citation-ref"><a href="#ref-${displayNumber}" class="citation-link" title="${tooltip}" data-citation-tooltip="${tooltip}" aria-label="Reference ${displayNumber}">[${displayNumber}]</a></sup>`;
      })
      .join('');
  });

  return rendered;
}

/**
 * Convert citation markers inside already-rendered, trusted/escaped HTML.
 *
 * Use this after Markdown rendering when the original text has already been
 * escaped by the renderer pipeline. Unlike renderCitations(), this does not
 * escape the full input HTML; it only injects citation links for marker groups.
 */
export function renderCitationMarkersInHtml(
  html: string,
  sources: CitationSource[],
  referencePrefix = 'ref',
): string {
  if (!html) return '';

  const normalizedSources = sources.some((source) => toInt(source.citation_index) != null)
    ? [...sources].sort((a, b) => (toInt(a.citation_index) ?? Number.MAX_SAFE_INTEGER) - (toInt(b.citation_index) ?? Number.MAX_SAFE_INTEGER))
    : normalizeCitationSources(sources, [html]);
  const byNumber = new Map<number, CitationSource>();
  normalizedSources.forEach((source, index) => {
    const citationIndex = toInt(source.citation_index) ?? index + 1;
    byNumber.set(citationIndex, source);
    const sourceId = toInt(source.source_id) ?? toInt(source.id);
    if (sourceId != null && !byNumber.has(sourceId)) byNumber.set(sourceId, source);
  });

  return html.replace(CITATION_GROUP_RE, (group) => {
    const displayNumbers: number[] = [];
    for (const marker of extractMarkers([group])) {
      const source = byNumber.get(marker);
      if (!source) continue;
      const citationIndex = toInt(source.citation_index) ?? normalizedSources.indexOf(source) + 1;
      if (!displayNumbers.includes(citationIndex)) displayNumbers.push(citationIndex);
    }
    if (displayNumbers.length === 0) return group;

    return displayNumbers
      .map((displayNumber) => {
        const source = normalizedSources.find((entry, index) => (toInt(entry.citation_index) ?? index + 1) === displayNumber);
        const tooltip = escapeHtml(referenceTooltip(source ?? {}, displayNumber));
        return `<sup class="citation-ref"><a href="#${referencePrefix}-${displayNumber}" class="citation-link" title="${tooltip}" data-citation-tooltip="${tooltip}" aria-label="Reference ${displayNumber}">[${displayNumber}]</a></sup>`;
      })
      .join('');
  });
}

export function hasCitations(text: string | null | undefined): boolean {
  if (!text) return false;
  return /\[\d+\]/.test(text);
}

export function extractCitationIds(text: string): number[] {
  return Array.from(new Set(extractMarkers([text]))).sort((a, b) => a - b);
}
