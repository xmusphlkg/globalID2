import { createHmac, createHash, timingSafeEqual } from "node:crypto";

export const PANEL_SESSION_COOKIE = "gids_panel_session";
export const PANEL_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7;
export const PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10;
export const PANEL_LOGIN_RATE_LIMIT_WINDOW_MS = 5 * 60 * 1000;

const MAX_TRACKED_CLIENTS = 1024;
const failedLoginAttempts = new Map<string, number[]>();

function envValue(name: string): string {
  // Dynamic lookup so `next build` does not bake the secret into the bundle.
  return (process.env[name] || "").trim();
}

export function panelPassword(): string {
  return envValue("DASHBOARD_PANEL_PASSWORD");
}

export function sessionSecret(): string {
  const explicit = envValue("DASHBOARD_SESSION_SECRET");
  if (explicit) return explicit;
  const password = panelPassword();
  if (!password) return "";
  return createHash("sha256").update(`gids-panel-session-v1:${password}`).digest("hex");
}

export function dashboardApiKey(): string {
  return envValue("DASHBOARD_API_KEY");
}

export function isHttpsRequest(request: Request): boolean {
  const forwardedProto = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim().toLowerCase();
  if (forwardedProto === "https") return true;
  try {
    return new URL(request.url).protocol === "https:";
  } catch {
    return false;
  }
}

export function extractClientKey(request: Request): string {
  const forwardedFor = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (forwardedFor) return forwardedFor;
  const realIp = request.headers.get("x-real-ip")?.trim();
  if (realIp) return realIp;
  const cfIp = request.headers.get("cf-connecting-ip")?.trim();
  if (cfIp) return cfIp;
  return "local";
}

function pruneClientWindow(clientKey: string, nowMs: number, windowMs: number): number[] {
  const cutoff = nowMs - windowMs;
  const existing = failedLoginAttempts.get(clientKey);
  if (!existing) return [];
  const active = existing.filter((ts) => ts > cutoff);
  if (active.length === 0) {
    failedLoginAttempts.delete(clientKey);
  } else if (active.length !== existing.length) {
    failedLoginAttempts.set(clientKey, active);
  }
  return active;
}

function evictExpiredClients(nowMs: number, windowMs: number): void {
  if (failedLoginAttempts.size < MAX_TRACKED_CLIENTS) return;
  const cutoff = nowMs - windowMs;
  for (const [key, timestamps] of failedLoginAttempts.entries()) {
    const active = timestamps.filter((ts) => ts > cutoff);
    if (active.length === 0) {
      failedLoginAttempts.delete(key);
    } else {
      failedLoginAttempts.set(key, active);
    }
  }
}

export type LoginRateLimitStatus = {
  limited: boolean;
  retryAfterSeconds: number;
  remaining: number;
};

export function checkLoginRateLimit(
  clientKey: string,
  nowMs = Date.now(),
  maxAttempts = PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
  windowMs = PANEL_LOGIN_RATE_LIMIT_WINDOW_MS,
): LoginRateLimitStatus {
  const active = pruneClientWindow(clientKey, nowMs, windowMs);
  if (active.length >= maxAttempts) {
    const retryAfterSeconds = Math.max(1, Math.ceil((active[0] + windowMs - nowMs) / 1000));
    return { limited: true, retryAfterSeconds, remaining: 0 };
  }
  return { limited: false, retryAfterSeconds: 0, remaining: Math.max(0, maxAttempts - active.length) };
}

export function recordFailedLoginAttempt(
  clientKey: string,
  nowMs = Date.now(),
  maxAttempts = PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
  windowMs = PANEL_LOGIN_RATE_LIMIT_WINDOW_MS,
): LoginRateLimitStatus {
  evictExpiredClients(nowMs, windowMs);
  const active = pruneClientWindow(clientKey, nowMs, windowMs);
  active.push(nowMs);
  failedLoginAttempts.set(clientKey, active);
  if (active.length >= maxAttempts) {
    const retryAfterSeconds = Math.max(1, Math.ceil((active[0] + windowMs - nowMs) / 1000));
    return { limited: true, retryAfterSeconds, remaining: 0 };
  }
  return { limited: false, retryAfterSeconds: 0, remaining: Math.max(0, maxAttempts - active.length) };
}

export function clearLoginFailures(clientKey: string): void {
  failedLoginAttempts.delete(clientKey);
}

export function resetLoginRateLimiter(): void {
  failedLoginAttempts.clear();
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
  const signature = createHmac("sha256", sessionSecret()).update(String(expiresAt)).digest("base64url");
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
  const expected = createHmac("sha256", sessionSecret()).update(String(expiresAt)).digest("base64url");
  const actualBytes = Buffer.from(signature);
  const expectedBytes = Buffer.from(expected);
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes);
}
