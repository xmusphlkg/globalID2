import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { addSituationPages } from './situation-build-cache.mjs';
import { weeklyArchive } from '../src/lib/situation-archive.ts';

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
