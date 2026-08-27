import { buildNotificationEmail } from "../lib/email.ts";
import { insertEmailDelivery, updateEmailDelivery } from "../lib/db.ts";
import { json, publicBaseUrl, requireAdmin, type JsonValue } from "../lib/http.ts";
import { boundedText, isRecord, normalizeCode, normalizeLocale, valueAsString } from "../lib/input.ts";
import { cleanHeaderValue } from "../lib/markdown.ts";
import { FREQUENCIES, maskEmail, normalizeFilters } from "../lib/subscriptions.ts";
import {
  campaignContentFingerprint,
  campaignProgressFromRows,
  campaignStatusFromProgress,
  localizedNotificationContent,
  normalizeCampaignListCodes,
  normalizeCampaignIdempotencyKey,
  normalizeNotificationContents,
  normalizeTargetLocales,
  notificationCampaignDeliveryProjection,
  notificationCampaignMetadataProjection,
  notificationCampaignSummaryProjection,
  parseNotificationMetadata,
  type CampaignProgress,
  type NotificationMetadata,
} from "../lib/campaign.ts";
import type { Env, Payload } from "../types.ts";

type SmtpHandlerConfig = {
  host: string; port: number; username: string; password: string; fromEmail: string;
  fromName: string; useTls: boolean;
};

type Dependencies = {
  readPayload(request: Request): Promise<Payload>;
  createSignedToken(env: Env, purpose: string, subscriptionId: string, ttlSeconds: number): Promise<string>;
  smtpConfig(env: Env): SmtpHandlerConfig | null;
  sendSmtpEmail(config: SmtpHandlerConfig, message: {
    to: string; subject: string; text: string; html: string; messageId?: string;
  }): Promise<void | { providerMessageId: string | null }>;
};

type CampaignRow = {
  id: string; subject: string; content_ref?: string | null; metadata_json?: string | null;
  status: string; created_at: string; scheduled_at?: string | null; sent_at?: string | null;
};

export function createCampaignHandlers(deps: Dependencies) {
  async function list(request: Request, env: Env): Promise<Response> {
    requireAdmin(request, env);
    const url = new URL(request.url);
    const rawLimit = Number(url.searchParams.get("limit") || 25);
    const rawOffset = Number(url.searchParams.get("offset") || 0);
    const limit = Math.min(Math.max(Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 25, 1), 100);
    const offset = Math.max(Number.isFinite(rawOffset) ? Math.trunc(rawOffset) : 0, 0);
    const total = await env.DB.prepare(
      "SELECT COUNT(*) AS count FROM message_campaigns WHERE trigger_type = 'admin_notification'",
    ).first<{ count: number }>();
    const rows = await env.DB.prepare(
      `SELECT id, subject, content_ref, metadata_json, status, created_at, scheduled_at, sent_at
       FROM message_campaigns WHERE trigger_type = 'admin_notification'
       ORDER BY created_at DESC LIMIT ? OFFSET ?`,
    ).bind(limit, offset).all<CampaignRow>();
    const campaigns: JsonValue[] = [];
    for (const row of rows.results || []) campaigns.push(await summary(env, row));
    return json({ ok: true, campaigns, pagination: { total: Number(total?.count || 0), limit, offset } }, request, env);
  }

  async function get(request: Request, env: Env, campaignId: string): Promise<Response> {
    requireAdmin(request, env);
    const rawLimit = Number(new URL(request.url).searchParams.get("delivery_limit") || 100);
    const deliveryLimit = Math.min(Math.max(Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 100, 1), 500);
    const campaign = await detail(env, campaignId, deliveryLimit);
    if (!campaign) return json({ error: "notification_campaign_not_found" }, request, env, 404);
    return json({ ok: true, campaign }, request, env);
  }

  async function create(request: Request, env: Env): Promise<Response> {
    requireAdmin(request, env);
    const payload = await deps.readPayload(request);
    const contents = normalizeNotificationContents(payload);
    const defaultLocale = normalizeLocale(valueAsString(payload.default_locale), "en");
    const fallbackLocale = contents[defaultLocale] ? defaultLocale : Object.keys(contents)[0];
    const defaultContent = contents[fallbackLocale];
    if (!defaultContent?.subject || !defaultContent.markdown) {
      return json({ error: "notification_content_required" }, request, env, 400);
    }
    const requestedListCodes = normalizeCampaignListCodes(payload);
    const listCodes = requestedListCodes.length ? requestedListCodes : ["reports"];
    if (listCodes.includes("alerts")) {
      return json({ error: "verified_situation_alert_endpoint_required" }, request, env, 422);
    }
    const listId = await campaignListId(env, listCodes[0] || "reports");
    if (!listId) return json({ error: "notification_list_not_found" }, request, env, 400);
    const rawMax = Number(payload.max_recipients ?? 10000);
    const maxRecipients = Math.min(Math.max(Number.isFinite(rawMax) ? Math.trunc(rawMax) : 10000, 1), 50000);
    const audienceFilters = normalizeFilters(payload);
    const sourceLocale = normalizeLocale(valueAsString(payload.source_locale), fallbackLocale);
    const targetLocales = normalizeTargetLocales(payload, contents);
    const idempotencyKeySupplied = payload.idempotency_key !== undefined && payload.idempotency_key !== null;
    const rawIdempotencyKey = valueAsString(payload.idempotency_key).trim();
    const idempotencyKey = normalizeCampaignIdempotencyKey(rawIdempotencyKey);
    if (idempotencyKeySupplied && !idempotencyKey) {
      return json({ error: "invalid_notification_idempotency_key" }, request, env, 400);
    }
    const frequencySupplied = payload.frequency !== undefined && payload.frequency !== null;
    const rawFrequency = valueAsString(payload.frequency).trim().toLowerCase();
    if (frequencySupplied && !FREQUENCIES.has(rawFrequency)) {
      return json({ error: "invalid_notification_frequency" }, request, env, 400);
    }
    const sourceRef = boundedText(valueAsString(payload.source_ref), "", 2048);
    const contentFingerprint = await campaignContentFingerprint({
      audience_filters: [...audienceFilters].sort((a, b) => `${a.type}:${a.value}`.localeCompare(`${b.type}:${b.value}`)),
      contents,
      default_locale: fallbackLocale,
      frequency: rawFrequency,
      list_codes: [...listCodes].sort(),
      max_recipients: maxRecipients,
      source_locale: sourceLocale,
      source_ref: sourceRef,
      target_locales: [...targetLocales].sort(),
    });
    const contentRef = idempotencyKey ? `idempotency:${idempotencyKey}` : "metadata_json.contents";
    if (idempotencyKey) {
      const existing = await loadRowByContentRef(env, contentRef);
      if (existing) {
        const prior = parseNotificationMetadata(existing.metadata_json || "");
        if (prior.content_fingerprint !== contentFingerprint) {
          return json({ error: "notification_idempotency_conflict" }, request, env, 409);
        }
        return json({ ok: true, duplicate: true, campaign: await summary(env, existing) }, request, env);
      }
    }
    const recipients = await audience(env, listCodes, maxRecipients, audienceFilters, rawFrequency);
    const now = new Date().toISOString();
    const campaignId = crypto.randomUUID();
    const metadata: NotificationMetadata = {
      source_locale: sourceLocale,
      default_locale: fallbackLocale,
      target_locales: targetLocales,
      list_codes: listCodes,
      contents,
      template_version: "admin-notification-v1",
      created_by: boundedText(valueAsString(payload.created_by), "dashboard", 80),
      audience_count: recipients.length,
      audience_filters: audienceFilters,
      idempotency_key: idempotencyKey || undefined,
      content_fingerprint: contentFingerprint,
      source_ref: sourceRef || undefined,
      frequency: rawFrequency || undefined,
      ai: isRecord(payload.ai) ? payload.ai as JsonValue : undefined,
    };
    const subject = cleanHeaderValue(defaultContent.subject, 200);
    const status = recipients.length > 0 ? "queued" : "sent";
    try {
      await env.DB.prepare(
        `INSERT INTO message_campaigns (
           id, list_id, trigger_type, subject, metadata_json, content_ref, status, created_at, scheduled_at, sent_at
         ) VALUES (?, ?, 'admin_notification', ?, ?, ?, ?, ?, NULL, NULL)`,
      ).bind(campaignId, listId, subject, JSON.stringify(metadata), contentRef, status, now).run();
    } catch (error) {
      if (!idempotencyKey) throw error;
      const existing = await loadRowByContentRef(env, contentRef);
      if (!existing) throw error;
      const prior = parseNotificationMetadata(existing.metadata_json || "");
      if (prior.content_fingerprint !== contentFingerprint) {
        return json({ error: "notification_idempotency_conflict" }, request, env, 409);
      }
      return json({ ok: true, duplicate: true, campaign: await summary(env, existing) }, request, env);
    }
    for (const recipient of recipients) {
      await env.DB.prepare(
        `INSERT INTO message_deliveries (
           id, campaign_id, subscription_id, contact_id, status, provider, attempts, queued_at
         ) VALUES (?, ?, ?, ?, 'queued', 'smtp', 0, ?)`,
      ).bind(crypto.randomUUID(), campaignId, recipient.subscription_id, recipient.contact_id, now).run();
    }
    return json({
      ok: true,
      duplicate: false,
      campaign: await summary(env, {
        id: campaignId, subject, content_ref: contentRef, metadata_json: JSON.stringify(metadata),
        status, created_at: now, scheduled_at: null, sent_at: null,
      }),
    }, request, env, 201);
  }

  async function process(request: Request, env: Env, campaignId: string): Promise<Response> {
    requireAdmin(request, env);
    const payload = await deps.readPayload(request);
    const requested = Number(payload.batch_size ?? new URL(request.url).searchParams.get("batch_size") ?? env.NOTIFICATION_BATCH_SIZE ?? 20);
    const batchSize = Math.min(Math.max(Number.isFinite(requested) ? Math.trunc(requested) : 20, 1), 100);
    const row = await loadRow(env, campaignId);
    if (!row) return json({ error: "notification_campaign_not_found" }, request, env, 404);
    const config = deps.smtpConfig(env);
    if (!config) {
      const now = new Date().toISOString();
      await env.DB.prepare(
        `UPDATE message_deliveries SET status = 'failed', provider = 'smtp', attempts = attempts + 1,
         last_error = 'smtp_not_configured', failed_at = COALESCE(failed_at, ?)
         WHERE campaign_id = ? AND status = 'queued'`,
      ).bind(now, campaignId).run();
      const state = await refreshStatus(env, campaignId);
      return json({ ok: true, campaign_id: campaignId, processed: 0, status: state.status,
        progress: state.progress, reason: "smtp_not_configured" }, request, env);
    }
    const metadata = parseNotificationMetadata(row.metadata_json || "");
    const deliveries = await env.DB.prepare(
      `SELECT d.id AS delivery_id, d.subscription_id, d.contact_id, d.attempts,
       s.subscriber_id, sub.locale, c.address AS email FROM message_deliveries d
       JOIN subscriptions s ON s.id = d.subscription_id JOIN subscriber_contacts c ON c.id = d.contact_id
       JOIN subscribers sub ON sub.id = s.subscriber_id
       WHERE d.campaign_id = ? AND d.status = 'queued' ORDER BY d.queued_at ASC LIMIT ?`,
    ).bind(campaignId, batchSize).all<{
      delivery_id: string; subscription_id: string; contact_id: string; attempts: number;
      subscriber_id: string; locale?: string | null; email: string;
    }>();
    if ((deliveries.results || []).length) {
      await env.DB.prepare("UPDATE message_campaigns SET status = 'sending' WHERE id = ? AND status IN ('queued', 'draft', 'sending')")
        .bind(campaignId).run();
    }
    let processed = 0;
    for (const delivery of deliveries.results || []) {
      processed += 1;
      const locale = normalizeLocale(delivery.locale || "", metadata.default_locale || "en");
      const content = localizedNotificationContent(metadata, locale);
      const token = await deps.createSignedToken(env, "unsubscribe", delivery.subscription_id, 60 * 60 * 24 * 365);
      const email = buildNotificationEmail(locale, content,
        `${publicBaseUrl(request, env)}/api/subscriptions/unsubscribe?token=${token}`);
      const transactionId = crypto.randomUUID();
      await insertEmailDelivery(env.DB, {
        deliveryId: transactionId, subscriberId: delivery.subscriber_id, contactId: delivery.contact_id,
        subscriptionId: delivery.subscription_id, recipient: delivery.email, subject: email.subject,
        deliveryType: "admin_notification", provider: "smtp", status: "queued",
        attempts: Number(delivery.attempts || 0) + 1, source: "admin_notification",
        metadata: { campaign_id: campaignId, message_delivery_id: delivery.delivery_id, locale },
        now: new Date().toISOString(),
      });
      try {
        const receipt = await deps.sendSmtpEmail(config, {
          to: delivery.email, subject: email.subject, text: email.text, html: email.html,
          messageId: `${transactionId}@globalinfectiousdisease.com`,
        });
        const providerMessageId = receipt?.providerMessageId || `${transactionId}@globalinfectiousdisease.com`;
        const sentAt = new Date().toISOString();
        await env.DB.prepare(
          `UPDATE message_deliveries SET status = 'sent', provider = 'smtp', attempts = attempts + 1,
           provider_message_id = ?, last_error = NULL, sent_at = ?, failed_at = NULL WHERE id = ?`,
        ).bind(providerMessageId, sentAt, delivery.delivery_id).run();
        await updateEmailDelivery(env.DB, {
          deliveryId: transactionId, status: "sent", sentAt, providerMessageId,
        });
      } catch (error) {
        const message = boundedText(error instanceof Error ? error.message : "smtp_delivery_failed", "smtp_delivery_failed", 500);
        await env.DB.prepare(
          `UPDATE message_deliveries SET status = 'failed', provider = 'smtp', attempts = attempts + 1,
           last_error = ?, failed_at = ? WHERE id = ?`,
        ).bind(message, new Date().toISOString(), delivery.delivery_id).run();
        await updateEmailDelivery(env.DB, { deliveryId: transactionId, status: "failed",
          errorCode: "smtp_delivery_failed", errorMessage: message });
      }
    }
    const state = await refreshStatus(env, campaignId);
    return json({ ok: true, campaign_id: campaignId, processed, status: state.status, progress: state.progress }, request, env);
  }

  return { list, get, create, process };
}

async function campaignListId(env: Env, code: string): Promise<string | null> {
  const row = await env.DB.prepare("SELECT id FROM subscription_lists WHERE code = ?")
    .bind(normalizeCode(code || "reports")).first<{ id: string }>();
  if (row?.id) return row.id;
  return null;
}

async function audience(
  env: Env,
  codes: string[],
  limit: number,
  filters: Array<{ type: string; value: string }> = [],
  frequency = "",
) {
  const clauses = ["s.status = 'active'", "c.channel = 'email'", "c.status = 'active'", "sub.status = 'active'"];
  const binds: unknown[] = [];
  if (codes.length) { clauses.push(`l.code IN (${codes.map(() => "?").join(", ")})`); binds.push(...codes); }
  if (frequency) { clauses.push("s.frequency = ?"); binds.push(frequency); }
  const grouped = new Map<string, string[]>();
  for (const filter of filters) {
    const values = grouped.get(filter.type) || [];
    if (!values.includes(filter.value)) values.push(filter.value);
    grouped.set(filter.type, values);
  }
  for (const [type, values] of grouped) {
    clauses.push(
      `(NOT EXISTS (
        SELECT 1 FROM subscription_filters f
        WHERE f.subscription_id = s.id AND f.filter_type = ?
      ) OR EXISTS (
        SELECT 1 FROM subscription_filters f
        WHERE f.subscription_id = s.id AND f.filter_type = ?
          AND f.filter_value IN (${values.map(() => "?").join(", ")})
      ))`,
    );
    binds.push(type, type, ...values);
  }
  binds.push(limit);
  const rows = await env.DB.prepare(
    `SELECT MIN(s.id) AS subscription_id, MIN(s.subscriber_id) AS subscriber_id, s.contact_id AS contact_id,
     c.address AS email, COALESCE(NULLIF(sub.locale, ''), 'en') AS locale, MIN(s.created_at) AS first_subscription_at
     FROM subscriptions s JOIN subscriber_contacts c ON c.id = s.contact_id
     JOIN subscribers sub ON sub.id = s.subscriber_id JOIN subscription_lists l ON l.id = s.list_id
     WHERE ${clauses.join(" AND ")} GROUP BY s.contact_id, c.address, sub.locale
     ORDER BY first_subscription_at ASC LIMIT ?`,
  ).bind(...binds).all<{ subscription_id: string; subscriber_id: string; contact_id: string; email: string; locale: string }>();
  return (rows.results || []).filter((row) => row.subscription_id && row.contact_id && row.email);
}

async function loadRow(env: Env, id: string): Promise<CampaignRow | null> {
  return env.DB.prepare(
    `SELECT id, subject, content_ref, metadata_json, status, created_at, scheduled_at, sent_at
     FROM message_campaigns WHERE id = ? AND trigger_type = 'admin_notification'`,
  ).bind(id).first<CampaignRow>();
}

async function loadRowByContentRef(env: Env, contentRef: string): Promise<CampaignRow | null> {
  return env.DB.prepare(
    `SELECT id, subject, content_ref, metadata_json, status, created_at, scheduled_at, sent_at
     FROM message_campaigns WHERE trigger_type = 'admin_notification' AND content_ref = ?`,
  ).bind(contentRef).first<CampaignRow>();
}

async function progress(env: Env, id: string): Promise<CampaignProgress> {
  const rows = await env.DB.prepare("SELECT status, COUNT(*) AS count FROM message_deliveries WHERE campaign_id = ? GROUP BY status")
    .bind(id).all<{ status: string; count: number }>();
  return campaignProgressFromRows(rows.results || []);
}

async function summary(env: Env, row: CampaignRow): Promise<JsonValue> {
  return notificationCampaignSummaryProjection(row, await progress(env, row.id));
}

async function detail(env: Env, id: string, limit: number): Promise<JsonValue | null> {
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

async function refreshStatus(env: Env, id: string) {
  const state = await progress(env, id);
  const status = campaignStatusFromProgress(state);
  const finishedAt = state.queued === 0 && state.deferred === 0 ? new Date().toISOString() : null;
  await env.DB.prepare(
    `UPDATE message_campaigns SET status = ?,
     sent_at = CASE WHEN ? IS NOT NULL THEN COALESCE(sent_at, ?) ELSE sent_at END WHERE id = ?`,
  ).bind(status, finishedAt, finishedAt, id).run();
  return { status, progress: state };
}
