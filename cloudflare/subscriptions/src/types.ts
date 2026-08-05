import type { D1Database } from "./lib/db.ts";

export interface Env {
  DB: D1Database;
  PUBLIC_BASE_URL?: string;
  ALLOWED_ORIGINS?: string;
  DEBUG_RETURN_TOKENS?: string;
  TURNSTILE_SECRET_KEY?: string;
  TOKEN_SIGNING_SECRET?: string;
  ADMIN_API_TOKEN?: string;
  SMTP_HOST?: string;
  SMTP_PORT?: string;
  SMTP_USERNAME?: string;
  SMTP_PASSWORD?: string;
  SMTP_FROM_EMAIL?: string;
  SMTP_FROM_NAME?: string;
  SMTP_USE_TLS?: string;
  PENDING_EXPIRY_DAYS?: string;
  SUBMISSION_RATE_LIMIT_PER_HOUR?: string;
  CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES?: string;
  NOTIFICATION_BATCH_SIZE?: string;
}

export type Payload = Record<string, unknown>;
