import { type JsonValue } from "./http.ts";

import type { Env } from "../types.ts";

export type Dependencies = {
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

export type SmtpConfig = {
  host: string;
  port: number;
  username: string;
  password: string;
  fromEmail: string;
  fromName: string;
  useTls: boolean;
};

export type AlertEventRow = {
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

export type ClaimedDelivery = {
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
