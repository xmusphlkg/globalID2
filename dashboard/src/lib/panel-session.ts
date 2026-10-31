import { createHmac, createHash, timingSafeEqual } from "node:crypto";

export const PANEL_SESSION_COOKIE = "gids_panel_session";
export const PANEL_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7;

function envValue(name: string): string {
  // Dynamic lookup so `next build` does not bake the secret into the bundle.
  return (process.env[name] || "").trim();
}

export function panelPassword(): string {
  return envValue("DASHBOARD_PANEL_PASSWORD");
}

export function dashboardApiKey(): string {
  return envValue("DASHBOARD_API_KEY");
}

export function passwordMatches(provided: string): boolean {
  const expected = panelPassword();
  if (!expected) return false;
  const actual = createHash("sha256").update(provided).digest();
  const wanted = createHash("sha256").update(expected).digest();
  return timingSafeEqual(actual, wanted);
}

export function issueSession(nowMs = Date.now()): string {
  const expiresAt = Math.floor(nowMs / 1000) + PANEL_SESSION_MAX_AGE_SECONDS;
  const signature = createHmac("sha256", panelPassword()).update(String(expiresAt)).digest("base64url");
  return `${expiresAt}.${signature}`;
}

export function sessionValid(token: string | undefined | null, nowMs = Date.now()): boolean {
  if (!panelPassword()) return true;
  if (!token) return false;
  const dot = token.indexOf(".");
  if (dot <= 0) return false;
  const expiresAt = Number(token.slice(0, dot));
  const signature = token.slice(dot + 1);
  if (!Number.isFinite(expiresAt) || expiresAt * 1000 <= nowMs || !signature) return false;
  const expected = createHmac("sha256", panelPassword()).update(String(expiresAt)).digest("base64url");
  const actualBytes = Buffer.from(signature);
  const expectedBytes = Buffer.from(expected);
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes);
}
