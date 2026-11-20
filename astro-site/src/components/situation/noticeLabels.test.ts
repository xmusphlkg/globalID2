import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { countLabels, countsLine, diseaseName, hostLabel } from './noticeLabels.ts';
import type { Locale, SituationAlertRecord } from '../../utils/situationTypes.ts';

test('French situation disease labels use canonical names while retaining unclassified source labels', () => {
  const flu = { disease_id: 'D038', disease_label: 'Influenza', disease_name_en: 'Influenza', disease_name_zh: '流行性感冒' };
  assert.equal(diseaseName(flu, 'fr'), 'Grippe');
  assert.equal(diseaseName(flu, 'en'), 'Influenza');
  assert.equal(diseaseName(flu, 'zh'), '流行性感冒');
  assert.equal(diseaseName({ disease_slug: 'measles', disease_label: 'Measles', disease_name_en: 'Measles' }, 'fr'), 'Rougeole');
  assert.equal(diseaseName({ ...flu, disease_name_fr: 'Grippe saisonnière' }, 'fr'), 'Grippe saisonnière');
  assert.equal(diseaseName({ disease_id: null, disease_label: 'Unclassified event', disease_name_en: '' }, 'fr'), 'Unclassified event');
});

test('all locales distinguish people, animals and records without a confirmed unit', () => {
  for (const locale of ['en', 'zh', 'fr'] as Locale[]) {
    const animal = { host_type: 'animal', host_species: 'wild birds', count_unit: 'animals' } as const;
    const human = { host_type: 'human', count_unit: 'people' } as const;
    assert.notDeepEqual(countLabels(animal, locale), countLabels(human, locale));
    assert.notDeepEqual(countLabels({}, locale), countLabels(human, locale));
    assert.deepEqual(countLabels({ host_type: 'animal', count_unit: 'people' }, locale), countLabels({}, locale));
    assert.match(hostLabel(animal, locale), /wild birds/);
    const record = { ...animal, cases: 383, deaths: 29, count_basis: null, as_of: null } as SituationAlertRecord;
    const line = countsLine(record, locale);
    assert.ok(line.includes('383') && line.includes('29'));
    assert.ok(line.includes(countLabels(animal, locale).cases));
  }
});
