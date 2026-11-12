import { researchCatalogue } from './research-catalogue-shards';
import { memoizeForBuild } from './build-memo';
import { statSync } from 'node:fs';
import { resolve } from 'node:path';
import { diseaseLiteratureBundleData, projectDiseaseLiterature } from './research-literature-projection';

const buildDiseaseLiteratureCatalogue = memoizeForBuild(() => {
  const { researchCatalogueArticles, researchCatalogueGeneratedAt, researchCatalogueReviewedPreprints } = researchCatalogue();
  const { byDisease, diseaseFacets } = projectDiseaseLiterature([...researchCatalogueArticles, ...researchCatalogueReviewedPreprints]);
  return { byDisease, diseaseFacets, generatedAt: researchCatalogueGeneratedAt };
});

// The source can exceed the raw-reader cache budget. Cache only this compact
// derived index in development and invalidate it whenever the source changes.
let developmentCache: { signature: string; catalogue: ReturnType<typeof buildDiseaseLiteratureCatalogue> } | undefined;
export function diseaseLiteratureCatalogue() {
  if (process.env.GIDS_STATIC_RENDER === '1') return buildDiseaseLiteratureCatalogue();
  const stat = statSync(resolve(process.env.GIDS_SITE_DATA_ROOT || './src/data', 'research/index.json'), { bigint: true });
  const signature = [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].join(':');
  if (!developmentCache || developmentCache.signature !== signature) developmentCache = { signature, catalogue: buildDiseaseLiteratureCatalogue() };
  return developmentCache.catalogue;
}

const bundles = new WeakMap<object, Map<string, ReturnType<typeof diseaseLiteratureBundleData>>>();

export function diseaseLiteratureBundle(diseaseId: string) {
  const catalogue = diseaseLiteratureCatalogue();
  let cached = bundles.get(catalogue);
  if (!cached) { cached = new Map(); bundles.set(catalogue, cached); }
  const id = diseaseId.toLowerCase();
  if (!cached.has(id)) cached.set(id, diseaseLiteratureBundleData(id, catalogue.byDisease.get(id) ?? []));
  return cached.get(id)!;
}
