import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

type DownloadCatalogue = {
  countries?: Array<Record<string, any>>;
  diseases?: Array<Record<string, any>>;
};

let cachedCatalogue: DownloadCatalogue | null | undefined;
const cacheDuringBuild = process.env.NODE_ENV === 'production';

// Static builds render thousands of pages in one process. The catalogue is
// ~26 MB, so parsing it in each page's frontmatter dominates render time.
export function downloadCatalogue(): DownloadCatalogue | null {
  if (cacheDuringBuild && cachedCatalogue !== undefined) return cachedCatalogue;
  const path = resolve('./src/data/downloads.json');
  const catalogue = existsSync(path)
    ? JSON.parse(readFileSync(path, 'utf8')) as DownloadCatalogue
    : null;
  if (cacheDuringBuild) cachedCatalogue = catalogue;
  return catalogue;
}
