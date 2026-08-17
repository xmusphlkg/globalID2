import assert from 'node:assert/strict';
import test from 'node:test';
import { renderResearchSocialCardSvg, wrapSocialCardText } from './research-social-card.ts';

test('wraps social-card copy deterministically and caps the line count', () => {
  const lines = wrapSocialCardText('one two three four five six seven eight nine', 14, 2);
  assert.deepEqual(lines, ['one two three', 'four five six…']);
});

test('renders a safe, evidence-labelled share card without raw markup injection', () => {
  const svg = renderResearchSocialCardSvg({
    title: 'Ebola <evidence> & response',
    studyType: 'Commentary',
    diseases: ['Ebola'],
    countries: ['DR Congo'],
    implication: 'Use evidence, not causal inference.',
    doi: '10.1000/example',
  });
  assert.match(svg, /GIDS RESEARCH RADAR/);
  assert.match(svg, /Ebola &lt;evidence&gt; &amp; response/);
  assert.doesNotMatch(svg, /Ebola <evidence>/);
  assert.match(svg, /DOI 10\.1000\/example/);
});
