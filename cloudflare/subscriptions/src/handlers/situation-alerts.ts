import { deliveryMode, recipientSql } from "../lib/pilot.ts";
import { buildNotificationEmail } from "../lib/email.ts";
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
import { officialNoticeContent, parseOfficialNoticeAlert, type OfficialNoticeAlert } from "../lib/official-notice-alert.ts";
import { isRecord } from "../lib/input.ts";

type DispatchAlert = {
  stored: SituationAlertPayload | OfficialNoticeAlert;
  idempotencyKey: string; reportId: string; signalId: string;
  basis: string; policyVersion: string | null; publicUrl: string;
  countries: string[]; diseases: string[]; severity: string[];
  content(locale: string): { subject: string; markdown: string };
};

function parseDispatchAlert(value: unknown): DispatchAlert {
  if (isRecord(value) && value.schema_version === "official-notice-alert.v1") {
    const notice = parseOfficialNoticeAlert(value);
    return {
      stored: notice, idempotencyKey: notice.idempotency_key,
      reportId: notice.release_id, signalId: notice.alert_id,
      basis: "official_authority_notice", policyVersion: null,
      publicUrl: notice.public_url, countries: notice.countries, diseases: notice.diseases,
      severity: [], content: (locale) => officialNoticeContent(notice, locale),
    };
  }
  const alert = parseSituationAlertPayload(value);
  return {
    stored: alert, idempotencyKey: alert.idempotency_key,
    reportId: alert.report.report_id, signalId: alert.signal.signal_id,
    basis: alert.signal.verification_basis, policyVersion: alert.signal.verification_policy_version,
    publicUrl: alert.report.public_url, countries: alert.signal.countries,
    diseases: alert.signal.diseases, severity: [alert.signal.anomaly_state],
    content: (locale) => situationAlertContent(alert, locale),
  };
}

const MAX_PAYLOAD_BYTES = 64 * 1024;

type Dependencies = {
  createSignedToken(
    env: Env,
    purpose: "unsubscribe" | "manage",
    subscriptionId: string,
    ttlSeconds: number,
  ): Promise<string>;
  smtpConfig(env: Env): SmtpConfig | null;
  sendSmtpEmail(config: SmtpConfig, message: {
    to: string;
    subject: string;
    text: string;
    html: string;
    messageId?: string;
    unsubscribeUrl?: string;
  }): Promise<void | { providerMessageId: string | null }>;
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
  claim_token: string;
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
  unknown: number;
  reason?: string;
}

export function createSituationAlertHandlers(deps: Dependencies) {
  async function ingest(request: Request, env: Env): Promise<Response> {
    await requireSituationAlertIngest(request, env);
    const raw = await readBoundedJson(request);
    let alert: DispatchAlert;
    try {
      alert = parseDispatchAlert(raw);
    } catch (error) {
      if (error instanceof SituationAlertValidationError) {
        throw new HttpError(422, error.message);
      }
      throw error;
    }
    if (alert.idempotencyKey.startsWith("pilot:") !== (deliveryMode(env) === "pilot")) {
      throw new HttpError(409, "alert_delivery_mode_mismatch");
    }
    if (
      alert.basis === "automated_policy"
      && env.SITUATION_ALERT_AUTOMATED_POLICY_ENABLED?.trim().toLowerCase() !== "true"
    ) {
      // Keep a second deployment-level kill switch after the report's
      // structured v3.2 policy decision has passed contract validation.
      throw new HttpError(422, "automated_policy_dispatch_disabled");
    }
    if (!isAllowedSituationPublicUrl(alert.publicUrl, env.SITUATION_PUBLIC_ORIGINS)) {
      throw new HttpError(422, "untrusted_public_report_origin");
    }

    const payloadJson = JSON.stringify(alert.stored);
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
      alert.idempotencyKey,
      alert.reportId,
      alert.signalId,
      alert.basis,
      alert.policyVersion,
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
    ).bind(alert.idempotencyKey).first<AlertEventRow>();
    if (!event) throw new HttpError(500, "situation_alert_not_persisted");
    if (event.payload_sha256 !== payloadSha256) {
      throw new HttpError(409, "idempotency_key_payload_conflict");
    }

    const duplicate = event.id !== proposedEventId;
    // Replays must not fan out to subscribers who joined after the event.
    // A received event may be a crash between persistence and its first fanout.
    const queued = !duplicate || event.status === "received"
      ? await fanOutSituationAlert(env, event.id, alert, now, event.received_at) : 0;
    const dispatch = queued > 0 ? await dispatchSituationAlert(env, event.id) : "not_required";
    structuredLog("info", "situation_alert_accepted", {
      event_id: event.id,
      report_id: event.report_id,
      signal_id: event.signal_id,
      verification_basis: alert.basis,
      verification_policy_version: alert.policyVersion,
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
      unknown: 0,
      reason: "smtp_not_configured",
    };
  }

  const now = new Date().toISOString();
  await recoverExpiredClaims(env, now);
  // Keep a single D1 invocation below the Free plan's query budget.
  const batchSize = configInt(env.SITUATION_ALERT_BATCH_SIZE, 3, 1, 3);
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
    unknown: 0,
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
      await markDeliverySkipped(env, claimed.id, claimed.claim_token, "subscription_inactive");
      result.skipped += 1;
      continue;
    }

    let alert: DispatchAlert;
    try {
      alert = parseDispatchAlert(parseJsonObject(claimed.payload_json));
    } catch (error) {
      await markDeliveryDeadLetter(env, claimed.id, claimed.claim_token, `stored_payload_invalid:${errorMessage(error)}`);
      result.dead_letter += 1;
      continue;
    }

    const locale = claimed.locale || "en";
    const content = alert.content(locale);
    const unsubscribeToken = await deps.createSignedToken(
      env,
      "unsubscribe",
      claimed.subscription_id,
      60 * 60 * 24 * 365,
    );
    const unsubscribeUrl = `${configuredPublicBaseUrl(env)}/api/subscriptions/unsubscribe?token=${unsubscribeToken}`;
    const manageToken = await deps.createSignedToken(env, "manage", claimed.subscription_id, 60 * 60 * 24);
    const manageUrl = `${configuredPublicBaseUrl(env)}/api/subscriptions/manage?token=${manageToken}`;
    const email = buildNotificationEmail(locale, content, unsubscribeUrl, manageUrl,
      alert.basis === "official_authority_notice" ? "alert" : "update");
    const transactionId = crypto.randomUUID();
    const attemptTime = new Date().toISOString();
    const deliveryMetadata = JSON.stringify({
        event_id: claimed.event_id,
        outbox_delivery_id: claimed.id,
        report_id: alert.reportId,
        signal_id: alert.signalId,
        verification_basis: alert.basis,
        verification_policy_version: alert.policyVersion,
    });
    try {
      await env.DB.batch([
        env.DB.prepare(
          `INSERT INTO transactional_email_deliveries (
             id, subscriber_id, contact_id, subscription_id, delivery_type, channel, recipient, subject,
             provider, status, attempts, request_source, metadata_json, created_at, updated_at
           ) VALUES (?, ?, ?, ?, 'verified_situation_alert', 'email', ?, ?, 'smtp', 'queued', ?, 'situation_alert_outbox', ?, ?, ?)`,
        ).bind(transactionId, claimed.subscriber_id, claimed.contact_id, claimed.subscription_id,
          claimed.email, email.subject, claimed.attempts, deliveryMetadata, attemptTime, attemptTime),
        env.DB.prepare(
          `UPDATE situation_alert_deliveries SET transaction_delivery_id = ?
           WHERE id = ? AND status = 'sending' AND claim_token = ?`,
        ).bind(transactionId, claimed.id, claimed.claim_token),
      ]);
    } catch (error) {
      await env.DB.prepare(
        `UPDATE situation_alert_deliveries SET status = 'retry', last_error = 'delivery_ledger_unavailable',
         claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`,
      ).bind(claimed.id, claimed.claim_token).run().catch(() => {});
      throw error;
    }
    // A contact can unsubscribe or be suppressed after the original fanout.
    const eligible = await env.DB.prepare(
      `SELECT d.id FROM situation_alert_deliveries d JOIN subscriptions s ON s.id = d.subscription_id
       JOIN subscriber_contacts c ON c.id = d.contact_id JOIN subscribers sub ON sub.id = s.subscriber_id
       WHERE d.id = ? AND d.status = 'sending' AND d.claim_token = ? AND d.claim_expires_at > ?
       AND s.status = 'active' AND c.status = 'active' AND sub.status = 'active'`,
    ).bind(claimed.id, claimed.claim_token, new Date().toISOString()).first<{ id: string }>();
    if (!eligible) {
      await env.DB.batch([
        env.DB.prepare(
          `UPDATE situation_alert_deliveries SET status = 'skipped', last_error = 'subscription_inactive',
           skipped_at = ?, updated_at = ?, claim_token = NULL, claim_expires_at = NULL
           WHERE id = ? AND status = 'sending' AND claim_token = ?`,
        ).bind(new Date().toISOString(), new Date().toISOString(), claimed.id, claimed.claim_token),
        env.DB.prepare(
          `UPDATE transactional_email_deliveries SET status = 'skipped', updated_at = ?
           WHERE id = ? AND status = 'queued'`,
        ).bind(new Date().toISOString(), transactionId),
      ]);
      result.skipped += 1;
      continue;
    }
    let receipt: void | { providerMessageId: string | null };
    try {
      receipt = await deps.sendSmtpEmail(config, {
        to: claimed.email,
        subject: email.subject,
        text: email.text,
        html: email.html,
        unsubscribeUrl,
        messageId: `${transactionId}@globalinfectiousdisease.com`,
      });
    } catch (error) {
      const message = deliveryErrorMessage(error);
      const rejected = /_rejected:([45])\d\d$/.exec(message);
      const retry = rejected?.[1] === "4" && claimed.attempts < maxAttempts;
      const status = retry ? "retry" : rejected ? "dead_letter" : "unknown";
      const delaySeconds = retryDelaySeconds(claimed.attempts);
      const nextAttemptAt = new Date(Date.now() + delaySeconds * 1000).toISOString();
      const failedAt = new Date().toISOString();
      await env.DB.batch([
        env.DB.prepare(`UPDATE situation_alert_deliveries
         SET status = ?, next_attempt_at = ?, last_error = ?, updated_at = ?,
         claim_token = NULL, claim_expires_at = NULL, failed_at = CASE WHEN ? = 'dead_letter' THEN ? ELSE failed_at END
         WHERE id = ? AND status = 'sending' AND claim_token = ?`)
          .bind(status, nextAttemptAt, message, failedAt, status, failedAt, claimed.id, claimed.claim_token),
        env.DB.prepare(`UPDATE transactional_email_deliveries SET status = ?, error_code = ?, error_message = ?, updated_at = ?
          WHERE id = ? AND status = 'queued'`)
          .bind(status === "unknown" ? "unknown" : "failed", status === "unknown" ? "smtp_result_unknown" : "smtp_delivery_failed", message, failedAt, transactionId),
      ]);
      if (status === "unknown") {
        result.unknown += 1;
      } else if (!retry) {
        result.dead_letter += 1;
      } else {
        result.retried += 1;
        await dispatchSituationAlert(env, claimed.event_id, delaySeconds);
      }
      structuredLog("error", "situation_alert_delivery_failed", {
        event_id: claimed.event_id,
        delivery_id: claimed.id,
        attempt: claimed.attempts,
        terminal: !retry,
        error: message,
      });
      continue;
    }
    const sentAt = new Date().toISOString();
    const providerMessageId = receipt?.providerMessageId || `${transactionId}@globalinfectiousdisease.com`;
    await env.DB.batch([
      env.DB.prepare(`UPDATE situation_alert_deliveries
        SET status = 'sent', sent_at = ?, updated_at = ?, last_error = NULL, claim_token = NULL, claim_expires_at = NULL
        WHERE id = ? AND status = 'sending' AND claim_token = ?`)
        .bind(sentAt, sentAt, claimed.id, claimed.claim_token),
      env.DB.prepare(`UPDATE transactional_email_deliveries
        SET status = CASE WHEN status = 'queued' THEN 'sent' ELSE status END,
        sent_at = ?, provider_message_id = ?, updated_at = ? WHERE id = ?`)
        .bind(sentAt, providerMessageId, sentAt, transactionId),
    ]);
    result.sent += 1;
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
    `SELECT id, status, payload_json, received_at FROM situation_alert_events
     WHERE status IN ('received', 'queued', 'sending') ORDER BY updated_at ASC LIMIT 3`,
  ).all<{ id: string; status: string; payload_json: string; received_at: string }>();
  let recoveredEvents = 0;
  for (const event of activeEvents.results || []) {
    if (event.status === "received") {
      let alert: DispatchAlert;
      try {
        alert = parseDispatchAlert(parseJsonObject(event.payload_json));
        if (!isAllowedSituationPublicUrl(alert.publicUrl, env.SITUATION_PUBLIC_ORIGINS)) {
          throw new SituationAlertValidationError("untrusted_public_report_origin");
        }
      } catch (error) {
        if (!(error instanceof SituationAlertValidationError)) throw error;
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
      // A pre-pilot event interrupted before fanout must wait until full mode;
      // otherwise its original audience would be irreversibly narrowed.
      if (deliveryMode(env) === "pilot" && !alert.idempotencyKey.startsWith("pilot:")) continue;
      // A temporary database/queue failure is not a bad stored payload. Let
      // the scheduler retry instead of turning an undelivered event terminal.
      const queued = await fanOutSituationAlert(env, event.id, alert, now, event.received_at);
      if (queued > 0) await dispatchSituationAlert(env, event.id);
      recoveredEvents += 1;
    }
    await refreshSituationAlertStatus(env, event.id);
  }

  const retentionDays = configInt(env.SITUATION_ALERT_RETENTION_DAYS, 180, 7, 3650);
  const cutoff = new Date(Date.now() - retentionDays * 24 * 60 * 60 * 1000).toISOString();
  const expired = await env.DB.prepare(
    `SELECT id FROM situation_alert_events
     WHERE completed_at IS NOT NULL AND completed_at < ? ORDER BY completed_at ASC LIMIT 5`,
  ).bind(cutoff).all<{ id: string }>();
  for (const event of expired.results || []) {
    await env.DB.prepare("DELETE FROM situation_alert_deliveries WHERE event_id = ?").bind(event.id).run();
    await env.DB.prepare("DELETE FROM situation_alert_events WHERE id = ?").bind(event.id).run();
  }
  await env.DB.prepare(
    `DELETE FROM transactional_email_deliveries
     WHERE delivery_type = 'verified_situation_alert' AND created_at < ?
       AND NOT EXISTS (SELECT 1 FROM situation_alert_deliveries d
         WHERE d.transaction_delivery_id = transactional_email_deliveries.id)`,
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
  alert: DispatchAlert,
  now: string,
  receivedAt: string,
): Promise<number> {
  const clauses = [
    "l.code = 'alerts'",
    "s.status = 'active'",
    "s.frequency = 'instant'",
    // A replay after an interrupted fanout must never reach someone who only
    // confirmed the subscription after this event was first persisted.
    "COALESCE(s.confirmed_at, s.created_at) <= ?",
    "c.channel = 'email'",
    "c.status = 'active'",
    "sub.status = 'active'",
  ];
  const binds: unknown[] = [receivedAt];
  // A trial event remains administrator-only even if recovery happens after
  // full release; its idempotency namespace defines its original audience.
  const policy = recipientSql(alert.idempotencyKey.startsWith("pilot:") ? { ...env, DELIVERY_MODE: "pilot" } : env, "c.address");
  clauses.push(policy.sql); binds.push(...policy.binds);
  addFilterClause(clauses, binds, "country", alert.countries);
  addFilterClause(clauses, binds, "disease", alert.diseases);
  addFilterClause(clauses, binds, "severity", alert.severity);
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
  const eventClause = eventId ? "AND d.event_id = ?" : "";
  const policy = recipientSql(env, "c.address");
  const binds: unknown[] = [now];
  if (eventId) binds.push(eventId);
  binds.push(...policy.binds, limit);
  const rows = await env.DB.prepare(
    `SELECT d.id FROM situation_alert_deliveries d JOIN subscriber_contacts c ON c.id=d.contact_id
     WHERE d.status IN ('queued', 'retry') AND d.next_attempt_at <= ? ${eventClause} AND ${policy.sql}
     ORDER BY d.next_attempt_at ASC, d.queued_at ASC LIMIT ?`,
  ).bind(...binds).all<{ id: string }>();
  return rows.results || [];
}

async function hasDueSituationAlertDeliveries(env: Env, eventId: string): Promise<boolean> {
  const policy = recipientSql(env, "c.address");
  const row = await env.DB.prepare(
    `SELECT COUNT(*) AS count FROM situation_alert_deliveries d JOIN subscriber_contacts c ON c.id=d.contact_id
     WHERE d.event_id = ? AND d.status IN ('queued', 'retry') AND d.next_attempt_at <= ? AND ${policy.sql}`,
  ).bind(eventId, new Date().toISOString(), ...policy.binds).first<{ count: number }>();
  return Number(row?.count || 0) > 0;
}

async function claimDelivery(env: Env, deliveryId: string, now: string): Promise<ClaimedDelivery | null> {
  const claimToken = crypto.randomUUID();
  const claimExpiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'sending', attempts = attempts + 1, claim_token = ?, claim_expires_at = ?,
         transaction_delivery_id = NULL, updated_at = ?
     WHERE id = ? AND status IN ('queued', 'retry') AND next_attempt_at <= ?`,
  ).bind(claimToken, claimExpiresAt, now, deliveryId, now).run();
  const delivery = await env.DB.prepare(
    `SELECT d.id, d.event_id, d.subscription_id, d.contact_id, d.attempts,
     e.payload_json, s.subscriber_id, s.status AS subscription_status,
     sub.status AS subscriber_status, c.status AS contact_status, c.address AS email, sub.locale
     FROM situation_alert_deliveries d
     JOIN situation_alert_events e ON e.id = d.event_id
     JOIN subscriptions s ON s.id = d.subscription_id
     JOIN subscribers sub ON sub.id = s.subscriber_id
     JOIN subscriber_contacts c ON c.id = d.contact_id
     WHERE d.id = ? AND d.claim_token = ? AND d.status = 'sending'`,
  ).bind(deliveryId, claimToken).first<Omit<ClaimedDelivery, "claim_token">>();
  return delivery ? { ...delivery, claim_token: claimToken } : null;
}

async function recoverExpiredClaims(env: Env, now: string): Promise<void> {
  await env.DB.batch([
    // No ledger means SMTP was never invoked: this particular crash point is
    // safe to retry. Once a ledger exists, keep the conservative unknown state.
    env.DB.prepare(`UPDATE situation_alert_deliveries
      SET status = 'retry', next_attempt_at = ?, last_error = 'claim_expired_before_send',
      claim_token = NULL, claim_expires_at = NULL, updated_at = ?
      WHERE status = 'sending' AND transaction_delivery_id IS NULL
        AND claim_expires_at IS NOT NULL AND claim_expires_at < ?`)
      .bind(now, now, now),
    env.DB.prepare(`UPDATE transactional_email_deliveries SET status = 'unknown', error_code = 'claim_timeout_unknown', updated_at = ?
      WHERE status = 'queued' AND id IN (SELECT transaction_delivery_id FROM situation_alert_deliveries
        WHERE status = 'sending' AND claim_expires_at IS NOT NULL AND claim_expires_at < ?)`)
      .bind(now, now),
    env.DB.prepare(`UPDATE situation_alert_deliveries
      SET status = 'unknown', last_error = 'claim_timeout_unknown',
      claim_token = NULL, claim_expires_at = NULL, updated_at = ?
      WHERE status = 'sending' AND transaction_delivery_id IS NOT NULL
        AND claim_expires_at IS NOT NULL AND claim_expires_at < ?`)
      .bind(now, now),
  ]);
}

async function markDeliverySkipped(env: Env, deliveryId: string, claimToken: string, reason: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'skipped', last_error = ?, skipped_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`,
  ).bind(reason, now, now, deliveryId, claimToken).run();
}

async function markDeliveryDeadLetter(env: Env, deliveryId: string, claimToken: string, reason: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'dead_letter', last_error = ?, failed_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`,
  ).bind(boundedText(reason, "delivery_invalid", 500), now, now, deliveryId, claimToken).run();
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
  const sent = Number(counts.sent || 0) + Number(counts.delivered || 0);
  const skipped = Number(counts.skipped || 0);
  const failed = Number(counts.dead_letter || 0) + Number(counts.unknown || 0);
  const pending = Number(counts.queued || 0) + Number(counts.retry || 0) + Number(counts.sending || 0) + Number(counts.deferred || 0);
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
