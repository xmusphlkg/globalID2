import assert from 'node:assert/strict';
import test from 'node:test';

import { renderDefaultSocialCardSvg } from './site-social-card.ts';

test('renders a descriptive 1200 by 630 default social card', () => {
  const svg = renderDefaultSocialCardSvg();
  assert.match(svg, /width="1200" height="630"/);
  assert.match(svg, /Global Infectious Disease/);
  assert.match(svg, /Official-source public health data/);
  assert.doesNotMatch(svg, /<script/i);
});
