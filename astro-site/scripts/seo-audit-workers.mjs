// Parse independent HTML files on multiple cores; merge catalogue-wide checks
// before declaring the audit successful. Workers only read the frozen dist.
import { availableParallelism } from 'node:os';
import { readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { auditSeoOutput, isIndependentResearchRoute } from './check-seo-output.mjs';

function htmlFiles(root, dist = root) {
  return readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const path = join(root, entry.name);
    if (process.env.GIDS_RESEARCH_PUBLICATION_ENABLED === '1' && isIndependentResearchRoute('/' + relative(dist, path).replaceAll('\\', '/'))) return [];
    return entry.isDirectory() ? htmlFiles(path, dist) : entry.name.endsWith('.html') ? [path] : [];
  });
}

export async function auditSeoParallel(directory, { workers = Number(process.env.GIDS_BUILD_SEO_WORKERS ?? Math.min(8, availableParallelism())) } = {}) {
  if (!Number.isInteger(workers) || workers < 1 || workers > 16) throw new Error('GIDS_BUILD_SEO_WORKERS must be 1–16');
  const dist = resolve(directory);
  const files = htmlFiles(dist);
  if (workers === 1 || files.length < 2) return auditSeoOutput(dist);
  const count = Math.min(workers, files.length);
  const threads = [];
  try {
    const results = await Promise.all(Array.from({ length: count }, (_, index) => new Promise((done, reject) => {
      const thread = new Worker(new URL(import.meta.url), {
        workerData: { dist, files: files.slice(Math.floor(index * files.length / count), Math.floor((index + 1) * files.length / count)) },
      });
      threads.push(thread);
      let received = false;
      thread.once('message', value => { received = true; done(value); });
      thread.once('error', reject);
      thread.once('exit', code => { if (code !== 0 || !received) reject(new Error(`SEO worker exited before completing (${code})`)); });
    })));
    const catalogue = { errors: [], pages: [], indexable: 0, noindex: 0 };
    const titles = new Map();
    const canonicals = new Map();
    for (const result of results) {
      catalogue.errors.push(...result.errors);
      catalogue.pages.push(...result.pages);
      catalogue.indexable += result.indexable;
      catalogue.noindex += result.noindex;
      for (const [key, route] of result.titles) {
        if (titles.has(key)) catalogue.errors.push(`${route}: duplicate indexable title also used by ${titles.get(key)}`);
        else titles.set(key, route);
      }
      for (const [key, route] of result.canonicals) {
        if (canonicals.has(key)) catalogue.errors.push(`${route}: duplicate canonical also used by ${canonicals.get(key)}`);
        else canonicals.set(key, route);
      }
    }
    // Sitemap membership, noindex, and redirect destinations need the complete
    // catalogue, including pages parsed by other workers.
    return auditSeoOutput(dist, { files: [], catalogue });
  } finally {
    await Promise.allSettled(threads.map(thread => thread.terminate()));
  }
}

if (!isMainThread) parentPort.postMessage(auditSeoOutput(workerData.dist, { files: workerData.files, partial: true }));

else if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  const result = await auditSeoParallel(resolve(import.meta.dirname, '..', 'dist'));
  process.stdout.write(`[seo-output] ${result.passed ? 'PASS' : 'FAIL'} ${JSON.stringify(result)}\n`);
  if (!result.passed) process.exitCode = 1;
}
