import {
  isRecord,
  normalizeCode,
  normalizeLocale,
  parseJsonObject,
  toArray,
  valueAsString,
} from "./input.ts";
import {
  cleanHeaderValue,
  cleanMarkdown,
  subjectFromMarkdown,
} from "./markdown.ts";

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue | undefined };
export type CampaignPayload = Record<string, unknown>;

export interface NotificationContent extends Record<string, JsonValue> {
  subject: string;
  markdown: string;
}

export interface NotificationMetadata {
  source_locale?: string;
  default_locale?: string;
  target_locales?: string[];
  list_codes?: string[];
  contents?: Record<string, NotificationContent>;
  template_version?: string;
  created_by?: string;
  ai?: JsonValue;
  [key: string]: unknown;
}

export interface CampaignProgress extends Record<string, JsonValue> {
  total: number;
  queued: number;
  sent: number;
  failed: number;
  skipped: number;
  completed: number;
  percent: number;
}

export interface NotificationCampaignRow {
  id: string;
  subject: string;
  content_ref?: string | null;
  metadata_json?: string | null;
  status: string;
  created_at: string;
  scheduled_at?: string | null;
  sent_at?: string | null;
}

export interface NotificationCampaignDeliveryRow {
  id: string;
  status: string;
  provider?: string | null;
  attempts: number;
  last_error?: string | null;
  queued_at: string;
  sent_at?: string | null;
  delivered_at?: string | null;
  failed_at?: string | null;
  email: string;
  locale?: string | null;
  list_code: string;
}

export function normalizeNotificationContents(
  payload: CampaignPayload,
): Record<string, NotificationContent> {
  const result: Record<string, NotificationContent> = {};
  const rawContents = payload.contents;
  if (isRecord(rawContents)) {
    for (const [rawLocale, rawContent] of Object.entries(rawContents)) {
      if (!isRecord(rawContent)) continue;
      const locale = normalizeLocale(rawLocale, "");
      if (!locale) continue;
      const subject = cleanHeaderValue(valueAsString(rawContent.subject), 200);
      const markdown = cleanMarkdown(valueAsString(rawContent.markdown ?? rawContent.body ?? rawContent.content));
      if (subject && markdown) {
        result[locale] = { subject, markdown };
      }
    }
  }

  const topLevelMarkdown = cleanMarkdown(valueAsString(payload.markdown ?? payload.body ?? payload.content));
  if (topLevelMarkdown) {
    const locale = normalizeLocale(valueAsString(payload.source_locale ?? payload.default_locale), "en");
    const subject = cleanHeaderValue(valueAsString(payload.subject) || subjectFromMarkdown(topLevelMarkdown), 200);
    if (subject) {
      result[locale] = { subject, markdown: topLevelMarkdown };
    }
  }

  return result;
}

export function normalizeCampaignListCodes(payload: CampaignPayload): string[] {
  const raw = toArray(payload.list_codes ?? payload.list_code ?? payload.lists);
  const codes = raw.map(normalizeCode).filter(Boolean);
  return [...new Set(codes)];
}

export function normalizeTargetLocales(
  payload: CampaignPayload,
  contents: Record<string, NotificationContent>,
): string[] {
  const requested = toArray(payload.target_locales ?? payload.locales);
  const locales = requested.length > 0
    ? requested.map((item) => normalizeLocale(item, "")).filter(Boolean)
    : Object.keys(contents);
  return [...new Set(locales)];
}

export function parseNotificationMetadata(value: string): NotificationMetadata {
  const parsed = parseJsonObject(value);
  const contents: Record<string, NotificationContent> = {};
  if (isRecord(parsed.contents)) {
    for (const [rawLocale, rawContent] of Object.entries(parsed.contents)) {
      if (!isRecord(rawContent)) continue;
      const locale = normalizeLocale(rawLocale, "");
      if (!locale) continue;
      const subject = cleanHeaderValue(valueAsString(rawContent.subject), 200);
      const markdown = cleanMarkdown(valueAsString(rawContent.markdown));
      if (subject && markdown) {
        contents[locale] = { subject, markdown };
      }
    }
  }
  return {
    ...parsed,
    source_locale: normalizeLocale(valueAsString(parsed.source_locale), ""),
    default_locale: normalizeLocale(valueAsString(parsed.default_locale), "en"),
    target_locales: toArray(parsed.target_locales).map((item) => normalizeLocale(item, "")).filter(Boolean),
    list_codes: toArray(parsed.list_codes).map(normalizeCode).filter(Boolean),
    contents,
  };
}

export function localizedNotificationContent(
  metadata: NotificationMetadata,
  locale: string,
): NotificationContent {
  const contents = metadata.contents || {};
  const requested = normalizeLocale(locale, metadata.default_locale || "en");
  return (
    contents[requested] ||
    contents[metadata.default_locale || "en"] ||
    contents.en ||
    contents.zh ||
    Object.values(contents)[0] ||
    { subject: "GIDS Update", markdown: "GIDS update." }
  );
}

export function campaignProgressFromRows(
  rows: Array<{ status: string; count: number }>,
): CampaignProgress {
  const counts = rows.reduce<Record<string, number>>((acc, row) => {
    acc[row.status] = Number(row.count || 0);
    return acc;
  }, {});
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  const queued = Number(counts.queued || 0);
  const sent = Number(counts.sent || 0);
  const failed = Number(counts.failed || 0);
  const skipped = Number(counts.skipped || 0);
  const completed = sent + failed + skipped;
  return {
    total,
    queued,
    sent,
    failed,
    skipped,
    completed,
    percent: total > 0 ? Math.round((completed / total) * 100) : 100,
  };
}

export function campaignStatusFromProgress(progress: CampaignProgress): string {
  if (progress.total === 0) return "sent";
  if (progress.queued > 0) return progress.completed > 0 ? "sending" : "queued";
  if (progress.failed > 0 && progress.sent > 0) return "partial_failed";
  if (progress.failed > 0) return "failed";
  return "sent";
}

export function notificationCampaignSummaryProjection(
  row: NotificationCampaignRow,
  progress: CampaignProgress,
): Record<string, JsonValue> {
  const metadata = parseNotificationMetadata(row.metadata_json || "");
  return {
    id: row.id,
    subject: row.subject,
    status: row.status,
    created_at: row.created_at,
    scheduled_at: row.scheduled_at || null,
    sent_at: row.sent_at || null,
    source_locale: metadata.source_locale || null,
    default_locale: metadata.default_locale || "en",
    target_locales: metadata.target_locales || Object.keys(metadata.contents || {}),
    list_codes: metadata.list_codes || [],
    audience_count: Number((metadata as Record<string, unknown>).audience_count || progress.total),
    progress,
  };
}

export function notificationCampaignMetadataProjection(
  metadata: NotificationMetadata,
): Record<string, JsonValue> {
  return {
    source_locale: metadata.source_locale || null,
    default_locale: metadata.default_locale || "en",
    target_locales: metadata.target_locales || Object.keys(metadata.contents || {}),
    list_codes: metadata.list_codes || [],
    template_version: metadata.template_version || "admin-notification-v1",
    created_by: metadata.created_by || "dashboard",
    ai: metadata.ai || null,
  };
}

export function notificationCampaignDeliveryProjection(
  delivery: NotificationCampaignDeliveryRow,
  metadata: NotificationMetadata,
  emailMasked: string,
): Record<string, JsonValue> {
  return {
    id: delivery.id,
    status: delivery.status,
    provider: delivery.provider || "smtp",
    attempts: Number(delivery.attempts || 0),
    last_error: delivery.last_error || null,
    queued_at: delivery.queued_at,
    sent_at: delivery.sent_at || null,
    delivered_at: delivery.delivered_at || null,
    failed_at: delivery.failed_at || null,
    email_masked: emailMasked,
    locale: normalizeLocale(delivery.locale || "", metadata.default_locale || "en"),
    list_code: delivery.list_code,
  };
}
