import { mainSiteBase } from './papers.ts';
import { createSurveillanceDataClient, surveillanceData } from './surveillance-data.ts';

const pages = createSurveillanceDataClient({ manifestUrl: `${mainSiteBase()}/site-data/subdivision-maps/pages.json` });

export async function mapDataForCountry(code: string, signal: AbortSignal, live = surveillanceData, staticMaps = pages) {
  // This specific data migration carried forward the old CN catalogue and
  // US geometry. All later surveillance releases own their map data again.
  if (['CN', 'US'].includes(code) && (await live.manifest({ signal }))?.release_id === 'data-download-cells-20261119-44486d79') {
    const manifest = await staticMaps.manifest({ signal });
    if (manifest?.files[`subdivision-maps/${code.toLowerCase()}/index.json`]) return staticMaps;
  }
  return live;
}
