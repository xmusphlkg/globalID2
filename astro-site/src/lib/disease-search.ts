/** Shared, human-facing names used by every public disease search entry point. */
export type DiseaseSearchIdentity = {
  id?: string; disease_id?: string; slug?: string;
  en?: string; zh?: string; fr?: string;
  name_en?: string; name_zh?: string; name_fr?: string;
  aliases?: string[];
};

const COMMON_ALIASES: Record<string, string[]> = {
  influenza: ['流感', '季节性流感', '流行性感冒', 'flu', 'seasonal influenza', 'grippe'],
  'covid-19': ['新冠', '新冠肺炎', '新型冠状病毒感染', 'COVID', 'COVID-19', 'SARS-CoV-2'],
  tuberculosis: ['结核', '结核病', '肺结核', 'TB', 'tuberculosis', 'tuberculose'],
  pertussis: ['百日咳', 'whooping cough', 'pertussis', 'coqueluche'],
  dengue: ['登革热', 'dengue fever', 'dengue'],
  mpox: ['猴痘', 'mpox', 'monkeypox', 'variole du singe'],
  measles: ['麻疹', 'measles', 'rougeole'],
  malaria: ['疟疾', 'malaria', 'paludisme'],
  'hepatitis-a': ['甲肝', '甲型肝炎', 'hepatitis A', 'hépatite A'],
  'hepatitis-b': ['乙肝', '乙型肝炎', 'hepatitis B', 'hépatite B'],
  'hepatitis-c': ['丙肝', '丙型肝炎', 'hepatitis C', 'hépatite C'],
};

export const normalizeSearchText = (value: unknown): string => String(value ?? '')
  .normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();

export function diseaseSearchAliases(identity: DiseaseSearchIdentity): string[] {
  const names = [identity.en, identity.zh, identity.fr, identity.name_en, identity.name_zh, identity.name_fr];
  // Only the disease's exact identity receives its aliases; H. influenzae must
  // never inherit the identity of seasonal influenza merely through its name.
  const slug = normalizeSearchText(identity.slug || '');
  const canonical = slug || Object.keys(COMMON_ALIASES).find(key => normalizeSearchText(identity.en ?? identity.name_en) === key);
  return [...new Set([
    identity.id, identity.disease_id, identity.slug, ...names,
    ...(identity.aliases ?? []), ...(COMMON_ALIASES[canonical ?? ''] ?? []),
  ].filter((value): value is string => typeof value === 'string' && value.trim().length > 0).map(value => value.trim()))];
}

export function matchesDiseaseSearch(identity: DiseaseSearchIdentity, query: string): boolean {
  const normalized = normalizeSearchText(query);
  if (!normalized) return true;
  return diseaseSearchAliases(identity).some(alias => normalizeSearchText(alias).includes(normalized));
}
