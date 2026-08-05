import { boundedText, normalizeEmail, valueAsString } from "./input.ts";

export interface SmtpConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  fromEmail: string;
  fromName: string;
  useTls: boolean;
}

export interface SmtpEnvironment {
  SMTP_HOST?: string;
  SMTP_PORT?: string;
  SMTP_USERNAME?: string;
  SMTP_PASSWORD?: string;
  SMTP_FROM_EMAIL?: string;
  SMTP_FROM_NAME?: string;
  SMTP_USE_TLS?: string;
}

export function smtpConfig(env: SmtpEnvironment): SmtpConfig | null {
  const host = valueAsString(env.SMTP_HOST);
  const port = Number(valueAsString(env.SMTP_PORT) || "587");
  const username = valueAsString(env.SMTP_USERNAME);
  const password = valueAsString(env.SMTP_PASSWORD);
  const fromEmail = normalizeEmail(valueAsString(env.SMTP_FROM_EMAIL));
  const fromName = boundedText(valueAsString(env.SMTP_FROM_NAME), "GIDS Alerts", 80);
  const useTls = valueAsString(env.SMTP_USE_TLS).toLowerCase() !== "false";

  if (!host || !Number.isFinite(port) || port <= 0 || !username || !password || !fromEmail) return null;
  return { host, port, username, password, fromEmail, fromName, useTls };
}
