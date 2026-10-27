/** Public, shareable Research Radar filters. Free-text search is deliberately excluded. */
export const RESEARCH_FILTER_QUERY_KEYS = {
  disease: 'd',
  country: 'c',
  study: 's',
  topic: 't',
  pathogenType: 'pt',
  population: 'p',
  journal: 'j',
  publisher: 'pub',
  publishedFrom: 'from',
  publishedTo: 'to',
  openAccess: 'oa',
} as const;

export type ResearchFilterValues = Record<keyof typeof RESEARCH_FILTER_QUERY_KEYS, string | boolean>;

const DATE_VALUE = /^\d{4}-\d{2}-\d{2}$/;

export function readResearchFilterValues(params: URLSearchParams): Partial<ResearchFilterValues> {
  const result: Partial<ResearchFilterValues> = {};
  for (const [field, key] of Object.entries(RESEARCH_FILTER_QUERY_KEYS) as Array<[keyof typeof RESEARCH_FILTER_QUERY_KEYS, string]>) {
    const value = params.get(key);
    if (field === 'openAccess') {
      if (value === '1' || value === 'true') result.openAccess = true;
      continue;
    }
    if (!value) continue;
    if ((field === 'publishedFrom' || field === 'publishedTo') && !DATE_VALUE.test(value)) continue;
    result[field] = value;
  }
  // Keep old shared links working while emitting the shorter canonical `d` key.
  if (!result.disease) {
    const legacyDisease = params.get('disease');
    if (legacyDisease) result.disease = legacyDisease;
  }
  return result;
}

export function writeResearchFilterValues(values: ResearchFilterValues): URLSearchParams {
  // Build from the public filter state only; never copy arbitrary query values
  // (which may contain search terms or unrelated private data) into the URL.
  const next = new URLSearchParams();
  for (const [field, key] of Object.entries(RESEARCH_FILTER_QUERY_KEYS) as Array<[keyof typeof RESEARCH_FILTER_QUERY_KEYS, string]>) {
    const value = values[field];
    if (field === 'openAccess') {
      if (value === true) next.set(key, '1');
    } else if (typeof value === 'string' && value) {
      next.set(key, value);
    }
  }
  return next;
}
