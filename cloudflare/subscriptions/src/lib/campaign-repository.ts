import { type JsonValue } from "./http.ts";
import { normalizeCode } from "./input.ts";

import { maskEmail } from "./subscriptions.ts";
import { campaignProgressFromRows, campaignStatusFromProgress, notificationCampaignDeliveryProjection, notificationCampaignMetadataProjection, notificationCampaignSummaryProjection, parseNotificationMetadata, type CampaignProgress } from "./campaign.ts";
import type { Env } from "../types.ts";

export type CampaignRow = {
  id: string; subject: string; content_ref?: string | null; metadata_json?: string | null;
  status: string; created_at: string; scheduled_at?: string | null; sent_at?: string | null;
};

export async function campaignListId(env: Env, code: string): Promise<string | null> {
  const row = await env.DB.prepare("SELECT id FROM subscription_lists WHERE code = ?")
    .bind(normalizeCode(code || "reports")).first<{ id: string }>();
  if (row?.id) return row.id;
  return null;
}

export async function loadRow(env: Env, id: string): Promise<CampaignRow | null> {
  return env.DB.prepare(
    `SELECT id, subject, content_ref, metadata_json, status, created_at, scheduled_at, sent_at
     FROM message_campaigns WHERE id = ? AND trigger_type = 'admin_notification'`,
  ).bind(id).first<CampaignRow>();
}

export async function loadRowByContentRef(env: Env, contentRef: string): Promise<CampaignRow | null> {
  return env.DB.prepare(
    `SELECT id, subject, content_ref, metadata_json, status, created_at, scheduled_at, sent_at
     FROM message_campaigns WHERE trigger_type = 'admin_notification' AND content_ref = ?`,
  ).bind(contentRef).first<CampaignRow>();
}

export async function progress(env: Env, id: string): Promise<CampaignProgress> {
  const rows = await env.DB.prepare("SELECT status, COUNT(*) AS count FROM message_deliveries WHERE campaign_id = ? GROUP BY status")
    .bind(id).all<{ status: string; count: number }>();
  return campaignProgressFromRows(rows.results || []);
}

export async function summary(env: Env, row: CampaignRow): Promise<JsonValue> {
  return notificationCampaignSummaryProjection(row, await progress(env, row.id));
}

export async function detail(env: Env, id: string, limit: number): Promise<JsonValue | null> {
  const row = await loadRow(env, id);
  if (!row) return null;
  const base = await summary(env, row) as Record<string, JsonValue>;
  const metadata = parseNotificationMetadata(row.metadata_json || "");
  const deliveries = await env.DB.prepare(
    `SELECT d.id, d.status, d.provider, d.attempts, d.last_error, d.queued_at, d.sent_at,
     d.delivered_at, d.failed_at, c.address AS email, sub.locale, l.code AS list_code
     FROM message_deliveries d JOIN subscriptions s ON s.id = d.subscription_id
     JOIN subscriber_contacts c ON c.id = d.contact_id JOIN subscribers sub ON sub.id = s.subscriber_id
     JOIN subscription_lists l ON l.id = s.list_id WHERE d.campaign_id = ? ORDER BY d.queued_at DESC LIMIT ?`,
  ).bind(id, limit).all<{
    id: string; status: string; provider?: string | null; attempts: number; last_error?: string | null;
    queued_at: string; sent_at?: string | null; delivered_at?: string | null; failed_at?: string | null;
    email: string; locale?: string | null; list_code: string;
  }>();
  return { ...base, metadata: notificationCampaignMetadataProjection(metadata), contents: metadata.contents || {},
    deliveries: (deliveries.results || []).map((item) =>
      notificationCampaignDeliveryProjection(item, metadata, maskEmail(item.email))) };
}

export async function refreshStatus(env: Env, id: string) {
  const state = await progress(env, id);
  const status = campaignStatusFromProgress(state);
  const finishedAt = state.queued === 0 && state.deferred === 0 ? new Date().toISOString() : null;
  await env.DB.prepare(
    `UPDATE message_campaigns SET status = ?,
     sent_at = CASE WHEN ? IS NOT NULL THEN COALESCE(sent_at, ?) ELSE sent_at END WHERE id = ?`,
  ).bind(status, finishedAt, finishedAt, id).run();
  return { status, progress: state };
}
