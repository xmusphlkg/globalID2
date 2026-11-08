import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const points: Record<string, number[]> = JSON.parse(readFileSync(new URL('./subdivision-map-points.json', import.meta.url), 'utf8'));
const metadata = JSON.parse(readFileSync(new URL('../data/meta.json', import.meta.url), 'utf8'));

test('US, Brazil and Japan subdivisions with data have valid map coordinates', () => {
  for (const prefix of ['US-', 'BR-', 'JP-']) {
    const rows = metadata.countries.filter((row: { code: string; data_available: boolean }) => row.code.startsWith(prefix) && row.data_available);
    assert.ok(rows.length > 0, `No published subdivisions for ${prefix}`);
    for (const row of rows) {
      const point = points[row.code];
      assert.ok(point, `Missing coordinates for ${row.code}`);
      assert.ok(point.length === 2 && point[0] >= -180 && point[0] <= 180 && point[1] >= -90 && point[1] <= 90, `Invalid coordinates for ${row.code}`);
    }
  }
  assert.ok(points['US-WA'][1] > 45, 'Washington state must not use Washington DC coordinates');
});
