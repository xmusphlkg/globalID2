import { deliveryMode } from "./pilot.ts";

import { configInt, type JsonValue } from "./http.ts";
import { parseJsonObject } from "./input.ts";

import { isAllowedSituationPublicUrl, SituationAlertValidationError } from "./situation-alert.ts";

import type { Env } from "../types.ts";

import { errorMessage, structuredLog } from "./situation-alert-observability.ts";
import { parseDispatchAlert, dispatchSituationAlert, type DispatchAlert } from "./situation-alert-dispatch.ts";
import { fanOutSituationAlert, recoverExpiredClaims, refreshSituationAlertStatus } from "./situation-alert-repository.ts";
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
