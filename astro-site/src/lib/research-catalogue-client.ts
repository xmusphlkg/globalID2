type CatalogueScope = 'disease' | 'country' | 'topic' | 'week';
type CatalogueChunk = { path: string; count: number; facets: Record<CatalogueScope, string[]> };
type CatalogueManifest = { chunks: CatalogueChunk[] };
type CatalogueRequest = { limit?: number; scope?: CatalogueScope; value?: string };

let manifestPromise: Promise<CatalogueManifest> | null = null;
const chunkPromises = new Map<string, Promise<any[]>>();

export async function loadResearchCatalogue({ limit, scope, value }: CatalogueRequest = {}): Promise<any[]> {
  manifestPromise ??= fetch('/research/catalogue/manifest.json', { cache: 'no-store' })
    .then(response => {
      if (!response.ok) throw new Error(`Research catalogue manifest failed: ${response.status}`);
      return response.json() as Promise<CatalogueManifest>;
    });
  const manifest = await manifestPromise;
  if (!Array.isArray(manifest.chunks)) throw new Error('Invalid research catalogue manifest');
  let selected = manifest.chunks.filter(chunk => !scope || !value || chunk.facets?.[scope]?.includes(value.toLowerCase()));
  if (limit !== undefined) {
    let count = 0;
    selected = selected.filter(chunk => {
      if (count >= limit) return false;
      count += chunk.count;
      return true;
    });
  }
  const chunks = await Promise.all(selected.map(chunk => {
    let promise = chunkPromises.get(chunk.path);
    if (!promise) {
      promise = fetch(chunk.path).then(async response => {
        if (!response.ok) throw new Error(`Research catalogue chunk failed: ${response.status}`);
        const payload = await response.json();
        if (!Array.isArray(payload.articles) || payload.articles.length !== chunk.count) throw new Error('Incomplete research catalogue chunk');
        return payload.articles as any[];
      });
      chunkPromises.set(chunk.path, promise);
    }
    return promise;
  }));
  return chunks.flat();
}
