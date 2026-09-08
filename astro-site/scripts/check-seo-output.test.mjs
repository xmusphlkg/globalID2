import assert from 'node:assert/strict';
import test from 'node:test';

import { tags } from './check-seo-output.mjs';

test('parses greater-than signs inside quoted meta content', () => {
  const html = '<meta name="description" content="Resistance exceeded 10%, including >5% in one group.">';

  const [description] = tags(html, 'meta');

  assert.equal(description.name, 'description');
  assert.equal(description.content, 'Resistance exceeded 10%, including >5% in one group.');
});
