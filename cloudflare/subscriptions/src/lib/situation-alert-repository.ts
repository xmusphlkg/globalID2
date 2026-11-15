import { recipientSql } from "./pilot.ts";

import { boundedText } from "./input.ts";

import type { Env } from "../types.ts";

import type { AlertEventRow, ClaimedDelivery } from "./situation-alert-types.ts";
import { type DispatchAlert } from "./situation-alert-dispatch.ts";
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

export async function fanOutSituationAlert(
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

export function addFilterClause(
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

export async function dueDeliveryIds(
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

export async function hasDueSituationAlertDeliveries(env: Env, eventId: string): Promise<boolean> {
  const policy = recipientSql(env, "c.address");
  const row = await env.DB.prepare(
    `SELECT COUNT(*) AS count FROM situation_alert_deliveries d JOIN subscriber_contacts c ON c.id=d.contact_id
     WHERE d.event_id = ? AND d.status IN ('queued', 'retry') AND d.next_attempt_at <= ? AND ${policy.sql}`,
  ).bind(eventId, new Date().toISOString(), ...policy.binds).first<{ count: number }>();
  return Number(row?.count || 0) > 0;
}

export async function claimDelivery(env: Env, deliveryId: string, now: string): Promise<ClaimedDelivery | null> {
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

export async function recoverExpiredClaims(env: Env, now: string): Promise<void> {
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

export async function markDeliverySkipped(env: Env, deliveryId: string, claimToken: string, reason: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'skipped', last_error = ?, skipped_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`,
  ).bind(reason, now, now, deliveryId, claimToken).run();
}

export async function markDeliveryDeadLetter(env: Env, deliveryId: string, claimToken: string, reason: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE situation_alert_deliveries
     SET status = 'dead_letter', last_error = ?, failed_at = ?, updated_at = ?,
     claim_token = NULL, claim_expires_at = NULL WHERE id = ? AND status = 'sending' AND claim_token = ?`,
  ).bind(boundedText(reason, "delivery_invalid", 500), now, now, deliveryId, claimToken).run();
}

export async function refreshSituationAlertStatus(env: Env, eventId: string): Promise<void> {
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

export function alertCounts(row: Pick<AlertEventRow, "queued_count" | "sent_count" | "skipped_count" | "failed_count">) {
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
