import type { JsonValue } from "./http.ts";

export interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<{ results?: T[] }>;
  run(): Promise<unknown>;
}

export interface D1Database {
  prepare(query: string): D1PreparedStatement;
}

export async function insertEmailDelivery(db: D1Database, input: {
  deliveryId: string;
  subscriberId: string;
  contactId: string;
  subscriptionId: string | null;
  recipient: string;
  subject: string;
  deliveryType: string;
  provider: string;
  status: string;
  attempts: number;
  source: string;
  metadata?: JsonValue;
  errorCode?: string;
  errorMessage?: string;
  now: string;
}): Promise<void> {
  try {
    await db.prepare(
      `INSERT INTO transactional_email_deliveries (
         id, subscriber_id, contact_id, subscription_id, delivery_type, channel,
         recipient, subject, provider, status, attempts, request_source,
         error_code, error_message, metadata_json, created_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, 'email', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(
      input.deliveryId,
      input.subscriberId,
      input.contactId,
      input.subscriptionId,
      input.deliveryType,
      input.recipient,
      input.subject,
      input.provider,
      input.status,
      input.attempts,
      input.source,
      input.errorCode || null,
      input.errorMessage || null,
      input.metadata ? JSON.stringify(input.metadata) : null,
      input.now,
      input.now,
    ).run();
  } catch {
    // Delivery logs are useful but must not block subscription creation.
  }
}

/** Atomically reserve one confirmation send and persist its delivery ledger.
 * D1 serializes the INSERT statement, so concurrent requests cannot both pass
 * a separate COUNT check and exceed the contact's limit. */
export async function reserveConfirmationEmail(db: D1Database, input: {
  deliveryId: string; subscriberId: string; contactId: string; subscriptionId: string | null;
  recipient: string; subject: string; source: string; metadata: JsonValue;
  now: string; cutoff: string; limit: number;
}): Promise<boolean> {
  const row = await db.prepare(
    `INSERT INTO transactional_email_deliveries (
       id, subscriber_id, contact_id, subscription_id, delivery_type, channel,
       recipient, subject, provider, status, attempts, request_source, metadata_json, created_at, updated_at
     ) SELECT ?, ?, ?, ?, 'subscription_confirmation', 'email', ?, ?, 'smtp', 'queued', 1, ?, ?, ?, ?
     WHERE (SELECT COUNT(*) FROM transactional_email_deliveries
       WHERE contact_id = ? AND delivery_type = 'subscription_confirmation' AND created_at >= ?
       AND status IN ('queued', 'sent', 'failed', 'delivered', 'deferred')) < ? RETURNING id`,
  ).bind(input.deliveryId, input.subscriberId, input.contactId, input.subscriptionId,
    input.recipient, input.subject, input.source, JSON.stringify(input.metadata), input.now, input.now,
    input.contactId, input.cutoff, input.limit).first<{ id: string }>();
  return Boolean(row);
}

export async function updateEmailDelivery(db: D1Database, input: {
  deliveryId: string;
  status: string;
  sentAt?: string;
  errorCode?: string;
  errorMessage?: string;
  providerMessageId?: string;
  deliveredAt?: string;
  failedAt?: string;
  now?: string;
}): Promise<void> {
  await db.prepare(
      `UPDATE transactional_email_deliveries
       SET status = CASE
             WHEN ? = 'sent' AND status IN ('delivered', 'deferred', 'failed') THEN status
             WHEN ? = 'failed' AND status IN ('delivered', 'deferred', 'failed') THEN status
             ELSE ? END,
       sent_at = COALESCE(?, sent_at), provider_message_id = COALESCE(?, provider_message_id),
       delivered_at = COALESCE(?, delivered_at),
       failed_at = CASE WHEN ? = 'failed' AND status IN ('delivered', 'deferred') THEN failed_at ELSE COALESCE(?, failed_at) END,
       error_code = CASE
             WHEN ? = 'sent' AND status IN ('delivered', 'deferred', 'failed') THEN error_code
             WHEN ? = 'failed' AND status IN ('delivered', 'deferred', 'failed') THEN error_code ELSE ? END,
       error_message = CASE
             WHEN ? = 'sent' AND status IN ('delivered', 'deferred', 'failed') THEN error_message
             WHEN ? = 'failed' AND status IN ('delivered', 'deferred', 'failed') THEN error_message ELSE ? END,
       updated_at = ?
       WHERE id = ?`
    ).bind(
      input.status,
      input.status,
      input.status,
      input.sentAt || null,
      input.providerMessageId || null,
      input.deliveredAt || null,
      input.status,
      input.failedAt || null,
      input.status,
      input.status,
      input.errorCode || null,
      input.status,
      input.status,
      input.errorMessage || null,
      input.now || new Date().toISOString(),
      input.deliveryId,
    ).run();
}
