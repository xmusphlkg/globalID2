import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { buildResearchEvidenceManifest } from './research-evidence-manifest.ts';

const summary = {
  research_question: 'Question', study_design: 'Design', population_setting: 'Setting',
  main_findings: ' Main finding ', public_health_relevance: 'Relevance', limitations: 'Limits',
  gids_interpretation: 'Interpretation', provenance: { secret: 'must not be copied' },
};
const article = {
  article_id: 'article-1', slug: 'article-one', editorial_status: 'published', integrity_status: 'current',
  peer_review_status: 'peer_reviewed', content_tier: 'quality_gated_bilingual_evidence', indexable: true,
  updated_at: '2026-11-09T00:00:00Z', summary: { en: summary, zh: { ...summary, main_findings: '发现' } },
};

test('exports public state and stable summary hashes without copying evidence text or provenance', () => {
  const input = { last_updated: '2026-11-09T00:00:00Z', articles: [article], integrity_alerts: [{
    alert_id: 'event-1', article_id: 'article-2', event_type: 'retraction', current_status: 'retracted',
    is_currently_public: false, internal_reviewer: 'private',
  }] };
  const result = buildResearchEvidenceManifest(input) as any;
  const output = result.articles[0];
  assert.equal(result.schema_version, 'gids.research-evidence.v1');
  assert.equal(output.article_id, 'article-1');
  assert.equal(output.editorial_status, 'published');
  assert.equal(output.integrity_status, 'current');
  assert.equal(output.detail_available, true);
  assert.equal(output.summaries.en.main_findings_sha256, createHash('sha256').update('Main finding').digest('hex'));
  const fields = ['Question', 'Design', 'Setting', 'Main finding', 'Relevance', 'Limits', 'Interpretation'];
  assert.equal(output.summaries.en.summary_sha256, createHash('sha256').update(JSON.stringify(fields)).digest('hex'));
  assert.equal(result.integrity_events[0].current_status, 'retracted');
  assert.doesNotMatch(JSON.stringify(result), /must not be copied|internal_reviewer|Main finding/);
  const changed = buildResearchEvidenceManifest({ ...input, articles: [{ ...article, summary: {
    ...article.summary, en: { ...summary, main_findings: 'Changed finding' },
  } }] }) as any;
  assert.notEqual(changed.articles[0].summaries.en.summary_sha256, output.summaries.en.summary_sha256);
  assert.equal(changed.articles[0].summaries.zh.summary_sha256, output.summaries.zh.summary_sha256);
});

test('represents missing or bibliographic evidence conservatively and rejects ambiguous identities', () => {
  const bibliography = { article_id: 'biblio', slug: 'biblio', content_tier: 'bibliographic', indexable: false };
  const result = buildResearchEvidenceManifest({ articles: [{ ...article, summary: { en: summary } }],
    bibliographic_articles: [bibliography], integrity_alerts: [] }) as any;
  assert.equal(result.articles.find((item: any) => item.article_id === 'article-1').summaries.zh, null);
  assert.equal(result.articles.find((item: any) => item.article_id === 'biblio').detail_available, false);
  assert.throws(() => buildResearchEvidenceManifest({ articles: [article, article] }), /identity_invalid/);
  assert.throws(() => buildResearchEvidenceManifest({ articles: [], integrity_alerts: [{ alert_id: 'only-id' }] }), /integrity_invalid/);
});

test('exposes a current correction as public revision evidence', () => {
  const corrected = { ...article, integrity_status: 'corrected' };
  const result = buildResearchEvidenceManifest({ articles: [corrected], integrity_alerts: [{
    alert_id: 'correction-1', article_id: 'article-1', event_type: 'correction', current_status: 'corrected',
    effective_at: '2026-11-08T00:00:00Z', is_currently_public: true,
  }] }) as any;
  assert.equal(result.articles[0].integrity_status, 'corrected');
  assert.deepEqual(result.integrity_events[0], { alert_id: 'correction-1', article_id: 'article-1',
    event_type: 'correction', current_status: 'corrected', effective_at: '2026-11-08T00:00:00Z',
    recorded_at: null, is_currently_public: true });
});

test('weekly inventory binds every listed public brief and cannot infer an empty scan from a missing list', () => {
  const brief = { week: '2026-W45', brief_status: 'ai_reviewed', cited_findings: [{ article_id: 'article-1' }],
    methodology: { en: 'Source', zh: '来源' }, byline: { ai_review: { verdict: 'pass' } }, articles: [article] };
  const { articles: _articles, ...projection } = brief;
  const bytes = JSON.stringify(brief);
  const result = buildResearchEvidenceManifest({ articles: [article], weekly_briefs: [projection] }, () => bytes, () => ['2026-W45']) as any;
  assert.equal(result.weekly_inventory_complete, true);
  assert.deepEqual(result.weekly_briefs, [{ week: '2026-W45', brief_status: 'ai_reviewed',
    brief_sha256: createHash('sha256').update(bytes).digest('hex'), cited_article_ids: ['article-1'] }]);
  assert.equal((buildResearchEvidenceManifest({ articles: [] }, () => bytes, () => []) as any).weekly_inventory_complete, false);
  assert.equal((buildResearchEvidenceManifest({ articles: [], weekly_briefs: [] }, () => bytes, () => []) as any).weekly_inventory_complete, true);
  assert.throws(() => buildResearchEvidenceManifest({ articles: [], weekly_briefs: [projection] }, () =>
    JSON.stringify({ ...brief, cited_findings: [] }), () => ['2026-W45']), /week_data_mismatch/);
  assert.throws(() => buildResearchEvidenceManifest({ articles: [], weekly_briefs: [] }, () => bytes, () => ['2026-W45']), /week_inventory_mismatch/);
  assert.throws(() => buildResearchEvidenceManifest({ articles: [], weekly_briefs: [projection] }, () => bytes, () => []), /week_inventory_mismatch/);
});
