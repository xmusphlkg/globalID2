// src/lib/worldMapNames.ts
// Shared helpers for binding bundled world GeoJSON polygons to ISO 3166-1
// alpha-2 country codes.
//
// The bundled world map (public/data/world.json) only carries a display `name`
// per feature, so every choropleth needs a stable name -> ISO2 lookup. The
// builder below is deliberately dependency free (it takes the "flag-icons"
// catalogue as an argument) so both build-time scripts and client islands can
// share one deterministic implementation.

export interface WorldFeatureLike {
  properties?: { name?: string } | null;
}

export interface CatalogueCountryLike {
  code?: string;
  name?: string;
  iso?: boolean;
}

export function normaliseName(value: string): string {
  return String(value || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/^(the|republic of|state of)\s+/, '');
}

// Spritesheets and open map extracts disagree on a handful of country names.
// Each alias resolves to a normalised canonical name used by the map extract.
export const NAME_ALIASES: Record<string, string[]> = {
  'czechia': ['czech republic'],
  'eswatini': ['swaziland'],
  'north macedonia': ['macedonia'],
  'myanmar': ['burma'],
  'timor leste': ['east timor'],
  'brunei': ['brunei darussalam'],
  'laos': ['lao pdr', 'lao people s democratic republic'],
  'russia': ['russian federation'],
  'south korea': ['korea'],
  'moldova': ['republic of moldova'],
  'vietnam': ['viet nam'],
  'tanzania': ['united republic of tanzania'],
  'bolivia': ['bolivia plurinational state of'],
  'venezuela': ['venezuela bolivarian republic'],
  'iran': ['iran islamic republic'],
  'syria': ['syrian arab republic'],
  'democratic republic of the congo': ['dem rep congo', 'democratic republic of congo'],
  'congo': ['republic of the congo'],
  'palestine': ['palestine west bank and gaza'],
  'united states': ['united states of america'],
  'united kingdom': ['uk'],
};

// Names in this map extract that never match a canonical catalogue spelling.
// Keys are `normaliseName()` outputs of the bundled world.json feature names;
// values are ISO 3166-1 alpha-2 codes.
const EXPLICIT_FEATURE_CODES: Record<string, string> = {
  aland: 'AX',
  'antigua and barb': 'AG',
  'bosnia and herz': 'BA',
  'br indian ocean ter': 'IO',
  'brunei': 'BN',
  'cape verde': 'CV',
  'cayman is': 'KY',
  'central african rep': 'CF',
  'congo': 'CG',
  'czech rep': 'CZ',
  'dem rep congo': 'CD',
  'dem rep korea': 'KP',
  'dominican rep': 'DO',
  'eq guinea': 'GQ',
  'f s antarctic lands': 'TF',
  'faeroe is': 'FO',
  'falkland is': 'FK',
  'fr polynesia': 'PF',
  'heard i and mcdonald is': 'HM',
  'macedonia': 'MK',
  'micronesia': 'FM',
  'n mariana is': 'MP',
  's geo and s sandw is': 'GS',
  's sudan': 'SS',
  'saint helena': 'SH',
  'saint lucia': 'LC',
  'solomon is': 'SB',
  'st pierre and miquelon': 'PM',
  'st vin and gren': 'VC',
  'timor leste': 'TL',
  'turks and caicos is': 'TC',
  'turkey': 'TR',
  'u s virgin is': 'VI',
  'united states': 'US',
  'w sahara': 'EH',
  // N. Cyprus and Siachen Glacier intentionally stay unresolved: neither owns
  // an ISO 3166-1 alpha-2 code, and folding them into Cyprus/India would
  // silently mis-attribute any surveillance record published for them.
};

export interface FeatureIsoResolution {
  /** feature name -> ISO 3166-1 alpha-2 code */
  byFeatureName: Record<string, string>;
  /** feature names that could not be resolved to a country code */
  unresolvedFeatureNames: string[];
}

/**
 * Resolve every GeoJSON feature name to an ISO2 code using the supplied
 * country catalogue (shape compatible with `flag-icons/country.json`).
 */
export function buildFeatureNameToIso(
  features: readonly WorldFeatureLike[],
  catalogue: readonly CatalogueCountryLike[],
): FeatureIsoResolution {
  const canonicalByNormalised = new Map<string, string>();
  const aliasToCanonical = new Map<string, string>();

  for (const entry of catalogue) {
    const code = String(entry.code ?? '').toUpperCase();
    if (!/^[A-Z]{2}$/.test(code)) continue;
    if (entry.iso === false) continue;
    const primary = normaliseName(entry.name ?? '');
    if (!primary) continue;
    if (!canonicalByNormalised.has(primary)) canonicalByNormalised.set(primary, code);
    for (const alias of NAME_ALIASES[primary] ?? []) {
      aliasToCanonical.set(normaliseName(alias), primary);
    }
  }

  const byFeatureName: Record<string, string> = {};
  const unresolvedFeatureNames: string[] = [];

  for (const feature of features) {
    const name = feature?.properties?.name;
    if (typeof name !== 'string' || !name.trim()) continue;
    const normalised = normaliseName(name);

    const explicit = EXPLICIT_FEATURE_CODES[normalised];
    if (explicit) {
      byFeatureName[name] = explicit;
      continue;
    }

    const canonical = normalised;
    const mapped =
      canonicalByNormalised.get(canonical) ??
      (aliasToCanonical.has(canonical)
        ? canonicalByNormalised.get(aliasToCanonical.get(canonical) as string)
        : undefined);

    if (mapped) byFeatureName[name] = mapped;
    else unresolvedFeatureNames.push(name);
  }

  return { byFeatureName, unresolvedFeatureNames };
}
