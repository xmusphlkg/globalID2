import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const points: Record<string, number[]> = JSON.parse(readFileSync(new URL('./subdivision-map-points.json', import.meta.url), 'utf8'));
const metadata = JSON.parse(readFileSync(new URL('../data/meta.json', import.meta.url), 'utf8'));

test('US, Brazil and Japan coordinate catalogues cover published subdivisions', () => {
  for (const prefix of ['US-', 'BR-', 'JP-']) {
    const rows = metadata.countries.filter((row: { code: string; data_available: boolean }) => row.code.startsWith(prefix) && row.data_available);
    const catalogueCodes = Object.keys(points).filter(code => code.startsWith(prefix));
    assert.ok(catalogueCodes.length > 0, `No coordinate catalogue for ${prefix}`);
    // A clean CI fixture has no published countries. Validate the complete
    // tracked catalogue there and additionally check any local published rows.
    for (const code of new Set([...catalogueCodes, ...rows.map((row: { code: string }) => row.code)])) {
      const point = points[code];
      assert.ok(point, `Missing coordinates for ${code}`);
      assert.ok(point.length === 2 && point[0] >= -180 && point[0] <= 180 && point[1] >= -90 && point[1] <= 90, `Invalid coordinates for ${code}`);
    }
  }
  assert.ok(points['US-WA'][1] > 45, 'Washington state must not use Washington DC coordinates');
});
