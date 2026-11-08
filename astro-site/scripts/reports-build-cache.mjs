import { createHash } from 'node:crypto';
import { pageSourceSnapshot } from './page-build-cache.mjs';
import { fileHash } from './file-fingerprints.mjs';

// Share the main site's dependency rules, but keep independent output/cache
// directories: an isolated build must never replace the main site's bundles.
export function reportsSourceSnapshot(snapshot = pageSourceSnapshot()) {
  const articles = Object.fromEntries(Object.entries(snapshot.articles).filter(([key]) => (
    key.startsWith('report:') || key.startsWith('report-archive:') || key.startsWith('report-disease:')
  )));
  const shared = createHash('sha256').update([
    snapshot.shared,
    fileHash(new URL('./build-reports.mjs', import.meta.url)),
    fileHash(new URL(import.meta.url)),
  ].join(':')).digest('hex');
  return { schema: 1, shared, articles };
}
