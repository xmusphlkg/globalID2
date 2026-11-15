import { buildNotificationEmail } from "./email.ts";
import { HttpError, configInt, publicBaseUrl } from "./http.ts";
import { parseJsonObject } from "./input.ts";

import type { Env } from "../types.ts";

import type { Dependencies, AlertProcessResult } from "./situation-alert-types.ts";
import { errorMessage, deliveryErrorMessage, structuredLog } from "./situation-alert-observability.ts";
import { parseDispatchAlert, dispatchSituationAlert, type DispatchAlert } from "./situation-alert-dispatch.ts";
import { dueDeliveryIds, hasDueSituationAlertDeliveries, claimDelivery, recoverExpiredClaims, markDeliverySkipped, markDeliveryDeadLetter, refreshSituationAlertStatus } from "./situation-alert-repository.ts";
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

export function configuredPublicBaseUrl(env: Env): string {
  if (!env.PUBLIC_BASE_URL) throw new HttpError(500, "PUBLIC_BASE_URL_not_configured");
  const url = new URL(env.PUBLIC_BASE_URL);
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !local) {
    throw new HttpError(500, "PUBLIC_BASE_URL_https_required");
  }
  return publicBaseUrl(new Request(url), env);
}

export function retryDelaySeconds(attempt: number): number {
  const schedule = [60, 5 * 60, 30 * 60, 2 * 60 * 60, 6 * 60 * 60, 24 * 60 * 60];
  return schedule[Math.min(Math.max(Math.trunc(attempt) - 1, 0), schedule.length - 1)];
}
