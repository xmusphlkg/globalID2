import { utcDay } from './data-updates.ts';

export type ChangeKind = 'new' | 'improved' | 'fixed';
export type ChangelogSection = {
  kind: ChangeKind;
  labelEn: string;
  labelZh: string;
  labelFr?: string;
  items: Array<{ en: string; zh: string; fr?: string }>;
};
export type ChangelogRelease = {
  version: string;
  date: string;
  titleEn: string;
  titleZh: string;
  titleFr?: string;
  summaryEn: string;
  summaryZh: string;
  summaryFr?: string;
  sections: ChangelogSection[];
};

export const CHANGELOG_URL = '/site-data/updates/changelog.json';
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;

/** Reject partial translations and invalid dates before publishing or displaying. */
export function readChangelog(value: unknown): ChangelogRelease[] {
  if (!record(value) || value.schema_version !== 1 || !Array.isArray(value.releases) || !value.releases.length) {
    throw new Error('Invalid website release history');
  }
  const versions = new Set<string>();
  for (const release of value.releases) {
    if (!record(release) || typeof release.version !== 'string' || !/^\d+\.\d+\.\d+$/.test(release.version)
      || versions.has(release.version) || typeof release.date !== 'string' || utcDay(release.date) !== release.date
      || !['titleEn', 'titleZh', 'titleFr', 'summaryEn', 'summaryZh', 'summaryFr'].every(key => text(release[key]))
      || !Array.isArray(release.sections) || !release.sections.length) throw new Error('Invalid website release');
    for (const section of release.sections) {
      if (!record(section) || !['new', 'improved', 'fixed'].includes(String(section.kind))
        || !['labelEn', 'labelZh', 'labelFr'].every(key => text(section[key])) || !Array.isArray(section.items) || !section.items.length
        || !section.items.every(item => record(item) && ['en', 'zh', 'fr'].every(key => text(item[key])))) {
        throw new Error('Invalid website release translation');
      }
    }
    versions.add(release.version);
  }
  return value.releases as ChangelogRelease[];
}
