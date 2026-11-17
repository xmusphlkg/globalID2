/** Read-only benchmark of actual local map data; no network, DB or rendering.
 * node --experimental-strip-types scripts/benchmark-subdivision-map.mjs
 * Use --model /tmp/model-before.ts to compare an earlier implementation.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath, pathToFileURL } from 'node:url';

const modelArgument = process.argv.indexOf('--model');
const modelPath = modelArgument < 0
  ? new URL('../src/components/charts/subdivision-map/model.ts', import.meta.url)
  : pathToFileURL(resolve(process.argv[modelArgument + 1]));
const model = await import(modelPath.href);
const directory = new URL('../public/site-data/subdivision-maps/us/', import.meta.url);
const bodies = Object.fromEntries(['index', 'population', 'd094'].map(name => [name, readFileSync(new URL(`${name}.json`, directory))]));
const sha256 = value => createHash('sha256').update(value).digest('hex');
const measure = action => {
  const start = performance.now(), value = action();
  return { value, ms: Number((performance.now() - start).toFixed(3)) };
};
const runs = [];
for (let run = 0; run < 3; run++) {
  // Fresh identities make each cold run include index creation/freezing. File
  // I/O and JSON parsing are outside both implementations' measured work.
  const catalog = JSON.parse(bodies.index), populations = JSON.parse(bodies.population), data = JSON.parse(bodies.d094);
  const window = { from: '2025-01-01', to: '2025-12-31', metric: 'rate', view: 'monthly' };
  const cold = measure(() => {
    model.prepareMapDataset?.(data);
    return model.sharedCeiling(catalog, data, populations, window);
  });
  const hot = measure(() => model.sharedCeiling(catalog, data, populations, window));
  const view = measure(() => model.sharedCeiling(catalog, data, populations, { ...window, view: 'annual' }));
  const changedRange = { ...window, from: '2025-01-01', to: '2025-03-31' };
  const changed = measure(() => model.sharedCeiling(catalog, data, populations, changedRange));
  const defaults = measure(() => model.defaultWindow(catalog, data, populations));
  const cases = model.resultsFor(catalog, data, populations, window);
  runs.push({ cold_shared_ceiling_ms: cold.ms, hot_shared_ceiling_ms: hot.ms,
    changed_view_ms: view.ms, changed_dates_ms: changed.ms, default_window_ms: defaults.ms,
    ceiling: cold.value, changed_ceiling: changed.value, default_window: defaults.value,
    result_sha256: sha256(JSON.stringify(cases)),
    changed_result_sha256: sha256(JSON.stringify(model.resultsFor(catalog, data, populations, changedRange))) });
}
const timings = Object.keys(runs[0]).filter(key => key.endsWith('_ms'));
console.log(JSON.stringify({ model: fileURLToPath(modelPath), input_sha256: Object.fromEntries(Object.entries(bodies).map(([key, value]) => [key, sha256(value)])),
  samples: runs, median_ms: Object.fromEntries(timings.map(key => [key, runs.map(run => run[key]).sort((a, b) => a - b)[1]])) }, null, 2));
