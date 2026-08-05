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

export async function updateEmailDelivery(db: D1Database, input: {
  deliveryId: string;
  status: string;
  sentAt?: string;
  errorCode?: string;
  errorMessage?: string;
  now?: string;
}): Promise<void> {
  try {
    await db.prepare(
      `UPDATE transactional_email_deliveries
       SET status = ?, sent_at = COALESCE(?, sent_at), error_code = ?, error_message = ?, updated_at = ?
       WHERE id = ?`
    ).bind(
      input.status,
      input.sentAt || null,
      input.errorCode || null,
      input.errorMessage || null,
      input.now || new Date().toISOString(),
      input.deliveryId,
    ).run();
  } catch {
    // Delivery logs are useful but must not block API responses.
  }
}
