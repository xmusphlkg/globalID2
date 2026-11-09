import { boundedText, isRecord, normalizeEmail, normalizeLocale, valueAsString } from "../lib/input.ts";
import { HttpError, htmlPage, json, publicBaseUrl, type JsonValue } from "../lib/http.ts";
import { normalizeFilters, normalizeListCodes, pendingCutoff } from "../lib/subscriptions.ts";
import { sha256Hex } from "../lib/security.ts";
import type { EmailSubscriptionItem } from "../lib/email.ts";
import type { Env, Payload } from "../types.ts";

const TTL = 7 * 24 * 60 * 60;
const FREQUENCIES: Record<string, string> = { alerts: "instant", reports: "weekly", weekly_digest: "weekly", research_digest: "weekly" };
type Deps = {
  readPayload(request: Request): Promise<Payload>;
  verifyTurnstileIfConfigured(payload: Payload, request: Request, env: Env): Promise<void>;
  enforceSubmissionRateLimit(request: Request, env: Env): Promise<void>;
  signingSecret(env: Env): string;
  createSignedToken(env: Env, kind: "confirm" | "unsubscribe", id: string, ttl: number): Promise<string>;
  verifySignedToken(env: Env, token: string, kind: "confirm" | "unsubscribe"): Promise<{ subscriptionId: string } | null>;
  sendAndRecordConfirmationEmail(env: Env, request: Request, input: {
    email: string; locale: string; subscriberId: string; contactId: string; source: string; subscriptions: EmailSubscriptionItem[];
    outboxId: string; outboxClaimToken: string;
  }): Promise<Record<string, JsonValue | undefined>>;
  recordEvent(env: Env, input: { subscriberId?: string; subscriptionId?: string; eventType: string; actorType: string; request: Request; metadata?: JsonValue }): Promise<void>;
  skipPendingSituationAlertsForSubscription(env: Env, subscriptionId: string): Promise<void>;
};
type Change = { id: string; subscription_id: string; subscriber_id: string; contact_id: string; locale: string; timezone: string; frequency: string; source: string; filters_json: string };

function consentPage(kind: "confirm" | "unsubscribe", token: string, locale: string): Response {
  const zh = locale === "zh";
  const heading = kind === "confirm"
    ? (zh ? "确认你的 GIDS 订阅" : "Confirm your GIDS subscription")
    : (zh ? "退订此 GIDS 订阅？" : "Unsubscribe from this GIDS list?");
  const button = kind === "confirm" ? (zh ? "确认订阅" : "Confirm subscription") : (zh ? "确认退订" : "Unsubscribe");
  const action = `/api/subscriptions/${kind}?token=${encodeURIComponent(token)}`;
  return new Response(`<!doctype html><html lang="${zh ? "zh" : "en"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${heading} | GIDS</title><style>body{margin:0;font-family:Arial,sans-serif;background:#f8fafc;color:#0f172a}main{max-width:560px;margin:64px auto;padding:28px;background:white;border:1px solid #dbe5e1}h1{font-size:24px}button{background:#0f766e;color:white;border:0;padding:12px 18px;font:inherit;cursor:pointer}</style></head><body><main><h1>${heading}</h1><form method="post" action="${action}"><button type="submit">${button}</button></form></main></body></html>`, {
    headers: { "content-type": "text/html; charset=utf-8", "referrer-policy": "no-referrer", "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'" },
  });
}

function resultPage(kind: "confirm" | "unsubscribe", locale: string): Response {
  const zh = locale === "zh";
  return kind === "confirm"
    ? htmlPage(zh ? "订阅已确认" : "Subscription confirmed", zh ? "你的 GIDS 订阅已生效。" : "Your GIDS subscription is active.", zh ? "zh" : "en")
    : htmlPage(zh ? "已退订" : "Unsubscribed", zh ? "此项 GIDS 订阅已停止。" : "This GIDS subscription has been stopped.", zh ? "zh" : "en");
}

export function createSubscriptionLifecycleHandlers(deps: Deps) {
  async function createSubscription(request: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
    const payload = await deps.readPayload(request);
    await deps.verifyTurnstileIfConfigured(payload, request, env);
    const email = normalizeEmail(valueAsString(payload.email));
    if (!email) throw new HttpError(400, "valid_email_required");
    if (payload.consent !== true && payload.consent !== "on" && payload.consent !== "true") throw new HttpError(400, "consent_required");
    await deps.enforceSubmissionRateLimit(request, env);
    const now = new Date().toISOString();
    const expires = new Date(Date.now() + TTL * 1000).toISOString();
    const locale = normalizeLocale(valueAsString(payload.locale), "en");
    if (!["en", "zh"].includes(locale)) throw new HttpError(400, "unsupported_locale");
    const timezone = boundedText(valueAsString(payload.timezone), "UTC", 80);
    try { new Intl.DateTimeFormat("en", { timeZone: timezone }); } catch { throw new HttpError(400, "invalid_timezone"); }
    const source = boundedText(valueAsString(payload.source), "website", 80);
    const codes = normalizeListCodes(payload);
    const filters = normalizeFilters(payload);
    if (codes.length > 10 || filters.length > 250) throw new HttpError(400, "too_many_preferences");
    const perList = isRecord(payload.list_frequencies) ? payload.list_frequencies : {};
    const global = valueAsString(payload.frequency).trim().toLowerCase();
    const lists: Array<{ code: string; id: string; frequency: string }> = [];
    for (const code of codes) {
      const list = await env.DB.prepare("SELECT id FROM subscription_lists WHERE code = ? AND is_public = 1").bind(code).first<{ id: string }>();
      if (!list) throw new HttpError(400, `unknown_list:${code}`);
      const supported = FREQUENCIES[code];
      if (!supported) throw new HttpError(400, `unsupported_list:${code}`);
      const available = code === "research_digest" ? env.RESEARCH_DIGEST_ENABLED === "true"
        : code === "alerts" ? env.NOTICE_ALERTS_ENABLED === "true"
        : env.REPORT_DIGEST_ENABLED === "true";
      if (!available) throw new HttpError(400, `subscription_unavailable:${code}`);
      const frequency = valueAsString(perList[code]).trim().toLowerCase() || global || supported;
      if (frequency !== supported) throw new HttpError(400, `unsupported_frequency:${code}`);
      lists.push({ code, id: list.id, frequency });
    }
    if (filters.length) {
      const invalid = await env.DB.prepare(`SELECT COUNT(*) AS count FROM json_each(?) AS f WHERE NOT EXISTS (
        SELECT 1 FROM subscription_filter_options o WHERE o.filter_type = json_extract(f.value, '$.type')
        AND o.filter_value = json_extract(f.value, '$.value') AND o.is_public = 1)`)
        .bind(JSON.stringify(filters)).first<{ count: number }>();
      if (Number(invalid?.count || 0) > 0) throw new HttpError(400, "unknown_filter");
    }
    const hash = await sha256Hex(`${deps.signingSecret(env)}:email:${email}`);
    const newSubscriber = crypto.randomUUID();
    const newContact = crypto.randomUUID();
    await env.DB.batch([
      env.DB.prepare(`INSERT INTO subscribers (id, locale, timezone, status, created_at, updated_at)
        SELECT ?, ?, ?, 'active', ?, ? WHERE NOT EXISTS (SELECT 1 FROM subscriber_contacts WHERE channel = 'email' AND address_hash = ?)`)
        .bind(newSubscriber, locale, timezone, now, now, hash),
      env.DB.prepare(`INSERT INTO subscriber_contacts (id, subscriber_id, channel, address, address_hash, status, created_at, updated_at)
        SELECT ?, ?, 'email', ?, ?, 'pending', ?, ? WHERE NOT EXISTS (SELECT 1 FROM subscriber_contacts WHERE channel = 'email' AND address_hash = ?)`)
        .bind(newContact, newSubscriber, email, hash, now, now, hash),
    ]);
    const contact = await env.DB.prepare("SELECT id, subscriber_id, status FROM subscriber_contacts WHERE channel = 'email' AND address_hash = ?")
      .bind(hash).first<{ id: string; subscriber_id: string; status: string }>();
    if (!contact) throw new HttpError(500, "subscriber_contact_not_created");
    const subscriberId = contact.subscriber_id, contactId = contact.id;
    const publicResult = { ok: true, status: "pending", message: "subscription_created_pending_confirmation",
      subscriptions: [] as JsonValue[], email: { status: "pending", provider: "smtp" } };
    if (contact.status === "suppressed") return json(publicResult, request, env, 201);
    const created: JsonValue[] = [], requestIds: string[] = [];
    const statements: D1PreparedStatement[] = [];
    for (const list of lists) {
      await env.DB.prepare(`INSERT INTO subscriptions (id, subscriber_id, contact_id, list_id, status, frequency, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?) ON CONFLICT(subscriber_id, contact_id, list_id) DO NOTHING`)
        .bind(crypto.randomUUID(), subscriberId, contactId, list.id, list.frequency, source, now, now).run();
      const subscription = await env.DB.prepare("SELECT id, status FROM subscriptions WHERE subscriber_id = ? AND contact_id = ? AND list_id = ?")
        .bind(subscriberId, contactId, list.id).first<{ id: string; status: string }>();
      if (!subscription) throw new HttpError(500, "subscription_not_created");
      const changeId = crypto.randomUUID();
      const listFilters = filters.filter(f => ["country", "disease"].includes(f.type) || list.code === "research_digest");
      statements.push(env.DB.prepare(`INSERT INTO subscription_change_requests (id, subscription_id, subscriber_id, contact_id, locale, timezone, frequency,
        source, filters_json, consent_version, status, created_at, expires_at)
        SELECT ?, s.id, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ? FROM subscriptions s WHERE s.id = ? AND s.status = ?
          AND EXISTS (SELECT 1 FROM subscriber_contacts c WHERE c.id = s.contact_id AND c.status <> 'suppressed')`)
        .bind(changeId, subscriberId, contactId, locale, timezone, list.frequency, source, JSON.stringify(listFilters), "service-terms-2026-08-18", now, expires,
          subscription.id, subscription.status));
      const token = await deps.createSignedToken(env, "confirm", changeId, TTL);
      const confirmUrl = `${publicBaseUrl(request, env)}/api/subscriptions/confirm?token=${token}`;
      const item: Record<string, JsonValue> = { id: subscription.id, list_code: list.code, status: "pending" };
      if (env.DEBUG_RETURN_TOKENS === "true") item.confirm_url = confirmUrl;
      created.push(item);
      requestIds.push(changeId);
    }
    statements.push(env.DB.prepare(`INSERT INTO confirmation_email_outbox (id, subscriber_id, contact_id, locale, source,
      request_ids_json, status, attempts, next_attempt_at, created_at, updated_at)
      SELECT ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ? WHERE
      (SELECT COUNT(*) FROM subscription_change_requests WHERE id IN (${requestIds.map(() => "?").join(",")})) = ?`)
      .bind(crypto.randomUUID(), subscriberId, contactId, locale, source, JSON.stringify(requestIds), now, now, now, ...requestIds, requestIds.length));
    const inserted = await env.DB.batch(statements);
    if (inserted.some(result => !result.meta.changes)) throw new HttpError(409, "subscription_changed_retry");
    try {
      await deps.recordEvent(env, { subscriberId, eventType: "subscription_requested", actorType: "subscriber", request,
        metadata: { list_codes: codes, consent_version: "service-terms-2026-08-18" } });
    } catch (error) {
      console.error(JSON.stringify({ message: "subscription_request_event_failed", error: error instanceof Error ? error.message : String(error) }));
    }
    if (ctx) ctx.waitUntil(drainConfirmationOutbox(env).catch(error => {
      console.error(JSON.stringify({ message: "confirmation_outbox_failed", error: error instanceof Error ? error.message : String(error) }));
    }));
    return json(env.DEBUG_RETURN_TOKENS === "true" ? { ...publicResult, subscriptions: created } : publicResult, request, env, 201);
  }

  async function confirmSubscription(request: Request, env: Env): Promise<Response> {
    const token = new URL(request.url).searchParams.get("token") || "";
    const parsed = await deps.verifySignedToken(env, token, "confirm");
    if (!parsed) throw new HttpError(400, "invalid_or_expired_token");
    if (request.method === "GET") {
      const pending = await env.DB.prepare("SELECT locale FROM subscription_change_requests WHERE id = ?")
        .bind(parsed.subscriptionId).first<{ locale: string }>();
      const legacy = pending ? null : await env.DB.prepare(`SELECT sub.locale FROM subscriptions s
        JOIN subscribers sub ON sub.id = s.subscriber_id WHERE s.id = ?`).bind(parsed.subscriptionId).first<{ locale: string }>();
      return consentPage("confirm", token, pending?.locale || legacy?.locale || "en");
    }
    const change = await env.DB.prepare(`SELECT id, subscription_id, subscriber_id, contact_id, locale, timezone, frequency, source, filters_json
      FROM subscription_change_requests WHERE id = ?`).bind(parsed.subscriptionId).first<Change>();
    if (!change) return confirmLegacy(request, env, parsed.subscriptionId);
    const now = new Date().toISOString(), nonce = crypto.randomUUID();
    let filters: Array<{ type: string; value: string }>;
    try {
      const values: unknown = JSON.parse(change.filters_json);
      if (!Array.isArray(values) || !values.every(x => isRecord(x) && typeof x.type === "string" && typeof x.value === "string")) throw new Error();
      filters = values as Array<{ type: string; value: string }>;
    } catch { throw new HttpError(500, "invalid_stored_preferences"); }
    const guard = "EXISTS (SELECT 1 FROM subscription_change_requests r WHERE r.id = ? AND r.status = 'confirmed' AND r.consume_nonce = ?)";
    const results = await env.DB.batch([
      env.DB.prepare(`UPDATE subscription_change_requests SET status = 'confirmed', confirmed_at = ?, consume_nonce = ?
        WHERE id = ? AND status = 'pending' AND expires_at > ?
        AND EXISTS (SELECT 1 FROM subscriber_contacts c WHERE c.id = contact_id AND c.status IN ('pending', 'active', 'expired'))
        AND EXISTS (SELECT 1 FROM subscribers sub WHERE sub.id = subscriber_id AND sub.status = 'active')`).bind(now, nonce, change.id, now),
      env.DB.prepare(`UPDATE subscriptions SET status = 'active', frequency = ?, source = ?, confirmed_at = ?, updated_at = ?, management_generation = ?, preference_revision = ? WHERE id = ? AND ${guard}`)
        .bind(change.frequency, change.source, now, now, crypto.randomUUID(), crypto.randomUUID(), change.subscription_id, change.id, nonce),
      env.DB.prepare(`UPDATE subscribers SET locale = ?, timezone = ?, updated_at = ? WHERE id = ? AND ${guard}`)
        .bind(change.locale, change.timezone, now, change.subscriber_id, change.id, nonce),
      env.DB.prepare(`UPDATE subscriber_contacts SET status = 'active', verified_at = COALESCE(verified_at, ?), updated_at = ? WHERE id = ? AND ${guard}`)
        .bind(now, now, change.contact_id, change.id, nonce),
      env.DB.prepare(`DELETE FROM subscription_filters WHERE subscription_id = ? AND ${guard}`).bind(change.subscription_id, change.id, nonce),
      ...filters.map(f => env.DB.prepare(`INSERT INTO subscription_filters (id, subscription_id, filter_type, filter_value, created_at)
        SELECT ?, ?, ?, ?, ? WHERE ${guard} ON CONFLICT(subscription_id, filter_type, filter_value) DO NOTHING`)
        .bind(crypto.randomUUID(), change.subscription_id, f.type, f.value, now, change.id, nonce)),
      env.DB.prepare(`UPDATE subscription_change_requests SET status = 'invalidated' WHERE subscription_id = ? AND id <> ? AND status = 'pending' AND ${guard}`)
        .bind(change.subscription_id, change.id, change.id, nonce),
    ]);
    if (!results[0]?.meta.changes) throw new HttpError(400, "invalid_or_expired_token");
    await deps.recordEvent(env, { subscriberId: change.subscriber_id, subscriptionId: change.subscription_id, eventType: "subscription_confirmed", actorType: "subscriber", request });
    return resultPage("confirm", change.locale);
  }

  async function confirmLegacy(request: Request, env: Env, subscriptionId: string): Promise<Response> {
    // Old links can only confirm a never-confirmed pending record, never undo an unsubscribe or suppression.
    const old = await env.DB.prepare(`SELECT s.id, s.subscriber_id, s.contact_id, sub.locale FROM subscriptions s
      JOIN subscriber_contacts c ON c.id = s.contact_id JOIN subscribers sub ON sub.id = s.subscriber_id
      WHERE s.id = ? AND s.status = 'pending' AND s.confirmed_at IS NULL AND c.status IN ('pending', 'active') AND sub.status = 'active'`)
      .bind(subscriptionId).first<{ id: string; subscriber_id: string; contact_id: string; locale: string }>();
    if (!old) throw new HttpError(400, "invalid_or_expired_token");
    const now = new Date().toISOString(), marker = `legacy:${subscriptionId}`, nonce = crypto.randomUUID();
    const results = await env.DB.batch([
      env.DB.prepare(`INSERT INTO subscription_change_requests (id, subscription_id, subscriber_id, contact_id, locale, timezone, frequency,
        source, filters_json, consent_version, status, created_at, expires_at, confirmed_at, consume_nonce)
        SELECT ?, s.id, s.subscriber_id, s.contact_id, sub.locale, sub.timezone, s.frequency, 'legacy', '[]', 'legacy', 'confirmed', ?, ?, ?, ?
        FROM subscriptions s JOIN subscriber_contacts c ON c.id = s.contact_id JOIN subscribers sub ON sub.id = s.subscriber_id
        WHERE s.id = ? AND s.status = 'pending' AND s.confirmed_at IS NULL AND c.status IN ('pending', 'active') AND sub.status = 'active'
        ON CONFLICT(id) DO NOTHING`).bind(marker, now, now, now, nonce, subscriptionId),
      env.DB.prepare(`UPDATE subscriptions SET status = 'active', confirmed_at = ?, updated_at = ?, management_generation = ?, preference_revision = ? WHERE id = ?
        AND EXISTS (SELECT 1 FROM subscription_change_requests WHERE id = ? AND consume_nonce = ?)`)
        .bind(now, now, crypto.randomUUID(), crypto.randomUUID(), subscriptionId, marker, nonce),
      env.DB.prepare(`UPDATE subscriber_contacts SET status = 'active', verified_at = COALESCE(verified_at, ?), updated_at = ? WHERE id = ?
        AND EXISTS (SELECT 1 FROM subscription_change_requests WHERE id = ? AND consume_nonce = ?)`).bind(now, now, old.contact_id, marker, nonce),
      env.DB.prepare(`UPDATE subscription_change_requests SET status = 'invalidated' WHERE subscription_id = ? AND status = 'pending'
        AND EXISTS (SELECT 1 FROM subscription_change_requests WHERE id = ? AND consume_nonce = ?)`).bind(subscriptionId, marker, nonce),
    ]);
    if (!results[0]?.meta.changes) throw new HttpError(400, "invalid_or_expired_token");
    await deps.recordEvent(env, { subscriberId: old.subscriber_id, subscriptionId, eventType: "subscription_confirmed", actorType: "subscriber", request });
    return resultPage("confirm", old.locale);
  }

  async function unsubscribe(request: Request, env: Env): Promise<Response> {
    const token = new URL(request.url).searchParams.get("token") || "";
    const parsed = await deps.verifySignedToken(env, token, "unsubscribe");
    if (!parsed) throw new HttpError(400, "invalid_or_expired_token");
    const subscription = await env.DB.prepare(`SELECT s.id, s.subscriber_id, sub.locale FROM subscriptions s
      JOIN subscribers sub ON sub.id = s.subscriber_id WHERE s.id = ?`).bind(parsed.subscriptionId)
      .first<{ id: string; subscriber_id: string; locale: string }>();
    if (!subscription) throw new HttpError(404, "subscription_not_found");
    if (request.method === "GET") {
      return consentPage("unsubscribe", token, subscription.locale);
    }
    const now = new Date().toISOString();
    await env.DB.batch([
      env.DB.prepare("UPDATE subscriptions SET status = 'unsubscribed', updated_at = ?, management_generation = ?, preference_revision = ? WHERE id = ?")
        .bind(now, crypto.randomUUID(), crypto.randomUUID(), subscription.id),
      env.DB.prepare("UPDATE subscription_change_requests SET status = 'invalidated' WHERE subscription_id = ? AND status = 'pending'").bind(subscription.id),
      env.DB.prepare("UPDATE message_deliveries SET status = 'skipped', last_error = 'subscription_unsubscribed' WHERE subscription_id = ? AND status IN ('queued', 'retry')").bind(subscription.id),
    ]);
    await deps.skipPendingSituationAlertsForSubscription(env, subscription.id);
    await deps.recordEvent(env, { subscriberId: subscription.subscriber_id, subscriptionId: subscription.id, eventType: "subscription_unsubscribed", actorType: "subscriber", request });
    return resultPage("unsubscribe", subscription.locale);
  }

  async function drainConfirmationOutbox(env: Env): Promise<void> {
    const now = new Date().toISOString();
    // An interrupted SMTP exchange may already have been accepted; recovering
    // it as unknown avoids an automatic duplicate send.
    await env.DB.prepare(`UPDATE confirmation_email_outbox SET status = 'unknown', last_error = 'send_outcome_unknown',
      claim_token = NULL, claim_expires_at = NULL, updated_at = ?
      WHERE status = 'sending' AND claim_expires_at <= ?`).bind(now, now).run();
    const due = await env.DB.prepare(`SELECT o.id FROM confirmation_email_outbox o JOIN subscriber_contacts c ON c.id=o.contact_id
      WHERE o.status IN ('queued', 'retry') AND o.next_attempt_at <= ? ORDER BY o.created_at LIMIT 1`)
      .bind(now).all<{ id: string }>();
    for (const candidate of due.results || []) {
      const nonce = crypto.randomUUID();
      const lease = new Date(Date.now() + 120_000).toISOString();
      const job = await env.DB.prepare(`UPDATE confirmation_email_outbox SET status = 'sending', attempts = attempts + 1,
        claim_token = ?, claim_expires_at = ?, updated_at = ? WHERE id = ? AND status IN ('queued', 'retry') AND next_attempt_at <= ?
        RETURNING id, subscriber_id, contact_id, locale, source, request_ids_json, attempts`)
        .bind(nonce, lease, now, candidate.id, now)
        .first<{ id: string; subscriber_id: string; contact_id: string; locale: string; source: string; request_ids_json: string; attempts: number }>();
      if (!job) continue;
      const update = async (status: string, reason: string | null, next: string | null) => {
        await env.DB.prepare(`UPDATE confirmation_email_outbox SET status = ?, last_error = ?, next_attempt_at = ?,
          claim_token = NULL, claim_expires_at = NULL, updated_at = ? WHERE id = ? AND status = 'sending' AND claim_token = ?`)
          .bind(status, reason, next, new Date().toISOString(), job.id, nonce).run();
      };
      try {
        const ids: unknown = JSON.parse(job.request_ids_json);
        if (!Array.isArray(ids) || !ids.length || ids.length > 10 || !ids.every(id => typeof id === "string")) {
          await update("failed", "invalid_request_ids", null);
          continue;
        }
        const contact = await env.DB.prepare("SELECT address, status FROM subscriber_contacts WHERE id = ?")
          .bind(job.contact_id).first<{ address: string; status: string }>();
        if (!contact || !["active", "pending", "expired"].includes(contact.status)) {
          await update("cancelled", "contact_inactive", null);
          continue;
        }
        const pending = await env.DB.prepare(`SELECT r.id, r.subscription_id, r.frequency, l.code
          FROM subscription_change_requests r JOIN subscriptions s ON s.id = r.subscription_id
          JOIN subscription_lists l ON l.id = s.list_id JOIN subscribers sub ON sub.id = r.subscriber_id
          WHERE r.id IN (${ids.map(() => "?").join(",")}) AND r.contact_id = ?
            AND r.status = 'pending' AND r.expires_at > ? AND sub.status = 'active'`)
          .bind(...ids, job.contact_id, new Date().toISOString())
          .all<{ id: string; subscription_id: string; frequency: string; code: string }>();
        if (!pending.results?.length) {
          await update("cancelled", "request_inactive", null);
          continue;
        }
        const baseUrl = env.PUBLIC_BASE_URL || "";
        if (!baseUrl) {
          await update("retry", "public_url_not_configured", new Date(Date.now() + 300_000).toISOString());
          continue;
        }
        const messageRequest = new Request(baseUrl);
        const subscriptions: EmailSubscriptionItem[] = await Promise.all(pending.results.map(async row => ({
          id: row.subscription_id, listCode: row.code, frequency: row.frequency,
          confirmUrl: `${baseUrl.replace(/\/+$/, "")}/api/subscriptions/confirm?token=${await deps.createSignedToken(env, "confirm", row.id, TTL)}`,
        })));
        const result = await deps.sendAndRecordConfirmationEmail(env, messageRequest, {
          email: contact.address, locale: job.locale, subscriberId: job.subscriber_id, contactId: job.contact_id,
          source: job.source, subscriptions, outboxId: job.id, outboxClaimToken: nonce,
        });
        if (result.status === "sent") await update("sent", null, null);
        else if (result.reason === "smtp_not_configured" || result.reason === "confirmation_rate_limited" || result.retryable === true) {
          if (job.attempts >= 12) await update("failed", String(result.reason || "retry_exhausted"), null);
          else await update("retry", String(result.reason || "temporary_failure"), new Date(Date.now() + 600_000).toISOString());
        } else if (result.permanent_failure === true) await update("failed", String(result.reason || "smtp_delivery_failed"), null);
        else await update("unknown", String(result.reason || "send_outcome_unknown"), null);
      } catch (error) {
        // Without a receipt it is unsafe to assert that no message was sent.
        await update("unknown", "send_outcome_unknown", null);
        console.error(JSON.stringify({ message: "confirmation_delivery_unknown", outbox_id: job.id,
          error: error instanceof Error ? error.message : String(error) }));
      }
    }
  }

  async function expirePending(env: Env) {
    const now = new Date().toISOString(), cutoff = pendingCutoff(env.PENDING_EXPIRY_DAYS);
    const results = await env.DB.batch([
      env.DB.prepare("UPDATE subscription_change_requests SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?").bind(now),
      env.DB.prepare(`UPDATE subscriptions SET status = 'expired', updated_at = ? WHERE status = 'pending' AND created_at < ?
        AND NOT EXISTS (SELECT 1 FROM subscription_change_requests r WHERE r.subscription_id = subscriptions.id AND r.status = 'pending' AND r.expires_at > ?)`)
        .bind(now, cutoff, now),
      env.DB.prepare(`UPDATE subscriber_contacts SET status = 'expired', updated_at = ? WHERE status = 'pending' AND created_at < ?
        AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.contact_id = subscriber_contacts.id AND s.status IN ('pending', 'active'))
        AND NOT EXISTS (SELECT 1 FROM subscription_change_requests r WHERE r.contact_id = subscriber_contacts.id AND r.status = 'pending' AND r.expires_at > ?)`)
        .bind(now, cutoff, now),
    ]);
    return { cutoff, expired_subscriptions: Number(results[1]?.meta.changes || 0), expired_contacts: Number(results[2]?.meta.changes || 0) };
  }
  return { createSubscription, confirmSubscription, unsubscribe, expirePending, drainConfirmationOutbox };
}
