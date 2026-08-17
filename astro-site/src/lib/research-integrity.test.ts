import assert from 'node:assert/strict';
import test from 'node:test';

import { integrityAlertLabel, normalizeIntegrityAlerts } from './research-integrity.ts';

test('normalizes only public integrity alert types and strips private fields', () => {
  const alerts = normalizeIntegrityAlerts([
    {
      alert_id: 'correction-1',
      article_id: 'article-1',
      article_title: 'Corrected dengue study',
      event_type: 'correction',
      current_status: 'corrected',
      effective_at: '2026-08-02T00:00:00Z',
      source: 'crossref',
      source_url: 'https://doi.org/10.1000/example',
      is_currently_public: true,
      article_url: '/research/articles/corrected-dengue-study/',
      summary: 'must not cross the browser boundary',
      metadata: { raw: 'must not cross the browser boundary' },
    },
    {
      alert_id: 'retraction-1',
      article_title: 'Retracted study',
      event_type: 'integrity_status_changed',
      current_status: 'retracted',
      effective_at: '2026-08-03T00:00:00Z',
      source: 'publisher',
      source_url: 'javascript:alert(1)',
      is_currently_public: false,
      article_url: '/research/articles/retracted-study/',
    },
    {
      alert_id: 'current-1',
      article_title: 'Current study',
      current_status: 'current',
    },
  ]);

  assert.deepEqual(alerts.map(alert => alert.event_type), ['retraction', 'correction']);
  assert.equal(alerts[0].source_url, undefined);
  assert.equal(alerts[0].article_url, undefined);
  assert.equal(alerts[1].article_url, '/research/articles/corrected-dengue-study/');
  assert.equal('summary' in alerts[1], false);
  assert.equal('metadata' in alerts[1], false);
});

test('provides bilingual labels for every integrity status', () => {
  assert.equal(integrityAlertLabel('retraction'), 'Retraction');
  assert.equal(integrityAlertLabel('expression_of_concern', 'zh'), '关注声明');
  assert.equal(integrityAlertLabel('correction', 'zh'), '更正');
});
