import assert from 'node:assert/strict';
import test from 'node:test';

import { answerResearchQuestion, rankResearchArticles, researchQueryTerms } from './research-ask.ts';

const articles = [
  {
    article_id: 'a1',
    slug: 'pertussis-japan',
    title: 'Pertussis resurgence and vaccine waning in Japan',
    doi: '10.1000/pertussis',
    published_at: '2026-08-01T00:00:00Z',
    discovery_score: 0.9,
    peer_review_status: 'peer_reviewed',
    study_type: 'Cohort study',
    diseases: [{ disease_id: 'D025', name_en: 'Pertussis', name_zh: '百日咳', confidence: 0.92 }],
    countries: [{ code: 'JP', name_en: 'Japan', name_zh: '日本', confidence: 0.9 }],
    topics: [{ name: 'Vaccination', confidence: 0.8 }, { name: 'Surveillance', confidence: 0.8 }],
    summary: { en: { main_findings: 'The authors report age-specific evidence consistent with waning immunity.' } },
    related_signals: [{ signal_id: 's1', disease_name_en: 'Pertussis', visibility: 'public', relation_level: 'exact_disease_geography' }],
  },
  {
    article_id: 'a2',
    slug: 'dengue-brazil',
    title: 'Dengue diagnostics in Brazil',
    published_at: '2026-08-10T00:00:00Z',
    discovery_score: 0.95,
    peer_review_status: 'peer_reviewed',
    study_type: 'Cross-sectional study',
    diseases: [{ disease_id: 'D021', name_en: 'Dengue', name_zh: '登革热', confidence: 0.9 }],
    countries: [{ code: 'BR', name_en: 'Brazil', name_zh: '巴西', confidence: 0.9 }],
    topics: [{ name: 'Diagnostics', confidence: 0.8 }],
  },
];

test('query terms remove generic question language and retain disease terms', () => {
  assert.deepEqual(researchQueryTerms('What are the latest explanations for pertussis resurgence?'), ['explanations', 'pertussis', 'resurgence']);
  assert.ok(researchQueryTerms('日本百日咳近期证据').some((term) => term.includes('百日咳')));
});

test('ranking prioritizes disease, geography, and title matches over recency alone', () => {
  const ranked = rankResearchArticles('pertussis in Japan', articles, { now: new Date('2026-08-17T00:00:00Z') });
  assert.equal(ranked.length, 1);
  assert.equal(ranked[0].article.article_id, 'a1');
  assert.equal(ranked[0].evidenceLevel, 'exact');
  assert.deepEqual(ranked[0].matchReasons.map((reason) => reason.kind), ['disease', 'country']);
  assert.ok(ranked[0].findingEn?.includes('waning immunity'));
});

test('English aliases expand into structured disease, country, and topic matches', () => {
  const ranked = rankResearchArticles('whooping cough vaccine evidence in JP', articles, {
    now: new Date('2026-08-17T00:00:00Z'),
  });
  assert.equal(ranked[0].article.article_id, 'a1');
  assert.equal(ranked[0].evidenceLevel, 'exact');
  assert.deepEqual(ranked[0].matchReasons.map((reason) => reason.kind), ['disease', 'country', 'topic']);
  assert.ok(ranked[0].matchReasons.some((reason) => (
    reason.kind === 'disease' && reason.queryTerm === 'Pertussis' && reason.matchedAlias === 'pertussis'
  )));
});

test('Chinese aliases use bilingual summaries and expose numbered source citations', () => {
  const bilingualArticles = articles.map((article) => article.article_id === 'a1'
    ? {
        ...article,
        summary: {
          ...article.summary,
          zh: { main_findings: '作者报告了与免疫力随时间减弱一致的年龄别证据。' },
        },
      }
    : article);
  const answer = answerResearchQuestion('日本百日咳疫苗证据', bilingualArticles, {
    now: new Date('2026-08-17T00:00:00Z'),
  });
  assert.equal(answer.exactEvidence[0].article.article_id, 'a1');
  assert.match(answer.exactEvidence[0].findingZh ?? '', /免疫力/);
  assert.equal(answer.exactEvidence[0].citation.marker, '[1]');
  assert.equal(answer.citations[0].sourceUrl, 'https://doi.org/10.1000/pertussis');
  assert.match(answer.summaryZh, /\[1\]/);
});

test('title, tags, and bilingual summaries are searchable with deterministic field weights', () => {
  const weightedArticles = [
    { article_id: 'summary-en', title: 'English summary match', summary: { en: { main_findings: 'Waning is discussed.' } } },
    { article_id: 'tag', title: 'Tagged match', tags: ['waning'] },
    { article_id: 'title', title: 'Waning immunity analysis' },
  ];
  const ranked = rankResearchArticles('waning', weightedArticles, { now: new Date('2026-08-17T00:00:00Z') });
  assert.deepEqual(ranked.map((item) => item.article.article_id), ['title', 'tag', 'summary-en']);
  assert.ok(ranked[0].matchReasons[0].points > ranked[1].matchReasons[0].points);
  assert.ok(ranked[1].matchReasons[0].points > ranked[2].matchReasons[0].points);

  const chineseSummary = rankResearchArticles('免疫减弱', [{
    article_id: 'summary-zh',
    title: 'Bilingual abstract record',
    summary: { zh: { main_findings: '随访显示免疫减弱。' } },
  }]);
  assert.equal(chineseSummary[0].article.article_id, 'summary-zh');
  assert.deepEqual(chineseSummary[0].matchReasons[0].fields, ['summaryZh']);
});

test('answers expose only public surveillance links and auditable themes', () => {
  const answer = answerResearchQuestion('pertussis resurgence', articles, { now: new Date('2026-08-17T00:00:00Z') });
  assert.equal(answer.evidence[0].article.doi, '10.1000/pertussis');
  assert.deepEqual(answer.signals.map((item) => item.signal_id), ['s1']);
  assert.deepEqual(answer.themes.map((item) => item.name), ['Surveillance', 'Vaccination']);
  assert.match(answer.limitationsEn, /not a causal synthesis/);
  assert.match(answer.limitationsEn, /risk assessment/);
});

test('unmatched questions return an explicit empty answer', () => {
  const answer = answerResearchQuestion('unrelated astronomy', articles);
  assert.equal(answer.evidence.length, 0);
  assert.equal(answer.exactEvidence.length, 0);
  assert.equal(answer.backgroundEvidence.length, 0);
  assert.equal(answer.citations.length, 0);
  assert.deepEqual(answer.gaps.map((gap) => gap.kind), ['no_relevant_records']);
  assert.match(answer.summaryEn, /No sufficiently relevant/);
});

test('same-name ambiguity requires the requested entity type for exact evidence', () => {
  const ambiguousArticles = [
    {
      article_id: 'country-guinea',
      title: 'National surveillance report',
      published_at: '2026-01-01T00:00:00Z',
      countries: [{ code: 'GN', name_en: 'Guinea', name_zh: '几内亚', confidence: 0.95 }],
      topics: [{ name: 'Surveillance', confidence: 0.95 }],
    },
    {
      article_id: 'guinea-worm',
      title: 'Guinea worm surveillance update',
      published_at: '2026-08-01T00:00:00Z',
      diseases: [{ name_en: 'Guinea worm disease', confidence: 0.95 }],
      topics: [{ name: 'Surveillance', confidence: 0.95 }],
    },
  ];
  const ranked = rankResearchArticles('Guinea surveillance', ambiguousArticles, {
    now: new Date('2026-08-17T00:00:00Z'),
  });
  assert.deepEqual(ranked.map((item) => item.article.article_id), ['country-guinea', 'guinea-worm']);
  assert.deepEqual(ranked.map((item) => item.evidenceLevel), ['exact', 'background']);
  assert.ok(ranked[1].matchReasons.some((reason) => reason.queryTerm === 'Guinea' && reason.fields.includes('title')));
  assert.ok(!ranked[1].matchReasons.some((reason) => reason.queryTerm === 'Guinea' && reason.fields.includes('countries')));
});

test('ranking is stable, field-weighted, and de-duplicates normalized DOI variants', () => {
  const candidates = [
    {
      article_id: 'title-match',
      title: 'Waning immunity after vaccination',
      doi: 'https://doi.org/10.1000/DUPLICATE',
      published_at: '2025-01-01T00:00:00Z',
    },
    {
      article_id: 'summary-match',
      title: 'Immunity update',
      doi: 'doi:10.1000/duplicate',
      published_at: '2026-08-01T00:00:00Z',
      summary: { en: { main_findings: 'The analysis discusses waning immunity.' } },
    },
    {
      article_id: 'second-title-match',
      title: 'A waning pattern in antibody response',
      published_at: '2025-01-01T00:00:00Z',
    },
  ];
  const forward = rankResearchArticles('waning', candidates, { now: new Date('2026-08-17T00:00:00Z') });
  const reversed = rankResearchArticles('waning', [...candidates].reverse(), { now: new Date('2026-08-17T00:00:00Z') });
  assert.deepEqual(forward.map((item) => item.article.article_id), ['second-title-match', 'title-match']);
  assert.deepEqual(reversed.map((item) => item.article.article_id), forward.map((item) => item.article.article_id));
  assert.equal(forward.filter((item) => item.citation.doi === '10.1000/duplicate').length, 1);
  assert.equal(forward.find((item) => item.citation.doi === '10.1000/duplicate')?.article.article_id, 'title-match');
});
