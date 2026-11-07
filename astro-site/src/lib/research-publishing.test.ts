import assert from 'node:assert/strict';
import test from 'node:test';
import { researchSocialCardsEnabled, researchSocialImage } from './research-publishing.ts';

test('article-specific cards are disabled and share the main-site default image', () => {
  assert.equal(researchSocialCardsEnabled, false);
  assert.equal(researchSocialImage('paper'), 'https://globalinfectiousdisease.com/og-default.png');
});
