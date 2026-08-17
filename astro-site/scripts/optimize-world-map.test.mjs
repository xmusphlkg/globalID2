import assert from 'node:assert/strict';
import test from 'node:test';
import { compactWorldMap } from './optimize-world-map.mjs';

test('rounds display coordinates without changing feature metadata or topology shape', () => {
  const source = {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: { name: 'Example', population: 123.456789 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[
          12.123456789,
          -34.987654321,
        ], [
          13.999999999,
          -33.111111111,
        ]]],
      },
    }],
  };

  const compact = compactWorldMap(source, 4);
  assert.deepEqual(compact.features[0].geometry.coordinates, [[[
    12.1235,
    -34.9877,
  ], [
    14,
    -33.1111,
  ]]]);
  assert.deepEqual(compact.features[0].properties, source.features[0].properties);
  assert.deepEqual(source.features[0].geometry.coordinates[0][0], [12.123456789, -34.987654321]);
});

test('rejects data that is not a GeoJSON feature collection', () => {
  assert.throws(() => compactWorldMap({ type: 'Feature' }), /FeatureCollection/);
});
