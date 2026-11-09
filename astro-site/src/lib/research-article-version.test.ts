import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';

import { researchArticleVersion } from './research-article-version.ts';

test('versions the exact published article input bytes across languages and corrections', () => {
  const input = Buffer.from(JSON.stringify({ article_id: 'article-1', summary: {
    en: { main_findings: 'Findings' }, zh: { main_findings: '研究发现' },
  } }), 'utf8');
  const sameData = Buffer.from(input);
  const changed = Buffer.from(input.toString('utf8').replace('研究发现', '更正结果'), 'utf8');
  assert.equal(researchArticleVersion(input), createHash('sha256').update(input).digest('hex'));
  assert.equal(researchArticleVersion(sameData), researchArticleVersion(input));
  assert.notEqual(researchArticleVersion(changed), researchArticleVersion(input));
});
