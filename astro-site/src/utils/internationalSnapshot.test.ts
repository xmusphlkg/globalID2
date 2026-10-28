import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { noticeSnapshotBadges, readLocalSnapshot } from './internationalSnapshot.ts';

test('a snapshot carries the newest point, its date and the period delta', () => {
  const snapshot = readLocalSnapshot('US', 'D001');
  assert.ok(snapshot, 'US / D001 is expected in the generated country data');
  assert.equal(snapshot?.countryCode, 'US');
  assert.equal(snapshot?.diseaseId, 'D001');
  assert.ok(typeof snapshot?.latestValue === 'number' && (snapshot?.latestValue as number) > 0, 'trailing zeros are skipped');
  assert.ok(!snapshot?.latestDate || /^\d{4}-\d{2}-\d{2}$/.test(snapshot.latestDate));
  if (snapshot?.changePct != null && snapshot.previousValue) {
    const expected = ((snapshot.latestValue as number) - snapshot.previousValue) / snapshot.previousValue * 100;
    assert.ok(Math.abs(expected - snapshot.changePct) < 1e-9);
  }
});

test('unknown country / disease / hostile key resolves to null', () => {
  assert.equal(readLocalSnapshot('ZZ', 'D001'), null);
  assert.equal(readLocalSnapshot('US', 'D9999'), null);
  assert.equal(readLocalSnapshot('../../etc/passwd', 'D001'), null);
});

test('page context jurisdictions still produce badges when the notice names others', () => {
  const notice = {
    countries: [{ code: 'CD', name_en: 'DR Congo', name_zh: '刚果民主共和国' }],
    diseases: [{ disease_id: 'D001', name_en: 'Plague', name_zh: '鼠疫', slug: 'plague' }],
  };
  assert.equal(noticeSnapshotBadges(notice, 'en').length, 0, 'CD has no local series');
  // US / D001's newest reported point is from 2020, so page context refuses it
  // while a notice that names the jurisdiction itself still gets its snapshot.
  assert.equal(noticeSnapshotBadges(notice, 'en', { contextCodes: ['US'] }).length, 0);
});

test('badges are localized and only appear when a local series exists', () => {
  const notice = {
    countries: [{ code: 'US', name_en: 'United States', name_zh: '美国' }],
    diseases: [{ disease_id: 'D001', name_en: 'Plague', name_zh: '鼠疫', slug: 'plague' }],
  };
  const en = noticeSnapshotBadges(notice, 'en');
  assert.equal(en.length, 1);
  assert.match(en[0], /Local snapshot/);
  assert.match(noticeSnapshotBadges(notice, 'zh')[0], /本地快照/);
  assert.match(noticeSnapshotBadges(notice, 'fr')[0], /Instantané local/);
  assert.equal(noticeSnapshotBadges({ countries: [], diseases: [] }, 'en').length, 0);
  assert.equal(
    noticeSnapshotBadges(
      { countries: [{ code: 'ZZ', name_en: 'Nowhere', name_zh: '无' }], diseases: notice.diseases },
      'en',
    ).length,
    0,
  );
});
