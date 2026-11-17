import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { countLabels, countsLine, hostLabel } from './noticeLabels.ts';
import type { Locale, SituationAlertRecord } from '../../utils/situationTypes.ts';

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
