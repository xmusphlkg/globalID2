import type { SituationAlertJob } from "./lib/situation-alert.ts";

type OptionalGeneratedBindings = Omit<
  Partial<CloudflareBindings>,
  "DB" | "SITUATION_ALERT_QUEUE"
>;

/**
 * Runtime bindings come from `wrangler types`. Secrets remain explicit because
 * Wrangler intentionally does not write secret names or values into config.
 * Optional non-secret vars preserve the Worker's defensive runtime defaults.
 */
export interface Env extends OptionalGeneratedBindings {
  DB: CloudflareBindings["DB"];
  SITUATION_ALERT_QUEUE?: Queue<SituationAlertJob>;
  TURNSTILE_SECRET_KEY?: string;
  TOKEN_SIGNING_SECRET?: string;
  ADMIN_API_TOKEN?: string;
  SMTP_USERNAME?: string;
  SMTP_PASSWORD?: string;
  SITUATION_ALERT_INGEST_TOKEN?: string;
}

export type Payload = Record<string, unknown>;
