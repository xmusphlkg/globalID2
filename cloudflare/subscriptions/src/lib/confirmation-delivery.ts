import { boundedText } from "./input.ts";

import { buildConfirmationEmail, type EmailSubscriptionItem } from "./email.ts";

import { configInt, type JsonValue } from "./http.ts";
import { confirmationRateLimit } from "./rate-limit.ts";

import { insertEmailDelivery as insertEmailDeliveryRecord, reserveConfirmationEmail, updateEmailDelivery as updateEmailDeliveryRecord } from "./db.ts";

import type { Env } from "../types.ts";

import { recordEvent } from "./subscription-events.ts";
export interface EmailDeliveryResult extends Record<string, JsonValue | undefined> {
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

type ConfirmationDependencies = {
  smtpConfig: typeof import("./smtp.ts").smtpConfig;
  sendSmtpEmail: typeof import("./smtp.ts").sendSmtpEmail;
};

export function createConfirmationDelivery({ smtpConfig, sendSmtpEmail }: ConfirmationDependencies) {
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

  return sendAndRecordConfirmationEmail;
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
