export type PathogenResourceLocale = 'en' | 'zh' | 'fr';

export interface PathogenResourceProfile {
  /** Curated disease-to-pathogen mapping, independent of disease display names. */
  diseaseId: string;
  pathogen: string;
  taxonomyId: number;
  taxonomyUrl: string;
  sequenceUrl: string;
  gisaidUrl?: string;
}

// Pilot mappings checked against the disease ontology and NCBI Taxonomy.
// Taxonomy names/IDs verified through NCBI EFetch on 2026-11-15. Keep subtype
// concepts narrow: human H5N1 must not open a search for all influenza viruses.
const profiles: Readonly<Record<string, { pathogen: string; taxonomyId: number; gisaid?: boolean }>> = {
  D001: { pathogen: 'Yersinia pestis', taxonomyId: 632 },
  D004: { pathogen: 'SARS-CoV-2', taxonomyId: 2697049, gisaid: true },
  D014: { pathogen: 'Influenza A(H5N1)', taxonomyId: 102793, gisaid: true },
  D017: { pathogen: 'Measles morbillivirus', taxonomyId: 11234 },
  D021: { pathogen: 'Dengue virus', taxonomyId: 12637 },
  D028: { pathogen: 'Bordetella pertussis', taxonomyId: 520 },
};

export const IPSN_RESOURCE_URL = 'https://www.who.int/initiatives/international-pathogen-surveillance-network';

export function pathogenResourceProfile(diseaseId: string): PathogenResourceProfile | null {
  const canonicalId = diseaseId.trim().toUpperCase();
  const profile = Object.hasOwn(profiles, canonicalId) ? profiles[canonicalId] : undefined;
  if (!profile) return null;
  const query = new URLSearchParams({ term: `txid${profile.taxonomyId}[Organism:exp]` });
  return {
    diseaseId: canonicalId,
    pathogen: profile.pathogen,
    taxonomyId: profile.taxonomyId,
    taxonomyUrl: `https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=${profile.taxonomyId}`,
    sequenceUrl: `https://www.ncbi.nlm.nih.gov/nuccore/?${query}`,
    ...(profile.gisaid ? { gisaidUrl: 'https://gisaid.org/' } : {}),
  };
}

export const pathogenResourceLabels = {
  en: {
    title: 'Pathogen sequences & resources',
    pathogen: 'Pathogen',
    sequences: 'Search NCBI Nucleotide / GenBank',
    sequencesDescription: 'Browse nucleotide records for the selected pathogen and refine the search at NCBI.',
    taxonomy: 'NCBI Taxonomy',
    gisaid: 'Open GISAID',
    account: 'Account required',
    ipsn: 'WHO IPSN resources',
    ipsnDescription: 'Explore the pathogen genomic surveillance network and its public resources.',
  },
  zh: {
    title: '病原体序列与资源',
    pathogen: '病原体',
    sequences: '检索 NCBI Nucleotide / GenBank',
    sequencesDescription: '查看所选病原体的核酸记录，并在 NCBI 继续调整检索条件。',
    taxonomy: 'NCBI 分类信息',
    gisaid: '前往 GISAID',
    account: '需要账户',
    ipsn: 'WHO IPSN 资源',
    ipsnDescription: '了解病原体基因组监测合作网络及其公开资源。',
  },
  fr: {
    title: 'Séquences et ressources sur le pathogène',
    pathogen: 'Agent pathogène',
    sequences: 'Rechercher dans NCBI Nucleotide / GenBank',
    sequencesDescription: 'Consultez les séquences nucléotidiques du pathogène et affinez la recherche sur NCBI.',
    taxonomy: 'Taxonomie NCBI',
    gisaid: 'Ouvrir GISAID',
    account: 'Compte requis',
    ipsn: 'Ressources IPSN de l’OMS',
    ipsnDescription: 'Découvrez le réseau de surveillance génomique des pathogènes et ses ressources publiques.',
  },
} satisfies Record<PathogenResourceLocale, Record<string, string>>;
