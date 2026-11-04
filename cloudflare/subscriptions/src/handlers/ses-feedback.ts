import { isRecord } from "../lib/input.ts";
import { applyDeliveryFeedback, normalizeDeliveryFeedback, type DeliveryFeedback } from "../lib/delivery-feedback.ts";
import { confirmationUrl, parseEnvelope, readLimited, SnsError, verifyEnvelope, type SnsEnvelope } from "../lib/sns.ts";
import type { Env } from "../types.ts";

const CORRELATION = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PROVIDER_ID = /^[A-Za-z0-9._@+-]{6,240}$/;

/** Extract only the bounded data needed for delivery accounting. SES SMTP is
 * one recipient per message in this application; multi-recipient events are
 * rejected rather than suppressing a whole message for one recipient. */
export function normalizeSesFeedback(envelope: SnsEnvelope): DeliveryFeedback | null {
  let raw: unknown;
  try { raw = JSON.parse(envelope.Message); } catch { throw new SnsError("invalid_ses_message"); }
  if (!isRecord(raw) || !isRecord(raw.mail)) throw new SnsError("invalid_ses_message");
  const mail = raw.mail;
  const messageId = typeof mail.messageId === "string" ? mail.messageId : "";
  if (!PROVIDER_ID.test(messageId)) throw new SnsError("invalid_ses_message_id");
  if (mail.destination !== undefined && (!Array.isArray(mail.destination) || mail.destination.length > 1)) {
    throw new SnsError("invalid_ses_recipient_count");
  }
  const kind = typeof raw.eventType === "string" ? raw.eventType : raw.notificationType;
  let eventType: string; let details: Record<string, unknown>; let errorCode = "";
  if (kind === "Delivery") { if (!isRecord(raw.delivery)) throw new SnsError("invalid_ses_delivery"); eventType = "delivered"; details = raw.delivery; }
  else if (kind === "Complaint") { if (!isRecord(raw.complaint)) throw new SnsError("invalid_ses_complaint"); eventType = "complained"; details = raw.complaint; }
  else if (kind === "DeliveryDelay") { if (!isRecord(raw.deliveryDelay)) throw new SnsError("invalid_ses_delay"); eventType = "deferred"; details = raw.deliveryDelay; }
  else if (kind === "Bounce") {
    details = isRecord(raw.bounce) ? raw.bounce : {};
    if (details.bounceType !== "Permanent" && details.bounceType !== "Transient" && details.bounceType !== "Undetermined") {
      throw new SnsError("invalid_ses_bounce_type");
    }
    eventType = details.bounceType === "Permanent" ? "bounced" : "deferred";
    errorCode = eventType === "bounced" ? "ses_permanent_bounce" : "";
  } else return null;
  for (const key of ["recipients", "bouncedRecipients", "complainedRecipients", "delayedRecipients"]) {
    if (details[key] !== undefined && (!Array.isArray(details[key]) || details[key].length > 1)) throw new SnsError("invalid_ses_recipient_count");
  }
  const tags = isRecord(mail.tags) ? mail.tags : {};
  const tag = tags["gids-delivery"];
  if (tag !== undefined && (!Array.isArray(tag) || tag.length !== 1 || typeof tag[0] !== "string" || !CORRELATION.test(tag[0]))) {
    throw new SnsError("invalid_ses_correlation");
  }
  const headers = Array.isArray(mail.headers) ? mail.headers : [];
  const matching = headers.filter(item => isRecord(item) && typeof item.name === "string" && item.name.toLowerCase() === "x-gids-delivery-id");
  if (matching.length > 1) throw new SnsError("invalid_ses_correlation");
  const header = matching.length && isRecord(matching[0]) && typeof matching[0].value === "string" ? matching[0].value : "";
  if (header && !CORRELATION.test(header)) throw new SnsError("invalid_ses_correlation");
  const correlation = Array.isArray(tag) ? tag[0] as string : header;
  if (header && correlation && header.toLowerCase() !== correlation.toLowerCase()) throw new SnsError("invalid_ses_correlation");
  const feedback = normalizeDeliveryFeedback({
    provider: "amazon-ses", event_id: envelope.MessageId, provider_message_id: messageId,
    correlation_id: correlation, event_type: eventType, error_code: errorCode,
    occurred_at: typeof details.timestamp === "string" ? details.timestamp : envelope.Timestamp,
  });
  if (!feedback) throw new SnsError("invalid_ses_feedback");
  return feedback;
}

async function recordOrPend(env: Env, feedback: DeliveryFeedback): Promise<void> {
  const applied = await env.DB.prepare("SELECT id FROM email_delivery_events WHERE provider=? AND provider_event_id=?")
    .bind(feedback.provider, feedback.providerEventId).first<{ id: string }>();
  if (applied) return;
  const known = await env.DB.prepare(
    "SELECT id,provider_message_id FROM transactional_email_deliveries WHERE id=? OR provider_message_id=? LIMIT 1",
  ).bind(feedback.correlationId || "", feedback.providerMessageId).first<{ id: string; provider_message_id: string | null }>();
  if (!known) {
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO ses_feedback_pending (event_id,provider_message_id,correlation_id,event_type,error_code,occurred_at,created_at,next_attempt_at)
       VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING`,
    ).bind(feedback.providerEventId, feedback.providerMessageId, feedback.correlationId, feedback.eventType,
      feedback.errorCode, feedback.occurredAt, now, now).run();
    return;
  }
  assertMatchingReceipt(known, feedback);
  await applyDeliveryFeedback(env.DB, feedback);
  await env.DB.prepare("DELETE FROM ses_feedback_pending WHERE event_id=?").bind(feedback.providerEventId).run();
}

function assertMatchingReceipt(known: { id: string; provider_message_id: string | null }, feedback: DeliveryFeedback): void {
  if (feedback.correlationId && known.id !== feedback.correlationId) throw new SnsError("ses_correlation_conflict", 403);
  const recorded = known.provider_message_id || "";
  const localMessageId = `${known.id}@globalinfectiousdisease.com`;
  if (recorded && recorded !== feedback.providerMessageId && recorded !== localMessageId) throw new SnsError("ses_message_id_conflict", 403);
}

export async function reconcileSesFeedback(env: Env): Promise<void> {
  if (env.SES_SNS_FEEDBACK_ENABLED !== "true") return;
  const now = new Date().toISOString();
  const rows = await env.DB.prepare(
    `SELECT event_id,provider_message_id,correlation_id,event_type,error_code,occurred_at,created_at,attempts
     FROM ses_feedback_pending WHERE next_attempt_at<=? ORDER BY next_attempt_at,created_at LIMIT 1`,
  ).bind(now).all<{ event_id: string; provider_message_id: string; correlation_id: string; event_type: string;
    error_code: string; occurred_at: string; created_at: string; attempts: number }>();
  for (const row of rows.results || []) {
    if (Date.now() - Date.parse(row.created_at) > 30 * 24 * 60 * 60 * 1000) {
      await env.DB.prepare("DELETE FROM ses_feedback_pending WHERE event_id=?").bind(row.event_id).run();
      console.error(JSON.stringify({ message: "ses_feedback_expired_unmatched", event_id: row.event_id }));
      continue;
    }
    const known = await env.DB.prepare("SELECT id,provider_message_id FROM transactional_email_deliveries WHERE id=? OR provider_message_id=? LIMIT 1")
      .bind(row.correlation_id, row.provider_message_id).first<{ id: string; provider_message_id: string | null }>();
    if (!known) {
      const delay = Math.min(3600, 30 * 2 ** Math.min(Number(row.attempts), 7));
      await env.DB.prepare("UPDATE ses_feedback_pending SET attempts=attempts+1,next_attempt_at=? WHERE event_id=?")
        .bind(new Date(Date.now() + delay * 1000).toISOString(), row.event_id).run();
      continue;
    }
    const feedback = normalizeDeliveryFeedback({ provider: "amazon-ses", ...row });
    if (!feedback) throw new Error("invalid_stored_ses_feedback");
    try { assertMatchingReceipt(known, feedback); } catch {
      await env.DB.prepare("DELETE FROM ses_feedback_pending WHERE event_id=?").bind(row.event_id).run();
      console.error(JSON.stringify({ message: "ses_feedback_correlation_conflict", event_id: row.event_id }));
      continue;
    }
    await applyDeliveryFeedback(env.DB, feedback);
    await env.DB.prepare("DELETE FROM ses_feedback_pending WHERE event_id=?").bind(row.event_id).run();
  }
}

export async function handleSesFeedback(request: Request, env: Env, send: typeof fetch = fetch): Promise<Response> {
  if (env.SES_SNS_FEEDBACK_ENABLED !== "true") return new Response(null, { status: 404 });
  const topic = env.SES_SNS_TOPIC_ARN || "";
  if (!topic) return new Response(null, { status: 503 });
  try {
    const length = Number(request.headers.get("content-length") || 0);
    if (Number.isFinite(length) && length > 262144) throw new SnsError("sns_body_too_large", 413);
    const bytes = await readLimited(new Response(request.body), 262144);
    let value: unknown;
    try { value = JSON.parse(new TextDecoder().decode(bytes)); } catch { throw new SnsError("invalid_sns_message"); }
    const envelope = parseEnvelope(value, topic);
    await verifyEnvelope(envelope, send);
    if (envelope.Type === "SubscriptionConfirmation") {
      const url = confirmationUrl(envelope);
      const response = await send(url.toString(), { method: "GET", redirect: "error", signal: AbortSignal.timeout(8000) });
      if (!response.ok || response.redirected) throw new SnsError("sns_confirmation_failed", 503);
      await response.body?.cancel();
    } else if (envelope.Type === "Notification") {
      const feedback = normalizeSesFeedback(envelope);
      if (feedback) await recordOrPend(env, feedback);
    }
    return new Response(null, { status: 204, headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof SnsError) {
      console.error(JSON.stringify({ message: "ses_sns_feedback_rejected", reason: error.message, status: error.status }));
      return new Response(null, { status: error.status });
    }
    console.error(JSON.stringify({ message: "ses_sns_feedback_failed" }));
    return new Response(null, { status: 503 });
  }
}
