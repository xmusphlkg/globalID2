export type IntegrityAlertType = 'retraction' | 'expression_of_concern' | 'correction';

export interface ResearchIntegrityAlert {
  alert_id: string;
  article_id?: string;
  article_slug?: string;
  article_title: string;
  doi?: string;
  journal?: string;
  published_at?: string;
  event_type: IntegrityAlertType;
  previous_status?: string;
  current_status: 'retracted' | 'expression_of_concern' | 'corrected';
  effective_at?: string;
  recorded_at?: string;
  source: string;
  source_url?: string;
  is_currently_public: boolean;
  article_url?: string;
}

const STATUS_BY_EVENT: Record<IntegrityAlertType, ResearchIntegrityAlert['current_status']> = {
  retraction: 'retracted',
  expression_of_concern: 'expression_of_concern',
  correction: 'corrected',
};

const EVENT_BY_STATUS: Record<string, IntegrityAlertType> = {
  retracted: 'retraction',
  retraction: 'retraction',
  expression_of_concern: 'expression_of_concern',
  corrected: 'correction',
  correction: 'correction',
};

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function publicUrl(value: unknown, internal = false): string | undefined {
  const candidate = text(value);
  if (!candidate) return undefined;
  if (internal && candidate.startsWith('/research/articles/')) return candidate;
  if (!internal && /^https?:\/\//i.test(candidate)) return candidate;
  return undefined;
}

function eventTimestamp(alert: ResearchIntegrityAlert): number {
  const parsed = Date.parse(alert.effective_at ?? alert.recorded_at ?? '');
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function normalizeIntegrityAlerts(value: unknown): ResearchIntegrityAlert[] {
  if (!Array.isArray(value)) return [];
  const alerts: ResearchIntegrityAlert[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue;
    const record = raw as Record<string, unknown>;
    const rawEvent = text(record.event_type)?.toLowerCase().replace(/[\s-]+/g, '_');
    const rawStatus = text(record.current_status)?.toLowerCase().replace(/[\s-]+/g, '_');
    const eventType = (rawEvent && EVENT_BY_STATUS[rawEvent])
      || (rawStatus && EVENT_BY_STATUS[rawStatus]);
    const articleTitle = text(record.article_title);
    const alertId = text(record.alert_id);
    if (!eventType || !articleTitle || !alertId) continue;
    const isCurrentlyPublic = record.is_currently_public === true;
    alerts.push({
      alert_id: alertId,
      article_id: text(record.article_id),
      article_slug: text(record.article_slug),
      article_title: articleTitle,
      doi: text(record.doi),
      journal: text(record.journal),
      published_at: text(record.published_at),
      event_type: eventType,
      previous_status: text(record.previous_status),
      current_status: STATUS_BY_EVENT[eventType],
      effective_at: text(record.effective_at),
      recorded_at: text(record.recorded_at),
      source: text(record.source) ?? 'source record',
      source_url: publicUrl(record.source_url),
      is_currently_public: isCurrentlyPublic,
      article_url: isCurrentlyPublic ? publicUrl(record.article_url, true) : undefined,
    });
  }
  return alerts.sort((left, right) => (
    eventTimestamp(right) - eventTimestamp(left)
    || right.alert_id.localeCompare(left.alert_id)
  ));
}

export function integrityAlertLabel(
  eventType: IntegrityAlertType,
  locale: 'en' | 'zh' = 'en',
): string {
  const labels = {
    retraction: { en: 'Retraction', zh: '撤稿' },
    expression_of_concern: { en: 'Expression of concern', zh: '关注声明' },
    correction: { en: 'Correction', zh: '更正' },
  } as const;
  return labels[eventType][locale];
}
