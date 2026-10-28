import type { Lang } from './i18n';
import { formatLocaleDate } from './i18n';

/** UTC calendar date used across the notice-based Situation pages. */
export function formatDate(value: string | null | undefined, locale: Lang = 'en'): string {
  return formatLocaleDate(value, locale, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  });
}
