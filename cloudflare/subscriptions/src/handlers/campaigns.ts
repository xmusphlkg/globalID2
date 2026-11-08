import { deliveryMode, recipientSql } from "../lib/pilot.ts";
import { buildNotificationEmail } from "../lib/email.ts";
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
    to: string; subject: string; text: string; html: string; messageId?: string; unsubscribeUrl?: string;
  }): Promise<void | { providerMessageId: string | null }>;
};

type CampaignRow = {
  id: string; subject: string; content_ref?: string | null; metadata_json?: string | null;
  status: string; created_at: string; scheduled_at?: string | null; sent_at?: string | null;
};

type ContentItem = { id: string; filters: Array<{ type: string; value: string }> };
const CONTENT_FILTER_TYPES = new Set(["country", "disease", "research_topic", "study_type", "peer_review_status"]);

function contentItems(payload: Payload): ContentItem[] | null {
  if (payload.content_items === undefined) return null;
  if (!Array.isArray(payload.content_items) || payload.content_items.length < 1 || payload.content_items.length > 100) {
    throw new Error("invalid_content_items");
  }
  const ids = new Set<string>();
  const items = payload.content_items.map((item) => {
    if (!isRecord(item)) throw new Error("invalid_content_items");
    const id = boundedText(valueAsString(item.id), "", 200);
    if (!id || ids.has(id)) throw new Error("invalid_content_items");
    ids.add(id);
    const filters = normalizeFilters(item).filter((filter) => CONTENT_FILTER_TYPES.has(filter.type));
    if (filters.length > 200) throw new Error("invalid_content_items");
    return { id, filters: filters.sort((a, b) => `${a.type}:${a.value}`.localeCompare(`${b.type}:${b.value}`)) };
  }).sort((a, b) => a.id.localeCompare(b.id));
  if (new TextEncoder().encode(JSON.stringify(items)).length > 1_000_000) throw new Error("invalid_content_items");
  return items;
}

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
    let items: ContentItem[] | null;
    try { items = contentItems(payload); } catch { return json({ error: "invalid_content_items" }, request, env, 400); }
    const sourceLocale = normalizeLocale(valueAsString(payload.source_locale), fallbackLocale);
    const targetLocales = normalizeTargetLocales(payload, contents);
    const idempotencyKeySupplied = payload.idempotency_key !== undefined && payload.idempotency_key !== null;
    const rawIdempotencyKey = valueAsString(payload.idempotency_key).trim();
    const idempotencyKey = normalizeCampaignIdempotencyKey(rawIdempotencyKey);
    if (idempotencyKeySupplied && !idempotencyKey) {
      return json({ error: "invalid_notification_idempotency_key" }, request, env, 400);
    }
    if (idempotencyKey && idempotencyKey.startsWith("pilot:") !== (deliveryMode(env) === "pilot")) {
      return json({ error: "notification_delivery_mode_mismatch" }, request, env, 409);
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
      ...(items ? { content_items: items } : {}),
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
    const recipients = await audience(env, listCodes, maxRecipients, audienceFilters, rawFrequency, items);
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
      const statements = [env.DB.prepare(
        `INSERT INTO message_campaigns (
           id, list_id, trigger_type, subject, metadata_json, content_ref, status, created_at, scheduled_at, sent_at
         ) VALUES (?, ?, 'admin_notification', ?, ?, ?, ?, ?, NULL, NULL)`,
      ).bind(campaignId, listId, subject, JSON.stringify(metadata), contentRef, status, now)];
      // Commit the campaign and its complete audience together. JSON chunks keep
      // each SQL value comfortably below D1's maximum string size.
      for (let offset = 0; offset < recipients.length; offset += 1000) {
        const chunk = recipients.slice(offset, offset + 1000).map((recipient) => [
          crypto.randomUUID(), recipient.subscription_id, recipient.contact_id,
        ]);
        statements.push(env.DB.prepare(
          `INSERT INTO message_deliveries (
             id, campaign_id, subscription_id, contact_id, status, provider, attempts, queued_at
           ) SELECT json_extract(value, '$[0]'), ?, json_extract(value, '$[1]'),
                    json_extract(value, '$[2]'), 'queued', 'smtp', 0, ? FROM json_each(?)`,
        ).bind(campaignId, now, JSON.stringify(chunk)));
      }
      await env.DB.batch(statements);
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
    // Each recipient requires several durable D1 operations. Bound each
    // invocation so processing also remains within the Free plan's query limit.
    const batchSize = Math.min(Math.max(Number.isFinite(requested) ? Math.trunc(requested) : 5, 1), 5);
    const row = await loadRow(env, campaignId);
    if (!row) return json({ error: "notification_campaign_not_found" }, request, env, 404);
    // An explicit process request authorizes this campaign for later recovery.
    // Draft/queued campaigns that were never started remain manual.
    await env.DB.prepare(
      "UPDATE message_campaigns SET processing_started_at = COALESCE(processing_started_at, ?) WHERE id = ?",
    ).bind(new Date().toISOString(), campaignId).run();
    const config = deps.smtpConfig(env);
    if (!config) {
      // Configuration failures must not permanently fail every recipient.
      const state = await refreshStatus(env, campaignId);
      return json({ ok: false, campaign_id: campaignId, processed: 0, status: state.status,
        progress: state.progress, reason: "smtp_not_configured" }, request, env, 503);
    }
    const now = new Date().toISOString();
    // SMTP acceptance is not transactional with D1. Never automatically resend
    // an expired in-flight claim: its outcome could already have been accepted.
    await env.DB.prepare(
      `UPDATE message_deliveries SET status = 'unknown', last_error = 'claim_expired_unknown',
       claim_token = NULL, claim_expires_at = NULL
       WHERE campaign_id = ? AND status = 'sending' AND claim_expires_at < ?`,
    ).bind(campaignId, now).run();
    const metadata = parseNotificationMetadata(row.metadata_json || "");
    const policy = recipientSql(env, "c.address");
    const candidates = await env.DB.prepare(
      `SELECT d.id FROM message_deliveries d JOIN subscriber_contacts c ON c.id=d.contact_id
       WHERE d.campaign_id = ? AND d.status = 'queued' AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?) AND ${policy.sql}
       ORDER BY d.queued_at ASC LIMIT ?`,
    ).bind(campaignId, now, ...policy.binds, batchSize).all<{ id: string }>();
    let processed = 0;
    for (const candidate of candidates.results || []) {
      const claimToken = crypto.randomUUID();
      const claimUntil = new Date(Date.now() + 10 * 60 * 1000).toISOString();
      await env.DB.prepare(
        `UPDATE message_deliveries SET status = 'sending', claim_token = ?, claim_expires_at = ?,
         attempts = attempts + 1, next_attempt_at = NULL
         WHERE id = ? AND status = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)`,
      ).bind(claimToken, claimUntil, candidate.id, new Date().toISOString()).run();
      const delivery = await env.DB.prepare(
        `SELECT d.id AS delivery_id, d.subscription_id, d.contact_id, d.attempts,
         s.subscriber_id, s.status AS subscription_status, sub.status AS subscriber_status,
         c.status AS contact_status, sub.locale, c.address AS email FROM message_deliveries d
         JOIN subscriptions s ON s.id = d.subscription_id
         JOIN subscriber_contacts c ON c.id = d.contact_id
         JOIN subscribers sub ON sub.id = s.subscriber_id
         WHERE d.id = ? AND d.status = 'sending' AND d.claim_token = ?`,
      ).bind(candidate.id, claimToken).first<{
        delivery_id: string; subscription_id: string; contact_id: string; attempts: number;
        subscriber_id: string; locale?: string | null; email: string;
        subscription_status: string; subscriber_status: string; contact_status: string;
      }>();
      if (!delivery) continue;
      processed += 1;
      if (delivery.subscription_status !== "active" || delivery.subscriber_status !== "active" || delivery.contact_status !== "active") {
        await env.DB.prepare(
          `UPDATE message_deliveries SET status = 'skipped', last_error = 'subscription_inactive',
           claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`,
        ).bind(delivery.delivery_id, claimToken).run();
        continue;
      }
      const locale = normalizeLocale(delivery.locale || "", metadata.default_locale || "en");
      const content = localizedNotificationContent(metadata, locale);
      const token = await deps.createSignedToken(env, "unsubscribe", delivery.subscription_id, 60 * 60 * 24 * 365);
      const unsubscribeUrl = `${publicBaseUrl(request, env)}/api/subscriptions/unsubscribe?token=${token}`;
      const manageToken = await deps.createSignedToken(env, "manage", delivery.subscription_id, 60 * 60 * 24);
      const manageUrl = `${publicBaseUrl(request, env)}/api/subscriptions/manage?token=${manageToken}`;
      const email = buildNotificationEmail(locale, content, unsubscribeUrl, manageUrl);
      const transactionId = crypto.randomUUID();
      // The delivery ledger is part of the state machine; failure must block SMTP.
      try {
        await env.DB.batch([
        env.DB.prepare(
          `INSERT INTO transactional_email_deliveries (
             id, subscriber_id, contact_id, subscription_id, delivery_type, channel, recipient, subject,
             provider, status, attempts, request_source, metadata_json, created_at, updated_at
           ) VALUES (?, ?, ?, ?, 'admin_notification', 'email', ?, ?, 'smtp', 'queued', ?, 'admin_notification', ?, ?, ?)`,
        ).bind(transactionId, delivery.subscriber_id, delivery.contact_id, delivery.subscription_id,
          delivery.email, email.subject, delivery.attempts,
          JSON.stringify({ campaign_id: campaignId, message_delivery_id: delivery.delivery_id, locale }), now, now),
        env.DB.prepare(
          `UPDATE message_deliveries SET transaction_delivery_id = ?
           WHERE id = ? AND status = 'sending' AND claim_token = ?`,
        ).bind(transactionId, delivery.delivery_id, claimToken),
        ]);
      } catch (error) {
        // The batch is transactional and SMTP has not started. Release this
        // claim for recovery; if D1 is unavailable, expiry remains conservative.
        await env.DB.prepare(
          `UPDATE message_deliveries SET status = 'queued', claim_token = NULL, claim_expires_at = NULL,
           last_error = 'delivery_ledger_unavailable' WHERE id = ? AND status = 'sending' AND claim_token = ?`,
        ).bind(delivery.delivery_id, claimToken).run().catch(() => {});
        throw error;
      }
      // Recheck after preparation, immediately before invoking the provider.
      const eligible = await env.DB.prepare(
        `SELECT d.id FROM message_deliveries d JOIN subscriptions s ON s.id = d.subscription_id
         JOIN subscriber_contacts c ON c.id = d.contact_id JOIN subscribers sub ON sub.id = s.subscriber_id
         WHERE d.id = ? AND d.status = 'sending' AND d.claim_token = ?
           AND d.claim_expires_at > ?
           AND s.status = 'active' AND c.status = 'active' AND sub.status = 'active'`,
      ).bind(delivery.delivery_id, claimToken, new Date().toISOString()).first<{ id: string }>();
      if (!eligible) {
        await env.DB.batch([
          env.DB.prepare(`UPDATE message_deliveries
            SET status = CASE WHEN claim_expires_at <= ? THEN 'unknown' ELSE 'skipped' END,
            last_error = CASE WHEN claim_expires_at <= ? THEN 'claim_expired_unknown' ELSE 'subscription_inactive' END,
            claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`)
            .bind(new Date().toISOString(), new Date().toISOString(), delivery.delivery_id, claimToken),
          env.DB.prepare(`UPDATE transactional_email_deliveries
            SET status = CASE WHEN EXISTS (SELECT 1 FROM message_deliveries WHERE id = ? AND status = 'skipped')
              THEN 'skipped' ELSE 'unknown' END, updated_at = ? WHERE id = ? AND status = 'queued'`)
            .bind(delivery.delivery_id, new Date().toISOString(), transactionId),
        ]);
        continue;
      }
      let receipt: void | { providerMessageId: string | null };
      try {
        receipt = await deps.sendSmtpEmail(config, {
          to: delivery.email, subject: email.subject, text: email.text, html: email.html,
          messageId: `${transactionId}@globalinfectiousdisease.com`, unsubscribeUrl,
        });
      } catch (error) {
        const message = boundedText(error instanceof Error ? error.message : "smtp_delivery_failed", "smtp_delivery_failed", 500);
        const rejected = /_rejected:([45])\d\d$/.exec(message);
        const retry = rejected?.[1] === "4" && delivery.attempts < 5;
        const status = retry ? "queued" : rejected ? "failed" : "unknown";
        const nextAttempt = retry ? new Date(Date.now() + Math.min(3600, 30 * 2 ** (delivery.attempts - 1)) * 1000).toISOString() : null;
        const failedAt = new Date().toISOString();
        await env.DB.batch([
          env.DB.prepare(
            `UPDATE message_deliveries SET status = ?, last_error = ?, failed_at = ?, next_attempt_at = ?,
             claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`,
          ).bind(status, message, status === "failed" ? failedAt : null, nextAttempt, delivery.delivery_id, claimToken),
          env.DB.prepare(
            `UPDATE transactional_email_deliveries SET status = ?, error_code = ?, error_message = ?, updated_at = ?
             WHERE id = ? AND status = 'queued'`,
          ).bind(status === "unknown" ? "unknown" : "failed", status === "unknown" ? "smtp_result_unknown" : "smtp_delivery_failed", message, failedAt, transactionId),
        ]);
        continue;
      }
      const providerMessageId = receipt?.providerMessageId || `${transactionId}@globalinfectiousdisease.com`;
      const sentAt = new Date().toISOString();
      // A failed commit leaves the claim in-flight; lease recovery makes it
      // unknown rather than incorrectly treating a possibly accepted mail as retryable.
      await env.DB.batch([
        env.DB.prepare(
          `UPDATE message_deliveries SET status = 'sent', provider = 'smtp', provider_message_id = ?,
           last_error = NULL, sent_at = ?, failed_at = NULL, claim_token = NULL, claim_expires_at = NULL
           WHERE id = ? AND status = 'sending' AND claim_token = ?`,
        ).bind(providerMessageId, sentAt, delivery.delivery_id, claimToken),
        env.DB.prepare(
          `UPDATE transactional_email_deliveries SET status = CASE WHEN status = 'queued' THEN 'sent' ELSE status END,
           sent_at = ?, provider_message_id = ?, updated_at = ? WHERE id = ?`,
        ).bind(sentAt, providerMessageId, sentAt, transactionId),
      ]);
    }
    const state = await refreshStatus(env, campaignId);
    return json({ ok: true, campaign_id: campaignId, processed, status: state.status, progress: state.progress }, request, env);
  }

  async function drainDue(env: Env): Promise<void> {
    if (!env.ADMIN_API_TOKEN) return;
    const now = new Date().toISOString();
    const policy = recipientSql(env, "contact.address");
    const due = await env.DB.prepare(
      `SELECT c.id FROM message_campaigns c
       WHERE c.trigger_type = 'admin_notification' AND c.processing_started_at IS NOT NULL
         AND c.status IN ('queued', 'sending')
         AND EXISTS (SELECT 1 FROM message_deliveries d JOIN subscriber_contacts contact ON contact.id=d.contact_id WHERE d.campaign_id = c.id AND ${policy.sql} AND (
           (d.status = 'queued' AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?))
           OR (d.status = 'sending' AND d.claim_expires_at < ?)
         )) ORDER BY c.created_at ASC LIMIT 1`,
    ).bind(...policy.binds, now, now).all<{ id: string }>();
    for (const campaign of due.results || []) {
      const request = new Request(`${env.PUBLIC_BASE_URL || "https://subscriptions.invalid"}/api/admin/notifications/${campaign.id}/process`, {
        method: "POST", headers: { authorization: `Bearer ${env.ADMIN_API_TOKEN}`, "content-type": "application/json" },
        body: JSON.stringify({ batch_size: 5 }),
      });
      const response = await process(request, env, campaign.id);
      if (!response.ok) console.error(JSON.stringify({ message: "campaign_scheduled_processing_failed", status: response.status, campaign_id: campaign.id }));
    }
  }

  return { list, get, create, process, drainDue };
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
  items: ContentItem[] | null = null,
) {
  const clauses = ["s.status = 'active'", "c.channel = 'email'", "c.status = 'active'", "sub.status = 'active'"];
  const binds: unknown[] = [];
  const policy = recipientSql(env, "c.address");
  clauses.push(policy.sql); binds.push(...policy.binds);
  if (codes.length) { clauses.push(`l.code IN (${codes.map(() => "?").join(", ")})`); binds.push(...codes); }
  if (frequency) { clauses.push("s.frequency = ?"); binds.push(frequency); }
  const grouped = new Map<string, string[]>();
  for (const filter of filters) {
    if (items && CONTENT_FILTER_TYPES.has(filter.type)) continue;
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
  if (items) {
    // A subscriber matches when one item satisfies every selected filter
    // dimension. Values within one dimension are alternatives (OR). Missing
    // item metadata does not match a subscriber's explicit preference.
    clauses.push(`EXISTS (
      SELECT 1 FROM json_each(?) AS item
      WHERE NOT EXISTS (
        SELECT f.filter_type FROM subscription_filters f
        WHERE f.subscription_id = s.id
          AND f.filter_type IN ('country', 'disease', 'research_topic', 'study_type', 'peer_review_status')
        GROUP BY f.filter_type
        HAVING NOT EXISTS (
          SELECT 1 FROM subscription_filters matched
          JOIN json_each(item.value, '$.filters') AS fact
            ON json_extract(fact.value, '$.type') = matched.filter_type
           AND json_extract(fact.value, '$.value') = matched.filter_value
          WHERE matched.subscription_id = s.id AND matched.filter_type = f.filter_type
        )
      )
    )`);
    binds.push(JSON.stringify(items));
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
