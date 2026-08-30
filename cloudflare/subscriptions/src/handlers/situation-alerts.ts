import { buildNotificationEmail } from "../lib/email.ts";
import { insertEmailDelivery, updateEmailDelivery } from "../lib/db.ts";
import { HttpError, configInt, json, publicBaseUrl, type JsonValue } from "../lib/http.ts";
import { boundedText, parseJsonObject } from "../lib/input.ts";
import { secureTextEqual, sha256Hex } from "../lib/security.ts";
import {
  isAllowedSituationPublicUrl,
  parseSituationAlertJob,
  parseSituationAlertPayload,
  situationAlertContent,
  situationAlertJob,
  SituationAlertValidationError,
  type SituationAlertJob,
  type SituationAlertPayload,
} from "../lib/situation-alert.ts";
import { maskEmail } from "../lib/subscriptions.ts";
import type { Env } from "../types.ts";

const MAX_PAYLOAD_BYTES = 64 * 1024;

type Dependencies = {
  createSignedToken(
    env: Env,
    purpose: "unsubscribe",
    subscriptionId: string,
    ttlSeconds: number,
  ): Promise<string>;
  smtpConfig(env: Env): SmtpConfig | null;
  sendSmtpEmail(config: SmtpConfig, message: {
    to: string;
    subject: string;
    text: string;
    html: string;
  }): Promise<unknown>;
};

type SmtpConfig = {
  host: string;
  port: number;
  username: string;
  password: string;
  fromEmail: string;
  fromName: string;
  useTls: boolean;
};

type AlertEventRow = {
  id: string;
  idempotency_key: string;
  report_id: string;
  signal_id: string;
  verification_basis: string;
  verification_policy_version?: string | null;
  payload_sha256: string;
  payload_json: string;
  status: string;
  received_at: string;
  updated_at: string;
  completed_at?: string | null;
  queued_count: number;
  sent_count: number;
  skipped_count: number;
  failed_count: number;
  last_error?: string | null;
};

type ClaimedDelivery = {
  id: string;
  event_id: string;
  subscription_id: string;
  contact_id: string;
  attempts: number;
  payload_json: string;
  subscriber_id: string;
  subscriber_status: string;
  subscription_status: string;
  contact_status: string;
  email: string;
  locale?: string | null;
};

export interface AlertProcessResult extends Record<string, JsonValue | undefined> {
  ok: boolean;
  processed: number;
  sent: number;
  retried: number;
  skipped: number;
  dead_letter: number;
  reason?: string;
}

export function createSituationAlertHandlers(deps: Dependencies) {
  async function ingest(request: Request, env: Env): Promise<Response> {
    await requireSituationAlertIngest(request, env);
    const raw = await readBoundedJson(request);
    let alert: SituationAlertPayload;
    try {
      alert = parseSituationAlertPayload(raw);
    } catch (error) {
      if (error instanceof SituationAlertValidationError) {
        throw new HttpError(422, error.message);
      }
      throw error;
    }
    if (
      alert.signal.verification_basis === "automated_policy"
      && env.SITUATION_ALERT_AUTOMATED_POLICY_ENABLED?.trim().toLowerCase() !== "true"
    ) {
      // Keep a second deployment-level kill switch after the report's
      // structured v3.2 policy decision has passed contract validation.
      throw new HttpError(422, "automated_policy_dispatch_disabled");
    }
    if (!isAllowedSituationPublicUrl(alert.report.public_url, env.SITUATION_PUBLIC_ORIGINS)) {
      throw new HttpError(422, "untrusted_public_report_origin");
    }

    const payloadJson = JSON.stringify(alert);
    const payloadSha256 = await sha256Hex(payloadJson);
    const now = new Date().toISOString();
    const proposedEventId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO situation_alert_events (
         id, idempotency_key, report_id, signal_id, verification_basis,
         verification_policy_version, payload_sha256, payload_json, status, received_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?)
       ON CONFLICT(idempotency_key) DO NOTHING`,
    ).bind(
      proposedEventId,
      alert.idempotency_key,
      alert.report.report_id,
      alert.signal.signal_id,
      alert.signal.verification_basis,
      alert.signal.verification_policy_version,
      payloadSha256,
      payloadJson,
      now,
      now,
    ).run();

    const event = await env.DB.prepare(
      `SELECT id, idempotency_key, report_id, signal_id, verification_basis,
       verification_policy_version, payload_sha256, payload_json,
       status, received_at, updated_at, completed_at, queued_count, sent_count,
       skipped_count, failed_count, last_error
       FROM situation_alert_events WHERE idempotency_key = ?`,
    ).bind(alert.idempotency_key).first<AlertEventRow>();
    if (!event) throw new HttpError(500, "situation_alert_not_persisted");
    if (event.payload_sha256 !== payloadSha256) {
      throw new HttpError(409, "idempotency_key_payload_conflict");
    }

    const duplicate = event.id !== proposedEventId;
    const queued = await fanOutSituationAlert(env, event.id, alert, now);
    const dispatch = queued > 0 ? await dispatchSituationAlert(env, event.id) : "not_required";
    structuredLog("info", "situation_alert_accepted", {
      event_id: event.id,
      report_id: event.report_id,
      signal_id: event.signal_id,
      verification_basis: alert.signal.verification_basis,
      verification_policy_version: alert.signal.verification_policy_version,
      duplicate,
      queued,
      dispatch,
    });
    return json({
      ok: true,
      event_id: event.id,
      idempotency_key: event.idempotency_key,
      duplicate,
      queued_deliveries: queued,
      dispatch,
    }, request, env, duplicate ? 200 : 202);
  }

  async function process(request: Request, env: Env): Promise<Response> {
    await requireSituationAlertAdmin(request, env);
    const result = await processSituationAlertOutbox(env, deps);
    return json(result, request, env);
  }

  async function list(request: Request, env: Env): Promise<Response> {
    await requireSituationAlertAdmin(request, env);
    const url = new URL(request.url);
    const rawLimit = Number(url.searchParams.get("limit") || 50);
    const rawOffset = Number(url.searchParams.get("offset") || 0);
    const limit = Math.min(Math.max(Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 50, 1), 250);
    const offset = Math.max(Number.isFinite(rawOffset) ? Math.trunc(rawOffset) : 0, 0);
    const total = await env.DB.prepare("SELECT COUNT(*) AS count FROM situation_alert_events")
      .first<{ count: number }>();
    const rows = await env.DB.prepare(
      `SELECT id, idempotency_key, report_id, signal_id, verification_basis,
       verification_policy_version, status, received_at, updated_at,
       completed_at, queued_count, sent_count, skipped_count, failed_count, last_error
       FROM situation_alert_events ORDER BY received_at DESC LIMIT ? OFFSET ?`,
    ).bind(limit, offset).all<Omit<AlertEventRow, "payload_sha256" | "payload_json">>();
    return json({
      ok: true,
      alerts: (rows.results || []).map((row) => ({
        event_id: row.id,
        idempotency_key: row.idempotency_key,
        report_id: row.report_id,
        signal_id: row.signal_id,
        verification_basis: row.verification_basis,
        verification_policy_version: row.verification_policy_version || null,
        status: row.status,
        received_at: row.received_at,
        updated_at: row.updated_at,
        completed_at: row.completed_at || null,
        counts: alertCounts(row),
        last_error: row.last_error || null,
      })),
      pagination: { total: Number(total?.count || 0), limit, offset },
    }, request, env);
  }

  return { ingest, process, list };
}

export async function consumeSituationAlertBatch(
  batch: MessageBatch<SituationAlertJob>,
  env: Env,
  deps: Dependencies,
): Promise<void> {
  for (const message of batch.messages) {
    const job = parseSituationAlertJob(message.body);
    if (!job) {
      structuredLog("error", "situation_alert_queue_invalid_message", {
        queue_message_id: message.id,
      });
      message.ack();
      continue;
    }
    try {
      const result = await processSituationAlertOutbox(env, deps, { eventId: job.event_id });
      if (result.reason === "smtp_not_configured") {
        message.retry({ delaySeconds: 300 });
      } else {
        message.ack();
      }
    } catch (error) {
      structuredLog("error", "situation_alert_queue_processing_failed", {
        event_id: job.event_id,
        queue_message_id: message.id,
        error: errorMessage(error),
      });
      message.retry({ delaySeconds: retryDelaySeconds(message.attempts) });
    }
  }
}

export async function processSituationAlertOutbox(
  env: Env,
  deps: Dependencies,
  options: { eventId?: string } = {},
): Promise<AlertProcessResult> {
  const config = deps.smtpConfig(env);
  if (!config) {
    structuredLog("error", "situation_alert_smtp_not_configured", {});
    return {
      ok: false,
      processed: 0,
      sent: 0,
      retried: 0,
      skipped: 0,
      dead_letter: 0,
      reason: "smtp_not_configured",
    };
  }

  const now = new Date().toISOString();
  await recoverExpiredClaims(env, now);
  const batchSize = configInt(env.SITUATION_ALERT_BATCH_SIZE, 20, 1, 100);
  const maxAttempts = configInt(env.SITUATION_ALERT_MAX_ATTEMPTS, 5, 1, 20);
  const candidates = await dueDeliveryIds(env, now, batchSize, options.eventId);
  const touchedEvents = new Set<string>();
  const result: AlertProcessResult = {
    ok: true,
    processed: 0,
    sent: 0,
    retried: 0,
    skipped: 0,
    dead_letter: 0,
  };

  for (const candidate of candidates) {
    const claimed = await claimDelivery(env, candidate.id, now);
    if (!claimed) continue;
    result.processed += 1;
    touchedEvents.add(claimed.event_id);

    if (
      claimed.subscription_status !== "active" ||
      claimed.contact_status !== "active" ||
      claimed.subscriber_status !== "active"
    ) {
      await markDeliverySkipped(env, claimed.id, "subscription_inactive");
      result.skipped += 1;
      continue;
    }

    let alert: SituationAlertPayload;
    try {
      alert = parseSituationAlertPayload(parseJsonObject(claimed.payload_json));
    } catch (error) {
      await markDeliveryDeadLetter(env, claimed.id, `stored_payload_invalid:${errorMessage(error)}`);
      result.dead_letter += 1;
      continue;
    }

    const locale = claimed.locale || "en";
    const content = situationAlertContent(alert, locale);
    const unsubscribeToken = await deps.createSignedToken(
      env,
      "unsubscribe",
      claimed.subscription_id,
      60 * 60 * 24 * 365,
    );
    const unsubscribeUrl = `${configuredPublicBaseUrl(env)}/api/subscriptions/unsubscribe?token=${unsubscribeToken}`;
    const email = buildNotificationEmail(locale, content, unsubscribeUrl);
    const transactionId = crypto.randomUUID();
    await insertEmailDelivery(env.DB, {
      deliveryId: transactionId,
      subscriberId: claimed.subscriber_id,
      contactId: claimed.contact_id,
      subscriptionId: claimed.subscription_id,
      recipient: claimed.email,
      subject: email.subject,
      deliveryType: "verified_situation_alert",
      provider: "smtp",
      status: "queued",
      attempts: claimed.attempts,
      source: "situation_alert_outbox",
      metadata: {
        event_id: claimed.event_id,
        outbox_delivery_id: claimed.id,
        report_id: alert.report.report_id,
        signal_id: alert.signal.signal_id,
        verification_basis: alert.signal.verification_basis,
        verification_policy_version: alert.signal.verification_policy_version,
      },
      now: new Date().toISOString(),
    });

    try {
      await deps.sendSmtpEmail(config, {
        to: claimed.email,
        subject: email.subject,
        text: email.text,
        html: email.html,
      });
      const sentAt = new Date().toISOString();
      await env.DB.prepare(
        `UPDATE situation_alert_deliveries
         SET status = 'sent', transaction_delivery_id = ?, sent_at = ?, updated_at = ?,
         last_error = NULL, claim_token = NULL, claim_expires_at = NULL
         WHERE id = ?`,
      ).bind(transactionId, sentAt, sentAt, claimed.id).run();
      await updateEmailDelivery(env.DB, { deliveryId: transactionId, status: "sent", sentAt });
      result.sent += 1;
      structuredLog("info", "situation_alert_delivery_sent", {
        event_id: claimed.event_id,
        delivery_id: claimed.id,
        attempt: claimed.attempts,
      });
    } catch (error) {
      const message = deliveryErrorMessage(error);
      const terminal = claimed.attempts >= maxAttempts;
      const delaySeconds = retryDelaySeconds(claimed.attempts);
      const nextAttemptAt = new Date(Date.now() + delaySeconds * 1000).toISOString();
      await env.DB.prepare(
        `UPDATE situation_alert_deliveries
         SET status = ?, next_attempt_at = ?, last_error = ?, updated_at = ?,
         claim_token = NULL, claim_expires_at = NULL, failed_at = CASE WHEN ? = 'dead_letter' THEN ? ELSE failed_at END
         WHERE id = ?`,
      ).bind(
        terminal ? "dead_letter" : "retry",
        terminal ? null : nextAttemptAt,
        message,
        new Date().toISOString(),
        terminal ? "dead_letter" : "retry",
        new Date().toISOString(),
        claimed.id,
      ).run();
      await updateEmailDelivery(env.DB, {
        deliveryId: transactionId,
        status: "failed",
        errorCode: terminal ? "smtp_delivery_dead_letter" : "smtp_delivery_retry",
        errorMessage: message,
      });
      if (terminal) {
        result.dead_letter += 1;
      } else {
        result.retried += 1;
        await dispatchSituationAlert(env, claimed.event_id, delaySeconds);
      }
      structuredLog("error", "situation_alert_delivery_failed", {
        event_id: claimed.event_id,
        delivery_id: claimed.id,
        attempt: claimed.attempts,
        terminal,
        error: message,
      });
    }
  }

  if (options.eventId) touchedEvents.add(options.eventId);
  for (const eventId of touchedEvents) await refreshSituationAlertStatus(env, eventId);
  if (options.eventId && await hasDueSituationAlertDeliveries(env, options.eventId)) {
    await dispatchSituationAlert(env, options.eventId);
  }
  return result;
}

export async function maintainSituationAlerts(env: Env): Promise<JsonValue> {
  const now = new Date().toISOString();
  await recoverExpiredClaims(env, now);
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries AS d
     SET status = 'skipped', last_error = 'subscription_inactive', skipped_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL
     WHERE d.status IN ('queued', 'retry')
       AND NOT EXISTS (
         SELECT 1 FROM subscriptions s
         JOIN subscriber_contacts c ON c.id = s.contact_id
         JOIN subscribers sub ON sub.id = s.subscriber_id
         WHERE s.id = d.subscription_id
           AND s.status = 'active' AND c.status = 'active' AND sub.status = 'active'
       )`,
  ).bind(now, now).run();

  const activeEvents = await env.DB.prepare(
    `SELECT id, status, payload_json FROM situation_alert_events
     WHERE status IN ('received', 'queued', 'sending') LIMIT 500`,
  ).all<{ id: string; status: string; payload_json: string }>();
  let recoveredEvents = 0;
  for (const event of activeEvents.results || []) {
    if (event.status === "received") {
      try {
        const alert = parseSituationAlertPayload(parseJsonObject(event.payload_json));
        if (!isAllowedSituationPublicUrl(alert.report.public_url, env.SITUATION_PUBLIC_ORIGINS)) {
          throw new SituationAlertValidationError("untrusted_public_report_origin");
        }
        const queued = await fanOutSituationAlert(env, event.id, alert, now);
        if (queued > 0) await dispatchSituationAlert(env, event.id);
        recoveredEvents += 1;
      } catch (error) {
        await env.DB.prepare(
          `UPDATE situation_alert_events
           SET status = 'failed', last_error = 'stored_payload_invalid', completed_at = ?, updated_at = ?
           WHERE id = ?`,
        ).bind(now, now, event.id).run();
        structuredLog("error", "situation_alert_recovery_failed", {
          event_id: event.id,
          error: errorMessage(error),
        });
        continue;
      }
    }
    await refreshSituationAlertStatus(env, event.id);
  }

  const retentionDays = configInt(env.SITUATION_ALERT_RETENTION_DAYS, 180, 7, 3650);
  const cutoff = new Date(Date.now() - retentionDays * 24 * 60 * 60 * 1000).toISOString();
  const expired = await env.DB.prepare(
    `SELECT id FROM situation_alert_events
     WHERE completed_at IS NOT NULL AND completed_at < ? LIMIT 500`,
  ).bind(cutoff).all<{ id: string }>();
  for (const event of expired.results || []) {
    await env.DB.prepare("DELETE FROM situation_alert_deliveries WHERE event_id = ?").bind(event.id).run();
    await env.DB.prepare("DELETE FROM situation_alert_events WHERE id = ?").bind(event.id).run();
  }
  await env.DB.prepare(
    `DELETE FROM transactional_email_deliveries
     WHERE delivery_type = 'verified_situation_alert' AND created_at < ?`,
  ).bind(cutoff).run();
  return {
    refreshed_events: (activeEvents.results || []).length,
    recovered_events: recoveredEvents,
    deleted_events: (expired.results || []).length,
    retention_days: retentionDays,
  };
}

export async function skipPendingSituationAlertsForSubscription(
  env: Env,
  subscriptionId: string,
): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'skipped', last_error = 'subscription_unsubscribed', skipped_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL
     WHERE subscription_id = ? AND status IN ('queued', 'retry')`,
  ).bind(now, now, subscriptionId).run();
}

async function fanOutSituationAlert(
  env: Env,
  eventId: string,
  alert: SituationAlertPayload,
  now: string,
): Promise<number> {
  const clauses = [
    "l.code = 'alerts'",
    "s.status = 'active'",
    "s.frequency = 'instant'",
    "c.channel = 'email'",
    "c.status = 'active'",
    "sub.status = 'active'",
  ];
  const binds: unknown[] = [];
  addFilterClause(clauses, binds, "country", alert.signal.countries);
  addFilterClause(clauses, binds, "disease", alert.signal.diseases);
  addFilterClause(clauses, binds, "severity", [alert.signal.anomaly_state]);
  addFilterClause(clauses, binds, "report_type", ["situation"]);
  await env.DB.prepare(
    `INSERT INTO situation_alert_deliveries (
       id, event_id, subscription_id, contact_id, status, attempts,
       next_attempt_at, queued_at, updated_at
     )
     SELECT ? || ':' || s.id, ?, s.id, s.contact_id, 'queued', 0, ?, ?, ?
     FROM subscriptions s
     JOIN subscriber_contacts c ON c.id = s.contact_id
     JOIN subscribers sub ON sub.id = s.subscriber_id
     JOIN subscription_lists l ON l.id = s.list_id
     WHERE ${clauses.join(" AND ")}
     ON CONFLICT(event_id, subscription_id) DO NOTHING`,
  ).bind(eventId, eventId, now, now, now, ...binds).run();
  const count = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM situation_alert_deliveries WHERE event_id = ?",
  ).bind(eventId).first<{ count: number }>();
  const total = Number(count?.count || 0);
  if (total === 0) {
    await env.DB.prepare(
      `UPDATE situation_alert_events
       SET status = 'completed_no_audience', queued_count = 0, updated_at = ?, completed_at = ?
       WHERE id = ?`,
    ).bind(now, now, eventId).run();
    return 0;
  }
  await refreshSituationAlertStatus(env, eventId);
  const due = await env.DB.prepare(
    `SELECT COUNT(*) AS count FROM situation_alert_deliveries
     WHERE event_id = ? AND status IN ('queued', 'retry') AND next_attempt_at <= ?`,
  ).bind(eventId, now).first<{ count: number }>();
  return Number(due?.count || 0);
}

function addFilterClause(
  clauses: string[],
  binds: unknown[],
  type: string,
  values: string[],
): void {
  if (values.length === 0) {
    clauses.push(
      `NOT EXISTS (
        SELECT 1 FROM subscription_filters f
        WHERE f.subscription_id = s.id AND f.filter_type = ?
      )`,
    );
    binds.push(type);
    return;
  }
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

async function dispatchSituationAlert(
  env: Env,
  eventId: string,
  delaySeconds?: number,
): Promise<"cloudflare_queue" | "d1_outbox"> {
  if (!env.SITUATION_ALERT_QUEUE) return "d1_outbox";
  try {
    await env.SITUATION_ALERT_QUEUE.send(situationAlertJob(eventId), {
      contentType: "json",
      ...(delaySeconds ? { delaySeconds } : {}),
    });
    return "cloudflare_queue";
  } catch (error) {
    structuredLog("error", "situation_alert_queue_dispatch_failed", {
      event_id: eventId,
      error: errorMessage(error),
    });
    return "d1_outbox";
  }
}

async function dueDeliveryIds(
  env: Env,
  now: string,
  limit: number,
  eventId?: string,
): Promise<Array<{ id: string }>> {
  const eventClause = eventId ? "AND event_id = ?" : "";
  const binds: unknown[] = [now];
  if (eventId) binds.push(eventId);
  binds.push(limit);
  const rows = await env.DB.prepare(
    `SELECT id FROM situation_alert_deliveries
     WHERE status IN ('queued', 'retry') AND next_attempt_at <= ? ${eventClause}
     ORDER BY next_attempt_at ASC, queued_at ASC LIMIT ?`,
  ).bind(...binds).all<{ id: string }>();
  return rows.results || [];
}

async function hasDueSituationAlertDeliveries(env: Env, eventId: string): Promise<boolean> {
  const row = await env.DB.prepare(
    `SELECT COUNT(*) AS count FROM situation_alert_deliveries
     WHERE event_id = ? AND status IN ('queued', 'retry') AND next_attempt_at <= ?`,
  ).bind(eventId, new Date().toISOString()).first<{ count: number }>();
  return Number(row?.count || 0) > 0;
}

async function claimDelivery(env: Env, deliveryId: string, now: string): Promise<ClaimedDelivery | null> {
  const claimToken = crypto.randomUUID();
  const claimExpiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'sending', attempts = attempts + 1, claim_token = ?, claim_expires_at = ?, updated_at = ?
     WHERE id = ? AND status IN ('queued', 'retry') AND next_attempt_at <= ?`,
  ).bind(claimToken, claimExpiresAt, now, deliveryId, now).run();
  return env.DB.prepare(
    `SELECT d.id, d.event_id, d.subscription_id, d.contact_id, d.attempts,
     e.payload_json, s.subscriber_id, s.status AS subscription_status,
     sub.status AS subscriber_status, c.status AS contact_status, c.address AS email, sub.locale
     FROM situation_alert_deliveries d
     JOIN situation_alert_events e ON e.id = d.event_id
     JOIN subscriptions s ON s.id = d.subscription_id
     JOIN subscribers sub ON sub.id = s.subscriber_id
     JOIN subscriber_contacts c ON c.id = d.contact_id
     WHERE d.id = ? AND d.claim_token = ? AND d.status = 'sending'`,
  ).bind(deliveryId, claimToken).first<ClaimedDelivery>();
}

async function recoverExpiredClaims(env: Env, now: string): Promise<void> {
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'retry', next_attempt_at = ?, last_error = 'claim_timeout',
     claim_token = NULL, claim_expires_at = NULL, updated_at = ?
     WHERE status = 'sending' AND claim_expires_at IS NOT NULL AND claim_expires_at < ?`,
  ).bind(now, now, now).run();
}

async function markDeliverySkipped(env: Env, deliveryId: string, reason: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'skipped', last_error = ?, skipped_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL WHERE id = ?`,
  ).bind(reason, now, now, deliveryId).run();
}

async function markDeliveryDeadLetter(env: Env, deliveryId: string, reason: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'dead_letter', last_error = ?, failed_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL WHERE id = ?`,
  ).bind(boundedText(reason, "delivery_invalid", 500), now, now, deliveryId).run();
}

async function refreshSituationAlertStatus(env: Env, eventId: string): Promise<void> {
  const rows = await env.DB.prepare(
    `SELECT status, COUNT(*) AS count
     FROM situation_alert_deliveries WHERE event_id = ? GROUP BY status`,
  ).bind(eventId).all<{ status: string; count: number }>();
  const counts = (rows.results || []).reduce<Record<string, number>>((result, row) => {
    result[row.status] = Number(row.count || 0);
    return result;
  }, {});
  const sent = Number(counts.sent || 0);
  const skipped = Number(counts.skipped || 0);
  const failed = Number(counts.dead_letter || 0);
  const pending = Number(counts.queued || 0) + Number(counts.retry || 0) + Number(counts.sending || 0);
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  let status = "skipped";
  if (total === 0) status = "completed_no_audience";
  else if (pending > 0) status = Number(counts.sending || 0) > 0 ? "sending" : "queued";
  else if (failed > 0) status = sent > 0 ? "partial_failed" : "failed";
  else if (sent > 0) status = "sent";
  const completedAt = pending === 0 ? new Date().toISOString() : null;
  await env.DB.prepare(
    `UPDATE situation_alert_events
     SET status = ?, queued_count = ?, sent_count = ?, skipped_count = ?, failed_count = ?,
     completed_at = ?, updated_at = ?, last_error = CASE WHEN ? > 0 THEN 'delivery_dead_letter' ELSE NULL END
     WHERE id = ?`,
  ).bind(status, total, sent, skipped, failed, completedAt, new Date().toISOString(), failed, eventId).run();
}

async function requireSituationAlertIngest(request: Request, env: Env): Promise<void> {
  const expected = env.SITUATION_ALERT_INGEST_TOKEN || "";
  if (!expected) throw new HttpError(500, "SITUATION_ALERT_INGEST_TOKEN_not_configured");
  const authorization = request.headers.get("authorization") || "";
  const provided = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!(await secureTextEqual(provided, expected))) throw new HttpError(401, "unauthorized");
}

async function requireSituationAlertAdmin(request: Request, env: Env): Promise<void> {
  const expected = env.ADMIN_API_TOKEN || "";
  if (!expected) throw new HttpError(500, "ADMIN_API_TOKEN_not_configured");
  const authorization = request.headers.get("authorization") || "";
  const provided = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!(await secureTextEqual(provided, expected))) throw new HttpError(401, "unauthorized");
}

async function readBoundedJson(request: Request): Promise<unknown> {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new HttpError(415, "application_json_required");
  }
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PAYLOAD_BYTES) {
    throw new HttpError(413, "situation_alert_payload_too_large");
  }
  if (!request.body) throw new HttpError(400, "situation_alert_payload_required");

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_PAYLOAD_BYTES) {
      await reader.cancel("payload_too_large");
      throw new HttpError(413, "situation_alert_payload_too_large");
    }
    chunks.push(value);
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(body)) as unknown;
  } catch {
    throw new HttpError(400, "invalid_json");
  }
}

function configuredPublicBaseUrl(env: Env): string {
  if (!env.PUBLIC_BASE_URL) throw new HttpError(500, "PUBLIC_BASE_URL_not_configured");
  const url = new URL(env.PUBLIC_BASE_URL);
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !local) {
    throw new HttpError(500, "PUBLIC_BASE_URL_https_required");
  }
  return publicBaseUrl(new Request(url), env);
}

function retryDelaySeconds(attempt: number): number {
  const schedule = [60, 5 * 60, 30 * 60, 2 * 60 * 60, 6 * 60 * 60, 24 * 60 * 60];
  return schedule[Math.min(Math.max(Math.trunc(attempt) - 1, 0), schedule.length - 1)];
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function alertCounts(row: Pick<AlertEventRow, "queued_count" | "sent_count" | "skipped_count" | "failed_count">) {
  const total = Number(row.queued_count || 0);
  const sent = Number(row.sent_count || 0);
  const skipped = Number(row.skipped_count || 0);
  const failed = Number(row.failed_count || 0);
  return {
    total,
    pending: Math.max(total - sent - skipped - failed, 0),
    sent,
    skipped,
    failed,
  };
}

function deliveryErrorMessage(error: unknown): string {
  const redacted = errorMessage(error)
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[redacted-email]")
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, "[redacted-ip]");
  return boundedText(redacted, "smtp_delivery_failed", 500);
}

function structuredLog(
  severity: "info" | "error",
  message: string,
  fields: Record<string, JsonValue>,
): void {
  const entry = JSON.stringify({ message, ...fields });
  if (severity === "error") console.error(entry);
  else console.log(entry);
}
