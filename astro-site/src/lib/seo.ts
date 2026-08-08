export type JsonLdNode = Record<string, unknown>;

export type BreadcrumbItem = {
  label: string;
  href?: string;
};

type DownloadFile = {
  bytes?: number;
  filename?: string;
  url?: string;
};

type DownloadPart = {
  is_current?: boolean;
  files?: Record<string, DownloadFile | undefined>;
};

type DownloadEntry = {
  site_json_path?: string;
  parts?: DownloadPart[];
};

const DOWNLOAD_MIME_TYPES: Record<string, string> = {
  csv: 'text/csv',
  json: 'application/json',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};

export function normalizeSeoText(value: unknown): string {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
}

function coverageText(start?: string | null, end?: string | null): string {
  if (start && end) return ` from ${start} to ${end}`;
  if (start) return ` since ${start}`;
  if (end) return ` through ${end}`;
  return '';
}

export function buildCountryMetaDescription(input: {
  name: string;
  diseaseCount?: number | null;
  start?: string | null;
  end?: string | null;
}): string {
  const name = normalizeSeoText(input.name) || 'Country';
  const diseaseCount = Math.max(0, Number(input.diseaseCount) || 0);
  const tracked = diseaseCount > 0
    ? `${diseaseCount} tracked infectious diseases`
    : 'tracked infectious diseases';
  return `${name} surveillance data for ${tracked}${coverageText(input.start, input.end)}, with case trends, source metadata, and downloadable datasets.`;
}

export function buildDiseaseMetaDescription(input: {
  name: string;
  category?: string | null;
  countryCount?: number | null;
  start?: string | null;
  end?: string | null;
}): string {
  const name = normalizeSeoText(input.name) || 'Disease';
  const category = normalizeSeoText(input.category);
  const countryCount = Math.max(0, Number(input.countryCount) || 0);
  const categoryText = category ? ` (${category.toLowerCase()})` : '';
  const geography = countryCount > 0
    ? ` across ${countryCount} ${countryCount === 1 ? 'country' : 'countries'}`
    : '';
  return `${name}${categoryText} surveillance data${geography}${coverageText(input.start, input.end)}, with case trends, source metadata, and downloadable records.`;
}

export function buildBreadcrumbStructuredData(input: {
  siteOrigin: string;
  currentPath: string;
  breadcrumbs: BreadcrumbItem[];
}): JsonLdNode | null {
  if (input.breadcrumbs.length === 0) return null;

  const items = [
    { label: 'Home', href: '/' },
    ...input.breadcrumbs,
  ].map((item, index, allItems) => ({
    '@type': 'ListItem',
    position: index + 1,
    name: normalizeSeoText(item.label),
    item: new URL(
      item.href || (index === allItems.length - 1 ? input.currentPath : '/'),
      input.siteOrigin,
    ).toString(),
  }));

  return {
    '@type': 'BreadcrumbList',
    itemListElement: items,
  };
}

export function buildDatasetDistributions(input: {
  entry?: DownloadEntry | null;
  siteOrigin: string;
  fallbackSiteJsonPath?: string | null;
}): JsonLdNode[] {
  const distributions: JsonLdNode[] = [];
  const siteJsonPath = normalizeSeoText(input.entry?.site_json_path)
    || normalizeSeoText(input.fallbackSiteJsonPath);

  if (siteJsonPath) {
    distributions.push({
      '@type': 'DataDownload',
      name: 'GIDS page dataset (JSON)',
      encodingFormat: 'application/json',
      contentUrl: new URL(siteJsonPath, input.siteOrigin).toString(),
    });
  }

  const parts = Array.isArray(input.entry?.parts) ? input.entry.parts : [];
  const currentPart = parts.find(part => part?.is_current) ?? parts[0];
  for (const [format, file] of Object.entries(currentPart?.files ?? {})) {
    const url = normalizeSeoText(file?.url);
    if (!url) continue;
    distributions.push({
      '@type': 'DataDownload',
      name: normalizeSeoText(file?.filename) || `Current dataset (${format.toUpperCase()})`,
      encodingFormat: DOWNLOAD_MIME_TYPES[format] ?? 'application/octet-stream',
      contentUrl: url,
      ...(Number.isFinite(file?.bytes) && Number(file?.bytes) > 0
        ? { contentSize: `${file?.bytes} bytes` }
        : {}),
    });
  }

  return distributions;
}

export function buildDatasetStructuredData(input: {
  id: string;
  url: string;
  name: string;
  description: string;
  siteOrigin: string;
  dateModified?: string | null;
  start?: string | null;
  end?: string | null;
  spatialCoverage?: string | null;
  keywords?: string[];
  distributions?: JsonLdNode[];
  sourceUrls?: string[];
}): JsonLdNode {
  const dateModified = normalizeSeoText(input.dateModified);
  const spatialCoverage = normalizeSeoText(input.spatialCoverage);
  const sourceUrls = [...new Set(
    (input.sourceUrls ?? []).map(normalizeSeoText).filter(Boolean),
  )];

  return {
    '@type': 'Dataset',
    '@id': input.id,
    url: input.url,
    name: normalizeSeoText(input.name),
    description: normalizeSeoText(input.description),
    creator: { '@id': `${input.siteOrigin}#organization` },
    ...(dateModified ? { dateModified } : {}),
    ...(input.start && input.end ? { temporalCoverage: `${input.start}/${input.end}` } : {}),
    ...(spatialCoverage ? {
      spatialCoverage: { '@type': 'Place', name: spatialCoverage },
    } : {}),
    ...(input.keywords?.length ? { keywords: input.keywords } : {}),
    ...(input.distributions?.length ? { distribution: input.distributions } : {}),
    ...(sourceUrls.length ? { isBasedOn: sourceUrls } : {}),
  };
}
