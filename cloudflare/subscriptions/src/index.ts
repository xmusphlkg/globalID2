import { boundedText, isRecord, valueAsString } from "./lib/input.ts";
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
import { deliveryMode } from "./lib/pilot.ts";
import { sendSmtpEmail, smtpConfig } from "./lib/smtp.ts";
import {
  HttpError,
  configInt,
  corsHeaders,
  json,
  requireAdmin,
  type JsonValue,
} from "./lib/http.ts";
import { confirmationRateLimit } from "./lib/rate-limit.ts";
import { matchWorkerRoute } from "./lib/router.ts";
import { dispatchScheduledTasks } from "./lib/scheduled.ts";
import {
  insertEmailDelivery as insertEmailDeliveryRecord,
  reserveConfirmationEmail,
  updateEmailDelivery as updateEmailDeliveryRecord,
} from "./lib/db.ts";
import { createCampaignHandlers } from "./handlers/campaigns.ts";
import { createSubscriptionLifecycleHandlers } from "./handlers/subscription-lifecycle.ts";
import { createPreferenceHandlers } from "./handlers/preferences.ts";
import { handleSesFeedback, reconcileSesFeedback } from "./handlers/ses-feedback.ts";
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
  retryable?: boolean;
  permanent_failure?: boolean;
}

const campaignHandlers = createCampaignHandlers({
  readPayload,
  createSignedToken,
  smtpConfig,
  sendSmtpEmail,
});
const subscriptionHandlerDependencies = { readPayload, createSignedToken };
const subscriptionLifecycle = createSubscriptionLifecycleHandlers({
  readPayload, verifyTurnstileIfConfigured, enforceSubmissionRateLimit, signingSecret,
  createSignedToken, verifySignedToken, sendAndRecordConfirmationEmail, recordEvent,
  skipPendingSituationAlertsForSubscription,
});
const preferenceHandlers = createPreferenceHandlers({ createSignedToken, verifySignedToken, smtpConfig, sendSmtpEmail });
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
          return json({ ok: true, service: "globalid-subscriptions", delivery_mode: deliveryMode(env), capabilities: { research_content_items_v1: true, research_personalized_items_v1: true, official_notice_alert_v1: true } }, request, env);
        case "subscription_options":
          return await listSubscriptionOptions(request, env);
        case "create_subscription":
          return await subscriptionLifecycle.createSubscription(request, env, _ctx);
        case "confirm_subscription":
          return await subscriptionLifecycle.confirmSubscription(request, env);
        case "unsubscribe":
          return await subscriptionLifecycle.unsubscribe(request, env);
        case "manage_subscription":
          return await (route.operation === "get" ? preferenceHandlers.get(request, env) : preferenceHandlers.post(request, env, _ctx));
        case "situation_alert_ingest":
          return await situationAlertHandlers.ingest(request, env);
        case "email_delivery_feedback":
          return await recordDeliveryFeedback(request, env);
        case "ses_sns_feedback":
          return await handleSesFeedback(request, env);
        case "internal_scheduled_task":
          requireAdmin(request, env);
          if (route.task === "maintenance") return json(await runMaintenance(env), request, env);
          if (route.task === "confirmation") await subscriptionLifecycle.drainConfirmationOutbox(env);
          if (route.task === "management") await preferenceHandlers.drainManagementOutbox(env);
          if (route.task === "campaign") await campaignHandlers.drainDue(env);
          if (route.task === "situation") await processSituationAlertOutbox(env, { createSignedToken, smtpConfig, sendSmtpEmail });
          if (route.task === "feedback") await reconcileSesFeedback(env);
          return json({ ok: true }, request, env);
        case "admin_audience":
          return await listAudience(request, env, subscriptionHandlerDependencies);
        case "admin_stats":
          return await subscriptionStats(request, env);
        case "admin_subscriptions":
          return await listSubscriptionsAdmin(request, env);
        case "admin_notifications":
          return await (route.operation === "list"
            ? campaignHandlers.list(request, env)
            : campaignHandlers.create(request, env));
        case "admin_notification":
          return await (route.operation === "get"
            ? campaignHandlers.get(request, env, route.campaignId)
            : campaignHandlers.process(request, env, route.campaignId));
        case "admin_situation_alerts":
          return await (route.operation === "list"
            ? situationAlertHandlers.list(request, env)
            : situationAlertHandlers.process(request, env));
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
    await dispatchScheduledTasks(env);
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
    outboxId: string;
    outboxClaimToken: string;
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

  const reserved = await reserveConfirmationEmail(env.DB, {
    ...baseDelivery,
    cutoff: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
    limit: configInt(env.CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES, 2, 1, 50),
  });
  if (!reserved) {
    const emailLimit = await confirmationEmailRateLimit(env, input.contactId);
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

  // Bind the outbox job to its durable ledger record before invoking SMTP so
  // an SNS callback can settle it even while the SMTP request is in flight.
  const linked = await env.DB.prepare(
    `UPDATE confirmation_email_outbox SET transaction_delivery_id=?, updated_at=?
     WHERE id=? AND status='sending' AND claim_token=? RETURNING id`,
  ).bind(deliveryId, new Date().toISOString(), input.outboxId, input.outboxClaimToken).first<{ id: string }>();
  if (!linked) return { status: "skipped", provider: "smtp", reason: "confirmation_claim_lost" };

  let receipt: Awaited<ReturnType<typeof sendSmtpEmail>>;
  try {
    receipt = await sendSmtpEmail(config, {
      to: input.email,
      subject: content.subject,
      text: content.text,
      html: content.html,
      messageId: `${deliveryId}@globalinfectiousdisease.com`,
    }, "transactional");
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
      retryable: /_rejected:4\d\d$/.test(errorMessage),
      permanent_failure: /_rejected:5\d\d$/.test(errorMessage),
    };
  }
  // A provider accepted the message. If the ledger write fails, propagate the
  // uncertainty to the outbox rather than claiming a durable success.
  await updateEmailDeliveryRecord(env.DB, {
    deliveryId,
    status: "sent",
    sentAt: new Date().toISOString(),
    providerMessageId: receipt.providerMessageId || `${deliveryId}@globalinfectiousdisease.com`,
  });
  try {
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_sent",
      actorType: "system",
      request,
      metadata: { provider: "smtp", recipient_domain: input.email.split("@")[1] || "" },
    });
  } catch {
    console.error(JSON.stringify({ message: "confirmation_sent_event_record_failed", delivery_id: deliveryId }));
  }
  return {
    status: "sent",
    provider: "smtp",
    delivery_id: deliveryId,
    recipient: input.email,
    from: config.fromEmail,
    message: "Confirmation email sent.",
  };
}

async function runMaintenance(env: Env): Promise<JsonValue> {
  const now = new Date().toISOString();
  const pending = await subscriptionLifecycle.expirePending(env);
  await env.DB.prepare("DELETE FROM preference_link_limits WHERE expires_at < ?").bind(now).run();

  const situationAlerts = await maintainSituationAlerts(env);

  return {
    ok: true,
    ran_at: now,
    ...pending,
    situation_alerts: situationAlerts,
  };
}

async function enforceSubmissionRateLimit(request: Request, env: Env): Promise<void> {
  const limit = configInt(env.SUBMISSION_RATE_LIMIT_PER_HOUR, 30, 1, 1000);
  const ip = request.headers.get("CF-Connecting-IP") || "";
  if (!ip || limit <= 0) return;

  const hour = Math.floor(Date.now() / 3_600_000);
  const bucket = await sha256Hex(`${signingSecret(env)}:subscribe-limit:${ip}:${hour}`);
  const expires = new Date((hour + 2) * 3_600_000).toISOString();
  const reserved = await env.DB.prepare(
    `INSERT INTO preference_link_limits (bucket, attempts, expires_at) VALUES (?, 1, ?)
     ON CONFLICT(bucket) DO UPDATE SET attempts = attempts + 1 WHERE attempts < ? RETURNING attempts`,
  ).bind(bucket, expires, limit).first<{ attempts: number }>();
  if (!reserved) throw new HttpError(429, "rate_limited");
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
       AND status IN ('queued', 'sent', 'failed', 'delivered', 'deferred')`
  ).bind(contactId, cutoff).first<{ count: number; oldest?: string | null }>();

  return confirmationRateLimit(recent?.count, recent?.oldest, limit);
}

async function readPayload(request: Request): Promise<Payload> {
  const contentType = request.headers.get("content-type") || "";
  const maxBytes = 1024 * 1024;
  if (Number(request.headers.get("content-length") || 0) > maxBytes) throw new HttpError(413, "payload_too_large");
  const chunks: Uint8Array[] = [];
  let total = 0;
  const reader = request.body?.getReader();
  if (reader) {
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > maxBytes) {
          await reader.cancel();
          throw new HttpError(413, "payload_too_large");
        }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  if (contentType.includes("application/json")) {
    try {
      const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
      if (!isRecord(parsed)) throw new Error("not_an_object");
      return parsed;
    } catch { throw new HttpError(400, "invalid_json"); }
  }

  if (contentType.includes("form")) {
    let form: FormData;
    try { form = await new Response(bytes, { headers: { "content-type": contentType } }).formData(); }
    catch { throw new HttpError(400, "invalid_form"); }
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
  kind: "confirm" | "unsubscribe" | "manage",
  subscriptionId: string,
  ttlSeconds: number,
): Promise<string> {
  if (kind === "manage") {
    const subscription = await env.DB.prepare("SELECT management_generation FROM subscriptions WHERE id = ?")
      .bind(subscriptionId).first<{ management_generation: string }>();
    if (!subscription) throw new HttpError(404, "subscription_not_found");
    return signSubscriptionToken(signingSecret(env), kind, subscriptionId, ttlSeconds, Date.now(), subscription.management_generation);
  }
  return signSubscriptionToken(signingSecret(env), kind, subscriptionId, ttlSeconds);
}

async function verifySignedToken(
  env: Env,
  token: string,
  kind: "confirm" | "unsubscribe" | "manage",
): Promise<{ subscriptionId: string; version?: string } | null> {
  const parsed = await verifySubscriptionToken(signingSecret(env), token, kind);
  if (!parsed || kind !== "manage") return parsed;
  if (typeof parsed.version !== "string") return null;
  const subscription = await env.DB.prepare("SELECT management_generation FROM subscriptions WHERE id = ?")
    .bind(parsed.subscriptionId).first<{ management_generation: string }>();
  return subscription && await secureTextEqual(parsed.version, subscription.management_generation) ? parsed : null;
}

function signingSecret(env: Env): string {
  if (!env.TOKEN_SIGNING_SECRET) {
    throw new HttpError(500, "TOKEN_SIGNING_SECRET_not_configured");
  }
  return env.TOKEN_SIGNING_SECRET;
}
