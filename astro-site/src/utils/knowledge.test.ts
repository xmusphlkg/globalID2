import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assessKnowledgeText,
  resolveKnowledgeText,
  stripUnavailableKnowledgeSentences,
} from './knowledge.ts';

test('metadata and absence prose is not treated as public disease knowledge', () => {
  const text = [
    'The evidence boundary consists mainly of scholarly metadata and article titles.',
    'The snippets do not describe clinical features, transmission, prevention, or burden.',
  ].join(' ');

  assert.equal(assessKnowledgeText(text, 'en').available, false);
  assert.equal(stripUnavailableKnowledgeSentences(text, 'en'), null);
});

test('dominant limitation prose is removed while a supported sentence survives', () => {
  const text = [
    'The supplied snippets do not identify a formal risk-group list.',
    'People exposed to infected fleas in established foci have source-backed ecological exposure [1].',
    'More detailed age information is not yet available [1].',
  ].join(' ');

  assert.equal(
    stripUnavailableKnowledgeSentences(text, 'en'),
    'People exposed to infected fleas in established foci have source-backed ecological exposure [1].',
  );
});

test('localized field resolution never falls back across languages', () => {
  const payload = {
    official_definition_en: 'A substantive English disease definition.',
    official_definition_zh: 'This English paragraph was incorrectly stored in the Chinese field.',
  };

  assert.equal(resolveKnowledgeText([payload], 'official_definition', 'en'), payload.official_definition_en);
  assert.equal(resolveKnowledgeText([payload], 'official_definition', 'zh'), null);
});

test('explicit field status prevents stale text from leaking into the page', () => {
  const payload = {
    transmission_en: 'A stale generated transmission paragraph.',
    knowledge_field_status: {
      transmission: { en: 'insufficient_evidence' },
    },
  };

  assert.equal(resolveKnowledgeText([payload], 'transmission', 'en'), null);
});

test('Chinese evidence-boundary prose is not counted as epidemiology', () => {
  const text = [
    '所给来源未包含地理分布、暴发背景或监测负担等流行病学数据。',
    '目前也没有可直接引用的证据说明该类目具有特定季节性模式。',
    '具体监测规模和流行特征尚缺乏源支持。',
  ].join('');

  assert.equal(assessKnowledgeText(text, 'zh').available, false);
  assert.equal(stripUnavailableKnowledgeSentences(text, 'zh'), null);
});
