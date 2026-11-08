import { boundedText, isRecord, valueAsString } from "./input.ts";
import type { D1Database } from "./db.ts";
import { campaignProgressFromRows, campaignStatusFromProgress } from "./campaign.ts";

const EVENT_ID = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$/;
const EVENT_TYPES = new Set(["delivered", "deferred", "bounced", "complained"]);

export interface DeliveryFeedback {
  provider: string;
  providerEventId: string;
  providerMessageId: string;
  correlationId: string;
  eventType: "delivered" | "deferred" | "bounced" | "complained";
  errorCode: string;
  occurredAt: string;
}

export function normalizeDeliveryFeedback(value: unknown): DeliveryFeedback | null {
  if (!isRecord(value)) return null;
  const provider = boundedText(valueAsString(value.provider).toLowerCase(), "", 80);
  const providerEventId = valueAsString(value.event_id).trim();
  const providerMessageId = boundedText(valueAsString(value.provider_message_id), "", 240);
  const correlationId = boundedText(valueAsString(value.correlation_id), "", 240);
  const eventType = valueAsString(value.event_type).trim().toLowerCase();
  const errorCode = boundedText(valueAsString(value.error_code), "", 160);
  const occurredText = valueAsString(value.occurred_at).trim();
  const occurred = new Date(occurredText);
  if (
    !provider
    || !EVENT_ID.test(providerEventId)
    || (!providerMessageId && !correlationId)
    || !EVENT_TYPES.has(eventType)
    || !occurredText
    || !Number.isFinite(occurred.valueOf())
    || Math.abs(Date.now() - occurred.valueOf()) > 366 * 24 * 60 * 60 * 1000
  ) return null;
  return {
    provider,
    providerEventId,
    providerMessageId,
    correlationId,
    eventType: eventType as DeliveryFeedback["eventType"],
    errorCode,
    occurredAt: occurred.toISOString(),
  };
}

export async function applyDeliveryFeedback(
  db: D1Database,
  feedback: DeliveryFeedback,
  now = new Date().toISOString(),
): Promise<{ duplicate: boolean; campaignRows: number; transactionalRows: number; suppressedContacts: number }> {
  const existing = await db.prepare(
    "SELECT id FROM email_delivery_events WHERE provider = ? AND provider_event_id = ?",
  ).bind(feedback.provider, feedback.providerEventId).first<{ id: string }>();
  if (existing) return { duplicate: true, campaignRows: 0, transactionalRows: 0, suppressedContacts: 0 };

  const terminalFailure = feedback.eventType === "bounced" || feedback.eventType === "complained";
  const status = feedback.eventType === "delivered"
    ? "delivered"
    : terminalFailure ? "failed" : "deferred";
  const deliveredAt = feedback.eventType === "delivered" ? feedback.occurredAt : null;
  const failedAt = terminalFailure ? feedback.occurredAt : null;
  const error = terminalFailure ? (feedback.errorCode || feedback.eventType) : null;
  const match = `(provider_message_id = ? OR id = ?)`;
  // The callback may arrive before the SMTP success transaction stores the
  // provider ID. The transaction ID is attached to the campaign row before
  // invoking SMTP, so correlation still identifies the in-flight message.
  const campaignMatch = `(provider_message_id = ? OR id = ? OR transaction_delivery_id = ?)`;
  const providerId = feedback.providerMessageId || "";
  const correlationId = feedback.correlationId || "";
  const statusGuard = feedback.eventType === "delivered"
    ? "AND status NOT IN ('failed', 'skipped')"
    : feedback.eventType === "deferred"
      ? "AND status NOT IN ('delivered', 'failed', 'skipped')"
      : "";

  const campaign = await db.prepare(
    `UPDATE message_deliveries SET status = ?, delivered_at = COALESCE(?, delivered_at),
       failed_at = COALESCE(?, failed_at), last_error = ?
     WHERE ${campaignMatch} ${statusGuard}`,
  ).bind(status, deliveredAt, failedAt, error, providerId, correlationId, correlationId).run() as { meta?: { changes?: number } };
  const transactional = await db.prepare(
    `UPDATE transactional_email_deliveries SET status = ?, delivered_at = COALESCE(?, delivered_at),
       failed_at = COALESCE(?, failed_at), error_code = ?, updated_at = ?
     WHERE ${match} ${statusGuard}`,
  ).bind(status, deliveredAt, failedAt, error, now, providerId, correlationId).run() as { meta?: { changes?: number } };

  // Confirmation jobs also have a durable transaction ID before SMTP starts.
  // Provider feedback settles an uncertain job and invalidates the old claim;
  // a late SMTP completion must not move it back to sending or resend it.
  const confirmationStatus = terminalFailure ? "failed" : "sent";
  const confirmationGuard = terminalFailure ? "AND status <> 'cancelled'" : "AND status NOT IN ('failed', 'cancelled')";
  await db.prepare(
    `UPDATE confirmation_email_outbox SET status=?, last_error=?, updated_at=?,
       claim_token=NULL, claim_expires_at=NULL, next_attempt_at=NULL
     WHERE (transaction_delivery_id=? OR transaction_delivery_id IN
       (SELECT id FROM transactional_email_deliveries WHERE provider_message_id=?)) ${confirmationGuard}`,
  ).bind(confirmationStatus, error, now, correlationId, providerId).run();

  // An alert stores the transaction ID before SMTP starts. A provider callback
  // can therefore settle an in-flight or ambiguous delivery before the SMTP
  // request returns. A defer means the provider accepted responsibility; it is
  // never a signal to submit another copy to SMTP.
  const alertStatus = terminalFailure ? "dead_letter" : "sent";
  const alertGuard = terminalFailure ? "AND status <> 'skipped'" : "AND status NOT IN ('dead_letter', 'skipped')";
  const alertMatch = `(transaction_delivery_id = ? OR transaction_delivery_id IN
    (SELECT id FROM transactional_email_deliveries WHERE provider_message_id = ?))`;
  await db.prepare(
    `UPDATE situation_alert_deliveries SET status = ?, sent_at = COALESCE(?, sent_at),
       failed_at = COALESCE(?, failed_at), last_error = ?, updated_at = ?,
       claim_token = NULL, claim_expires_at = NULL
     WHERE ${alertMatch} ${alertGuard}`,
  ).bind(alertStatus, terminalFailure ? null : feedback.occurredAt, failedAt, error, now, correlationId, providerId).run();
  const affectedAlerts = await db.prepare(
    `SELECT DISTINCT event_id FROM situation_alert_deliveries WHERE ${alertMatch} LIMIT 20`,
  ).bind(correlationId, providerId).all<{ event_id: string }>();
  for (const row of affectedAlerts.results || []) {
    const rows = await db.prepare("SELECT status, COUNT(*) AS count FROM situation_alert_deliveries WHERE event_id = ? GROUP BY status")
      .bind(row.event_id).all<{ status: string; count: number }>();
    const counts = Object.fromEntries((rows.results || []).map(item => [item.status, Number(item.count || 0)]));
    const sent = (counts.sent || 0) + (counts.delivered || 0), skipped = counts.skipped || 0;
    const failed = (counts.dead_letter || 0) + (counts.unknown || 0);
    const pending = (counts.queued || 0) + (counts.retry || 0) + (counts.sending || 0) + (counts.deferred || 0);
    const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
    const parentStatus = total === 0 ? "completed_no_audience" : pending ? (counts.sending ? "sending" : "queued")
      : failed ? (sent ? "partial_failed" : "failed") : sent ? "sent" : "skipped";
    await db.prepare(
      `UPDATE situation_alert_events SET status=?, queued_count=?, sent_count=?, skipped_count=?, failed_count=?,
       completed_at=?, updated_at=?, last_error=? WHERE id=?`,
    ).bind(parentStatus, total, sent, skipped, failed, pending ? null : now, now,
      failed ? "delivery_dead_letter" : null, row.event_id).run();
  }

  const affectedCampaigns = await db.prepare(
    `SELECT DISTINCT campaign_id FROM message_deliveries WHERE ${campaignMatch} LIMIT 20`,
  ).bind(providerId, correlationId, correlationId).all<{ campaign_id: string }>();
  for (const row of affectedCampaigns.results || []) {
    if (!row.campaign_id) continue;
    const statusRows = await db.prepare(
      "SELECT status, COUNT(*) AS count FROM message_deliveries WHERE campaign_id = ? GROUP BY status",
    ).bind(row.campaign_id).all<{ status: string; count: number }>();
    const progress = campaignProgressFromRows(statusRows.results || []);
    const campaignStatus = campaignStatusFromProgress(progress);
    const finishedAt = progress.queued === 0 && progress.deferred === 0 ? now : null;
    await db.prepare(
      `UPDATE message_campaigns SET status = ?,
       sent_at = CASE WHEN ? IS NOT NULL THEN COALESCE(sent_at, ?) ELSE sent_at END
       WHERE id = ?`,
    ).bind(campaignStatus, finishedAt, finishedAt, row.campaign_id).run();
  }

  let suppressedContacts = 0;
  if (terminalFailure) {
    const suppressed = await db.prepare(
      `UPDATE subscriber_contacts SET status = 'suppressed', updated_at = ?
       WHERE id IN (
         SELECT contact_id FROM transactional_email_deliveries
         WHERE ${match}
         UNION
         SELECT contact_id FROM message_deliveries
         WHERE ${campaignMatch}
       )`,
    ).bind(now, providerId, correlationId, providerId, correlationId, correlationId).run() as { meta?: { changes?: number } };
    suppressedContacts = Number(suppressed.meta?.changes || 0);
  }
  // Record the idempotency marker only after state application. If a D1 write
  // fails before this point, a provider retry can safely apply the event again.
  await db.prepare(
    `INSERT INTO email_delivery_events (
       id, provider, provider_event_id, provider_message_id, correlation_id,
       event_type, error_code, occurred_at, created_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    crypto.randomUUID(), feedback.provider, feedback.providerEventId,
    feedback.providerMessageId || null, feedback.correlationId || null,
    feedback.eventType, feedback.errorCode || null, feedback.occurredAt, now,
  ).run();
  return {
    duplicate: false,
    campaignRows: Number(campaign.meta?.changes || 0),
    transactionalRows: Number(transactional.meta?.changes || 0),
    suppressedContacts,
  };
}
