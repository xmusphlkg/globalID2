import {
  SUPPORTED_LOCALES,
  boundedText,
  normalizeCode,
  normalizeEmail,
  normalizeLocale,
  valueAsString,
} from "./lib/input.ts";
import {
  sha256Hex,
  signSubscriptionToken,
  secureTextEqual,
  verifySubscriptionToken,
} from "./lib/security.ts";
import {
  buildConfirmationEmail,
  type EmailSubscriptionItem,
} from "./lib/email.ts";
import { sendSmtpEmail, smtpConfig } from "./lib/smtp.ts";
import {
  HttpError,
  configInt,
  corsHeaders,
  htmlPage,
  json,
  pick,
  publicBaseUrl,
  requireAdmin,
  type JsonValue,
} from "./lib/http.ts";
import { confirmationRateLimit, submissionRateLimited } from "./lib/rate-limit.ts";
import { matchWorkerRoute } from "./lib/router.ts";
import {
  insertEmailDelivery as insertEmailDeliveryRecord,
  updateEmailDelivery as updateEmailDeliveryRecord,
} from "./lib/db.ts";
import {
  FREQUENCIES,
  LOCALE_LABELS,
  SUBSCRIPTION_STATUSES,
  escapeLike,
  maskEmail,
  normalizeFilter,
  normalizeFilters,
  normalizeListCodes,
  parseFilterGroups,
  pendingCutoff,
  rowsToCounts,
} from "./lib/subscriptions.ts";
import { createCampaignHandlers } from "./handlers/campaigns.ts";
import {
  listAudience,
  listSubscriptionOptions,
  listSubscriptionsAdmin,
  subscriptionStats,
} from "./handlers/subscriptions.ts";
import {
  consumeSituationAlertBatch,
  createSituationAlertHandlers,
  maintainSituationAlerts,
  processSituationAlertOutbox,
  skipPendingSituationAlertsForSubscription,
} from "./handlers/situation-alerts.ts";
import type { Env, Payload } from "./types.ts";
import type { SituationAlertJob } from "./lib/situation-alert.ts";
import { applyDeliveryFeedback, normalizeDeliveryFeedback } from "./lib/delivery-feedback.ts";
interface EmailDeliveryResult extends Record<string, JsonValue | undefined> {
  status: "sent" | "failed" | "skipped";
  provider: "smtp";
  delivery_id?: string;
  recipient?: string;
  from?: string;
  reason?: string;
  message?: string;
}

const campaignHandlers = createCampaignHandlers({
  readPayload,
  createSignedToken,
  smtpConfig,
  sendSmtpEmail,
});
const subscriptionHandlerDependencies = { readPayload, createSignedToken };
const situationAlertHandlers = createSituationAlertHandlers({
  createSignedToken,
  smtpConfig,
  sendSmtpEmail,
});

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    }

    try {
      const url = new URL(request.url);
      const route = matchWorkerRoute(request.method, url.pathname);
      switch (route.name) {
        case "health":
          return json({ ok: true, service: "globalid-subscriptions" }, request, env);
        case "subscription_options":
          return listSubscriptionOptions(request, env);
        case "create_subscription":
          return createSubscription(request, env);
        case "confirm_subscription":
          return confirmSubscription(request, env);
        case "unsubscribe":
          return unsubscribe(request, env);
        case "situation_alert_ingest":
          return situationAlertHandlers.ingest(request, env);
        case "email_delivery_feedback":
          return recordDeliveryFeedback(request, env);
        case "admin_audience":
          return listAudience(request, env, subscriptionHandlerDependencies);
        case "admin_stats":
          return subscriptionStats(request, env);
        case "admin_subscriptions":
          return listSubscriptionsAdmin(request, env);
        case "admin_notifications":
          return route.operation === "list"
            ? campaignHandlers.list(request, env)
            : campaignHandlers.create(request, env);
        case "admin_notification":
          return route.operation === "get"
            ? campaignHandlers.get(request, env, route.campaignId)
            : campaignHandlers.process(request, env, route.campaignId);
        case "admin_situation_alerts":
          return route.operation === "list"
            ? situationAlertHandlers.list(request, env)
            : situationAlertHandlers.process(request, env);
        case "admin_maintenance":
          requireAdmin(request, env);
          return json(await runMaintenance(env), request, env);
        case "not_found":
          return json({ error: "not_found" }, request, env, 404);
      }
    } catch (error) {
      const message = error instanceof HttpError ? error.message : "internal_error";
      const status = error instanceof HttpError ? error.status : 500;
      if (!(error instanceof HttpError) || status >= 500) {
        console.error(JSON.stringify({
          message: "subscription_worker_request_failed",
          path: new URL(request.url).pathname,
          status,
          error: error instanceof Error ? error.message : String(error),
        }));
      }
      return json({ error: message }, request, env, status);
    }
  },

  async scheduled(_controller: ScheduledController, env: Env, _ctx: ExecutionContext): Promise<void> {
    const tasks = [
      { name: "maintenance", run: () => runMaintenance(env) },
      {
        name: "situation_alert_outbox",
        run: () => processSituationAlertOutbox(env, {
          createSignedToken,
          smtpConfig,
          sendSmtpEmail,
        }),
      },
    ];
    for (const task of tasks) {
      try {
        await task.run();
      } catch (error) {
        console.error(JSON.stringify({
          message: "subscription_worker_scheduled_task_failed",
          task: task.name,
          error: error instanceof Error ? error.message : String(error),
        }));
      }
    }
  },

  async queue(
    batch: MessageBatch<SituationAlertJob>,
    env: Env,
    _ctx: ExecutionContext,
  ): Promise<void> {
    await consumeSituationAlertBatch(batch, env, {
      createSignedToken,
      smtpConfig,
      sendSmtpEmail,
    });
  },
} satisfies ExportedHandler<Env, SituationAlertJob>;

async function createSubscription(request: Request, env: Env): Promise<Response> {
  const payload = await readPayload(request);
  await verifyTurnstileIfConfigured(payload, request, env);

  const email = normalizeEmail(valueAsString(payload.email));
  if (!email) {
    throw new HttpError(400, "valid_email_required");
  }

  await enforceSubmissionRateLimit(request, env);

  const now = new Date().toISOString();
  const locale = normalizeLocale(valueAsString(payload.locale), "en");
  const timezone = boundedText(valueAsString(payload.timezone), "UTC", 80);
  const requestedFrequency = valueAsString(payload.frequency).trim().toLowerCase();
  const frequency = FREQUENCIES.has(requestedFrequency) ? requestedFrequency : "";
  const source = boundedText(valueAsString(payload.source), "website", 80);
  const listCodes = normalizeListCodes(payload);
  const filters = normalizeFilters(payload);
  const addressHash = await sha256Hex(`${signingSecret(env)}:email:${email}`);

  let contact = await env.DB.prepare(
    "SELECT id, subscriber_id FROM subscriber_contacts WHERE channel = ? AND address_hash = ?"
  ).bind("email", addressHash).first<{ id: string; subscriber_id: string }>();

  let subscriberId = contact?.subscriber_id;
  let contactId = contact?.id;

  if (!subscriberId || !contactId) {
    subscriberId = crypto.randomUUID();
    contactId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO subscribers (id, locale, timezone, status, created_at, updated_at)
       VALUES (?, ?, ?, 'active', ?, ?)`
    ).bind(subscriberId, locale, timezone, now, now).run();
    await env.DB.prepare(
      `INSERT INTO subscriber_contacts (
         id, subscriber_id, channel, address, address_hash, status, created_at, updated_at
       ) VALUES (?, ?, 'email', ?, ?, 'pending', ?, ?)`
    ).bind(contactId, subscriberId, email, addressHash, now, now).run();
  } else {
    await env.DB.prepare(
      "UPDATE subscribers SET locale = ?, timezone = ?, updated_at = ? WHERE id = ?"
    ).bind(locale, timezone, now, subscriberId).run();
    await env.DB.prepare(
      "UPDATE subscriber_contacts SET address = ?, updated_at = ? WHERE id = ?"
    ).bind(email, now, contactId).run();
  }

  if (!subscriberId || !contactId) {
    throw new HttpError(500, "subscriber_contact_not_created");
  }

  const createdSubscriptions: JsonValue[] = [];
  const emailSubscriptions: EmailSubscriptionItem[] = [];

  for (const code of listCodes) {
    const list = await env.DB.prepare(
      "SELECT id, default_frequency FROM subscription_lists WHERE code = ? AND is_public = 1"
    ).bind(code).first<{ id: string; default_frequency: string }>();

    if (!list) {
      throw new HttpError(400, `unknown_list:${code}`);
    }

    const existing = await env.DB.prepare(
      `SELECT id FROM subscriptions
       WHERE subscriber_id = ? AND contact_id = ? AND list_id = ?`
    ).bind(subscriberId, contactId, list.id).first<{ id: string }>();

    const subscriptionId = existing?.id ?? crypto.randomUUID();
    const effectiveFrequency = frequency || list.default_frequency || "weekly";

    if (existing) {
      await env.DB.prepare(
        `UPDATE subscriptions
         SET status = 'pending', frequency = ?, source = ?, updated_at = ?
         WHERE id = ?`
      ).bind(effectiveFrequency, source, now, subscriptionId).run();
      await env.DB.prepare("DELETE FROM subscription_filters WHERE subscription_id = ?")
        .bind(subscriptionId).run();
    } else {
      await env.DB.prepare(
        `INSERT INTO subscriptions (
           id, subscriber_id, contact_id, list_id, status, frequency, source, created_at, updated_at
         ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)`
      ).bind(subscriptionId, subscriberId, contactId, list.id, effectiveFrequency, source, now, now).run();
    }

    for (const filter of filters) {
      await env.DB.prepare(
        `INSERT INTO subscription_filters (id, subscription_id, filter_type, filter_value, created_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(subscription_id, filter_type, filter_value) DO NOTHING`
      ).bind(crypto.randomUUID(), subscriptionId, filter.type, filter.value, now).run();
    }

    await recordEvent(env, {
      subscriberId,
      subscriptionId,
      eventType: existing ? "subscription_updated" : "subscription_created",
      actorType: "subscriber",
      request,
      metadata: { list_code: code, frequency: effectiveFrequency, filters },
    });

    const confirmToken = await createSignedToken(env, "confirm", subscriptionId, 60 * 60 * 24 * 7);
    const confirmUrl = `${publicBaseUrl(request, env)}/api/subscriptions/confirm?token=${confirmToken}`;
    const item: Record<string, JsonValue> = {
      id: subscriptionId,
      list_code: code,
      status: "pending",
    };
    if (env.DEBUG_RETURN_TOKENS === "true") {
      item.confirm_url = confirmUrl;
    }
    createdSubscriptions.push(item);
    emailSubscriptions.push({
      id: subscriptionId,
      listCode: code,
      frequency: effectiveFrequency,
      confirmUrl,
    });
  }

  const emailDelivery = await sendAndRecordConfirmationEmail(env, request, {
    email,
    locale,
    subscriberId,
    contactId,
    source,
    subscriptions: emailSubscriptions,
  });

  return json({
    ok: true,
    status: "pending",
    message: "subscription_created_pending_confirmation",
    subscriptions: createdSubscriptions,
    email: emailDelivery,
  }, request, env, 201);
}

async function recordDeliveryFeedback(request: Request, env: Env): Promise<Response> {
  const expected = env.EMAIL_DELIVERY_INGEST_TOKEN || "";
  if (!expected) throw new HttpError(500, "EMAIL_DELIVERY_INGEST_TOKEN_not_configured");
  const authorization = request.headers.get("authorization") || "";
  const provided = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!(await secureTextEqual(provided, expected))) throw new HttpError(401, "unauthorized");
  const feedback = normalizeDeliveryFeedback(await readPayload(request));
  if (!feedback) throw new HttpError(400, "invalid_email_delivery_event");
  const result = await applyDeliveryFeedback(env.DB, feedback);
  return json({
    ok: true,
    duplicate: result.duplicate,
    matched_delivery_count: result.campaignRows + result.transactionalRows,
    suppressed_contact_count: result.suppressedContacts,
    event_type: feedback.eventType,
  }, request, env, result.duplicate ? 200 : 202);
}

async function sendAndRecordConfirmationEmail(
  env: Env,
  request: Request,
  input: {
    email: string;
    locale: string;
    subscriberId: string;
    contactId: string;
    source: string;
    subscriptions: EmailSubscriptionItem[];
  },
): Promise<EmailDeliveryResult> {
  const config = smtpConfig(env);
  const now = new Date().toISOString();
  const deliveryId = crypto.randomUUID();
  const content = buildConfirmationEmail(input.locale, input.subscriptions);
  const primarySubscriptionId = input.subscriptions[0]?.id || null;
  const baseDelivery = {
    deliveryId,
    subscriberId: input.subscriberId,
    contactId: input.contactId,
    subscriptionId: primarySubscriptionId,
    recipient: input.email,
    subject: content.subject,
    deliveryType: "subscription_confirmation",
    provider: "smtp",
    source: input.source,
    metadata: {
      subscription_ids: input.subscriptions.map((subscription) => subscription.id),
      list_codes: input.subscriptions.map((subscription) => subscription.listCode),
    },
    now,
  };

  if (!config) {
    await insertEmailDeliveryRecord(env.DB, {
      ...baseDelivery,
      status: "skipped",
      attempts: 0,
      errorCode: "smtp_not_configured",
      errorMessage: "SMTP is not fully configured.",
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_skipped",
      actorType: "system",
      request,
      metadata: { reason: "smtp_not_configured" },
    });
    return {
      status: "skipped",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      reason: "smtp_not_configured",
      message: "SMTP delivery is not configured.",
    };
  }

  const emailLimit = await confirmationEmailRateLimit(env, input.contactId);
  if (!emailLimit.allowed) {
    await insertEmailDeliveryRecord(env.DB, {
      ...baseDelivery,
      status: "skipped",
      attempts: 0,
      errorCode: "confirmation_rate_limited",
      errorMessage: `Confirmation email limit reached. Try again after ${emailLimit.retryAfterSeconds} seconds.`,
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_rate_limited",
      actorType: "system",
      request,
      metadata: { retry_after_seconds: emailLimit.retryAfterSeconds },
    });
    return {
      status: "skipped",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      reason: "confirmation_rate_limited",
      message: "Confirmation email was recently sent. Please wait before requesting another one.",
    };
  }

  await insertEmailDeliveryRecord(env.DB, {
    ...baseDelivery,
    status: "queued",
    attempts: 1,
  });

  try {
    const receipt = await sendSmtpEmail(config, {
      to: input.email,
      subject: content.subject,
      text: content.text,
      html: content.html,
      messageId: `${deliveryId}@globalinfectiousdisease.com`,
    });
    await updateEmailDeliveryRecord(env.DB, {
      deliveryId,
      status: "sent",
      sentAt: new Date().toISOString(),
      providerMessageId: receipt.providerMessageId || `${deliveryId}@globalinfectiousdisease.com`,
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_sent",
      actorType: "system",
      request,
      metadata: {
        provider: "smtp",
        recipient_domain: input.email.split("@")[1] || "",
      },
    });
    return {
      status: "sent",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      from: config.fromEmail,
      message: "Confirmation email sent.",
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : "smtp_delivery_failed";
    await updateEmailDeliveryRecord(env.DB, {
      deliveryId,
      status: "failed",
      errorCode: "smtp_delivery_failed",
      errorMessage: boundedText(errorMessage, "smtp_delivery_failed", 500),
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_failed",
      actorType: "system",
      request,
      metadata: { provider: "smtp", reason: "smtp_delivery_failed" },
    });
    return {
      status: "failed",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      from: config.fromEmail,
      reason: "smtp_delivery_failed",
      message: "SMTP delivery failed.",
    };
  }
}

async function confirmSubscription(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("token") || "";
  const parsed = await verifySignedToken(env, token, "confirm");
  if (!parsed) {
    throw new HttpError(400, "invalid_or_expired_token");
  }

  const subscription = await env.DB.prepare(
    `SELECT s.id, s.subscriber_id, s.contact_id
     FROM subscriptions s
     WHERE s.id = ?`
  ).bind(parsed.subscriptionId).first<{ id: string; subscriber_id: string; contact_id: string }>();

  if (!subscription) {
    throw new HttpError(404, "subscription_not_found");
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE subscriptions SET status = 'active', confirmed_at = COALESCE(confirmed_at, ?), updated_at = ? WHERE id = ?"
  ).bind(now, now, subscription.id).run();
  await env.DB.prepare(
    "UPDATE subscriber_contacts SET status = 'active', verified_at = COALESCE(verified_at, ?), updated_at = ? WHERE id = ?"
  ).bind(now, now, subscription.contact_id).run();
  await recordEvent(env, {
    subscriberId: subscription.subscriber_id,
    subscriptionId: subscription.id,
    eventType: "subscription_confirmed",
    actorType: "subscriber",
    request,
  });

  return htmlPage("Subscription confirmed", "Your GIDS subscription is active.");
}

async function unsubscribe(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("token") || "";
  const parsed = await verifySignedToken(env, token, "unsubscribe");
  if (!parsed) {
    throw new HttpError(400, "invalid_or_expired_token");
  }

  const subscription = await env.DB.prepare(
    "SELECT id, subscriber_id FROM subscriptions WHERE id = ?"
  ).bind(parsed.subscriptionId).first<{ id: string; subscriber_id: string }>();

  if (!subscription) {
    throw new HttpError(404, "subscription_not_found");
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE subscriptions SET status = 'unsubscribed', updated_at = ? WHERE id = ?"
  ).bind(now, subscription.id).run();
  await skipPendingSituationAlertsForSubscription(env, subscription.id);
  await recordEvent(env, {
    subscriberId: subscription.subscriber_id,
    subscriptionId: subscription.id,
    eventType: "subscription_unsubscribed",
    actorType: "subscriber",
    request,
  });

  return htmlPage("Unsubscribed", "This GIDS subscription has been stopped.");
}

async function runMaintenance(env: Env): Promise<JsonValue> {
  const now = new Date().toISOString();
  const cutoff = pendingCutoff(env.PENDING_EXPIRY_DAYS);

  const pendingSubscriptions = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM subscriptions WHERE status = 'pending' AND created_at < ?"
  ).bind(cutoff).first<{ count: number }>();

  await env.DB.prepare(
    "UPDATE subscriptions SET status = 'expired', updated_at = ? WHERE status = 'pending' AND created_at < ?"
  ).bind(now, cutoff).run();

  const pendingContacts = await env.DB.prepare(
    `SELECT COUNT(*) AS count
     FROM subscriber_contacts c
     WHERE c.status = 'pending'
       AND c.created_at < ?
       AND NOT EXISTS (
         SELECT 1 FROM subscriptions s
         WHERE s.contact_id = c.id AND s.status IN ('pending', 'active')
       )`
  ).bind(cutoff).first<{ count: number }>();

  await env.DB.prepare(
    `UPDATE subscriber_contacts
     SET status = 'expired', updated_at = ?
     WHERE status = 'pending'
       AND created_at < ?
       AND NOT EXISTS (
         SELECT 1 FROM subscriptions s
         WHERE s.contact_id = subscriber_contacts.id AND s.status IN ('pending', 'active')
       )`
  ).bind(now, cutoff).run();

  const situationAlerts = await maintainSituationAlerts(env);

  return {
    ok: true,
    ran_at: now,
    cutoff,
    expired_subscriptions: Number(pendingSubscriptions?.count || 0),
    expired_contacts: Number(pendingContacts?.count || 0),
    situation_alerts: situationAlerts,
  };
}

async function enforceSubmissionRateLimit(request: Request, env: Env): Promise<void> {
  const limit = configInt(env.SUBMISSION_RATE_LIMIT_PER_HOUR, 30, 1, 1000);
  const ip = request.headers.get("CF-Connecting-IP") || "";
  if (!ip || limit <= 0) return;

  const ipHash = await sha256Hex(`${signingSecret(env)}:ip:${ip}`);
  const cutoff = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const recent = await env.DB.prepare(
    `SELECT COUNT(*) AS count
     FROM subscription_events
     WHERE ip_hash = ?
       AND created_at >= ?
       AND event_type IN (
         'subscription_created',
         'subscription_updated',
         'confirmation_email_sent',
         'confirmation_email_failed',
         'confirmation_email_rate_limited'
       )`
  ).bind(ipHash, cutoff).first<{ count: number }>();

  if (submissionRateLimited(recent?.count, limit)) {
    throw new HttpError(429, "rate_limited");
  }
}

async function confirmationEmailRateLimit(
  env: Env,
  contactId: string,
): Promise<{ allowed: boolean; retryAfterSeconds: number }> {
  const limit = configInt(env.CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES, 2, 1, 50);
  const cutoffMs = Date.now() - 10 * 60 * 1000;
  const cutoff = new Date(cutoffMs).toISOString();
  const recent = await env.DB.prepare(
    `SELECT COUNT(*) AS count, MIN(created_at) AS oldest
     FROM transactional_email_deliveries
     WHERE contact_id = ?
       AND delivery_type = 'subscription_confirmation'
       AND created_at >= ?
       AND status IN ('queued', 'sent', 'failed')`
  ).bind(contactId, cutoff).first<{ count: number; oldest?: string | null }>();

  return confirmationRateLimit(recent?.count, recent?.oldest, limit);
}

async function readPayload(request: Request): Promise<Payload> {
  const contentType = request.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return await request.json() as Payload;
  }

  if (contentType.includes("form")) {
    const form = await request.formData();
    const payload: Payload = {};
    for (const [key, value] of form.entries()) {
      if (typeof value !== "string") continue;
      const existing = payload[key];
      if (Array.isArray(existing)) {
        existing.push(value);
      } else if (typeof existing === "string") {
        payload[key] = [existing, value];
      } else {
        payload[key] = value;
      }
    }
    return payload;
  }

  return {};
}

async function verifyTurnstileIfConfigured(payload: Payload, request: Request, env: Env): Promise<void> {
  if (!env.TURNSTILE_SECRET_KEY) return;

  const token = valueAsString(payload.turnstileToken) || valueAsString(payload["cf-turnstile-response"]);
  if (!token) {
    throw new HttpError(400, "turnstile_token_required");
  }

  const form = new FormData();
  form.append("secret", env.TURNSTILE_SECRET_KEY);
  form.append("response", token);
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) form.append("remoteip", ip);

  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body: form,
  });
  const result = await response.json() as { success?: boolean };
  if (!result.success) {
    throw new HttpError(400, "turnstile_verification_failed");
  }
}

async function recordEvent(env: Env, input: {
  subscriberId?: string;
  subscriptionId?: string;
  eventType: string;
  actorType: string;
  request: Request;
  metadata?: JsonValue;
}): Promise<void> {
  const now = new Date().toISOString();
  const ip = input.request.headers.get("CF-Connecting-IP") || "";
  const ipHash = ip ? await sha256Hex(`${signingSecret(env)}:ip:${ip}`) : null;
  await env.DB.prepare(
    `INSERT INTO subscription_events (
       id, subscriber_id, subscription_id, event_type, actor_type, ip_hash,
       user_agent, metadata_json, created_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    crypto.randomUUID(),
    input.subscriberId || null,
    input.subscriptionId || null,
    input.eventType,
    input.actorType,
    ipHash,
    boundedText(input.request.headers.get("user-agent") || "", "", 500),
    input.metadata ? JSON.stringify(input.metadata) : null,
    now,
  ).run();
}

async function createSignedToken(
  env: Env,
  kind: "confirm" | "unsubscribe",
  subscriptionId: string,
  ttlSeconds: number,
): Promise<string> {
  return signSubscriptionToken(signingSecret(env), kind, subscriptionId, ttlSeconds);
}

async function verifySignedToken(
  env: Env,
  token: string,
  kind: "confirm" | "unsubscribe",
): Promise<{ subscriptionId: string } | null> {
  return verifySubscriptionToken(signingSecret(env), token, kind);
}

function signingSecret(env: Env): string {
  if (!env.TOKEN_SIGNING_SECRET) {
    throw new HttpError(500, "TOKEN_SIGNING_SECRET_not_configured");
  }
  return env.TOKEN_SIGNING_SECRET;
}
