import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { addSituationPages } from './situation-build-cache.mjs';
import { weeklyArchive } from '../src/lib/situation-archive.ts';
import { prerenderSituationJson } from '../src/lib/situation-build-mode.ts';
import { pageSourceSnapshot } from './page-build-cache.mjs';
import { finalizeResearchSnapshot, planResearchBuild, restoreReusablePages } from './research-build-cache.mjs';

test('history depends on its alerts, labels and neighbors, not unrelated events or export time', () => {
  const root = mkdtempSync(join(tmpdir(), 'situation-pages-'));
  const dir = join(root, 'situation/international');
  const schema_version = 'gids.situation_alerts.v1';
  const write = (path, data) => {
    const file = join(dir, path); mkdirSync(join(file, '..'), { recursive: true });
    writeFileSync(file, JSON.stringify({ schema_version, ...data }));
  };
  const week = { period_key: '2026-W40', new_outbreaks: ['a'], updated_outbreaks: [], new_places: 1, alerts: 1 };
  const other = { ...week, period_key: '2026-W44', new_outbreaks: ['b'] };
  const a = { outbreak_id: 'a', disease_name_en: 'Flu', first_published: '2026-09-28' };
  const b = { ...a, outbreak_id: 'b', disease_name_en: 'Other' };
  const alert = { alert_id: 'a-1', published_date: '2026-09-28', quote: 'one' };
  const snapshot = () => {
    const pages = {};
    addSituationPages({ dataRoot: root,
      hashFile: path => createHash('sha256').update(readFileSync(path)).digest('hex'),
      add: (key, path, dependencies, options) => { pages[key] = JSON.stringify({ path, dependencies, options }); },
    });
    return pages;
  };
  try {
    write('weekly/index.json', { periods: [other, week] });
    write('weekly/2026-W40.json', week); write('weekly/2026-W44.json', other);
    write('map.json', { generated_at: 'one', points: [], outbreaks: [a, b] });
    write('outbreaks/a.json', { alerts: [alert] }); write('outbreaks/b.json', { alerts: [] });
    const first = snapshot();
    assert.equal(weeklyArchive('2026-W40', dir).published, '2026-09-28');
    write('map.json', { generated_at: 'two', points: [], outbreaks: [a, { ...b, disease_name_en: 'Changed elsewhere' }] });
    assert.equal(snapshot()['situation-week:2026-W40'], first['situation-week:2026-W40']);
    write('outbreaks/a.json', { alerts: [alert, { alert_id: 'later', published_date: '2026-11-01' }] });
    assert.equal(snapshot()['situation-week:2026-W40'], first['situation-week:2026-W40']);
    write('outbreaks/a.json', { alerts: [{ ...alert, quote: 'corrected' }] });
    assert.notEqual(snapshot()['situation-week:2026-W40'], first['situation-week:2026-W40']);
    const corrected = snapshot();
    write('weekly/index.json', { periods: [other, { ...week, period_key: '2026-W41' }, week] });
    write('weekly/2026-W41.json', { ...week, period_key: '2026-W41' });
    const neighbor = snapshot();
    assert.notEqual(neighbor['situation-week:2026-W40'], corrected['situation-week:2026-W40']);
    assert.equal(neighbor['situation-month:2026-09'], corrected['situation-month:2026-09']);
    assert.notEqual(neighbor['situation-month:2026-10'], corrected['situation-month:2026-10']);
    assert.match(neighbor['situation-outbreak:a'], /situation\/outbreaks\/a\.json/);
    write('weekly/index.json', { periods: [other] });
    assert.equal(snapshot()['situation-week:2026-W40'], undefined);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('Pages runtime mode finalizes without outbreak JSON and never restores legacy JSON bundles', () => {
  const root = mkdtempSync(join(tmpdir(), 'situation-runtime-cache-'));
  const dataRoot = join(root, 'data'), outputRoot = join(root, 'dist'), pageCache = join(root, 'cached-pages');
  const targetManifest = join(root, 'manifest.json'), sourcePending = join(root, 'pending.json');
  const schema_version = 'gids.situation_alerts.v1';
  const write = (path, data) => {
    const destination = join(dataRoot, path); mkdirSync(join(destination, '..'), { recursive: true });
    writeFileSync(destination, JSON.stringify(data));
  };
  const snapshot = env => pageSourceSnapshot({ dataRoot, shared: 'stable', env, directoryCachePath: join(root, 'directory-cache.json') });
  const finalize = value => finalizeResearchSnapshot(structuredClone(value), { outputRoot, pageCache, targetManifest, sourcePending });
  try {
    write('meta.json', { countries: [] }); write('diseases/index.json', []); write('reports/index.json', []);
    const week = { period_key: '2026-W40', new_outbreaks: ['a', 'b'], updated_outbreaks: [], new_places: 2, alerts: 2 };
    write('situation/international/weekly/index.json', { schema_version, periods: [week] });
    write('situation/international/weekly/2026-W40.json', { schema_version, ...week });
    write('situation/international/map.json', { schema_version, points: [], outbreaks: [
      { outbreak_id: 'a', first_published: '2026-09-28' }, { outbreak_id: 'b', first_published: '2026-09-29' },
    ] });
    for (const id of ['a', 'b']) write(`situation/international/outbreaks/${id}.json`, { schema_version, outbreak_id: id, alerts: [{ alert_id: `${id}-1`, published_date: '2026-09-29' }] });

    const legacy = snapshot({});
    assert.equal(prerenderSituationJson({}), true);
    // A standalone Worker/custom manifest does not change getStaticPaths.
    const standaloneEnv = { GIDS_EXTERNAL_SURVEILLANCE_DATA: '1', PUBLIC_GIDS_SITUATION_MANIFEST_URL: 'https://data.example.test/current.json' };
    assert.equal(prerenderSituationJson(standaloneEnv), true);
    const standalone = snapshot(standaloneEnv);
    for (const id of ['a', 'b']) {
      assert.deepEqual(legacy.articles[`situation-outbreak:${id}`].paths, [`situation/outbreaks/${id}.json`]);
      assert.deepEqual(standalone.articles[`situation-outbreak:${id}`], legacy.articles[`situation-outbreak:${id}`]);
    }
    for (const entry of Object.values(legacy.articles)) for (const path of entry.paths) {
      const destination = join(outputRoot, path); mkdirSync(join(destination, '..'), { recursive: true });
      writeFileSync(destination, path.endsWith('.json')
        ? readFileSync(join(dataRoot, 'situation/international', path.slice('situation/'.length)))
        : `<main>${path}</main>`);
    }
    finalize(legacy);
    const previous = JSON.parse(readFileSync(targetManifest));
    for (const id of ['a', 'b']) {
      assert.equal(existsSync(join(pageCache, `situation/outbreaks/${id}.json`)), true);
      rmSync(join(outputRoot, `situation/outbreaks/${id}.json`));
    }

    const env = { GIDS_PAGES_DATA_WORKER: '1', GIDS_EXTERNAL_SURVEILLANCE_DATA: '1' };
    assert.equal(prerenderSituationJson(env), false);
    const external = snapshot(env);
    assert.notEqual(external.shared, legacy.shared, 'Effective prerender mode belongs to the shared fingerprint');
    assert.deepEqual(Object.keys(external.articles).filter(key => key.startsWith('situation-outbreak:')), []);
    const plan = planResearchBuild({ snapshot: external, previous, incremental: true, outputRoot: pageCache });
    assert.deepEqual(plan.removed.sort(), ['situation-outbreak:a', 'situation-outbreak:b']);
    assert.doesNotThrow(() => finalize(external), 'Missing runtime-owned JSON must not fail finalization');
    for (const id of ['a', 'b']) assert.equal(existsSync(join(pageCache, `situation/outbreaks/${id}.json`)), false);

    const warm = planResearchBuild({ snapshot: snapshot(env), previous: JSON.parse(readFileSync(targetManifest)), incremental: true, outputRoot: pageCache });
    assert.ok(warm.reused.length > 0);
    const restored = join(root, 'restored');
    restoreReusablePages(warm, pageCache, restored);
    assert.equal(existsSync(join(restored, 'situation/weekly/2026-W40/index.html')), true);
    assert.equal(existsSync(join(restored, 'situation/outbreaks/a.json')), false);
    assert.equal(existsSync(join(restored, 'situation/outbreaks/b.json')), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
