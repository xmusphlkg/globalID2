import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";

import {
  PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
  PANEL_LOGIN_RATE_LIMIT_WINDOW_MS,
  checkLoginRateLimit,
  clearLoginFailures,
  isHttpsRequest,
  issueSession,
  passwordMatches,
  recordFailedLoginAttempt,
  resetLoginRateLimiter,
  sessionValid,
} from "./panel-session";

const ORIGINAL_PASSWORD = process.env.DASHBOARD_PANEL_PASSWORD;
const ORIGINAL_SECRET = process.env.DASHBOARD_SESSION_SECRET;

afterEach(() => {
  if (ORIGINAL_PASSWORD === undefined) delete process.env.DASHBOARD_PANEL_PASSWORD;
  else process.env.DASHBOARD_PANEL_PASSWORD = ORIGINAL_PASSWORD;

  if (ORIGINAL_SECRET === undefined) delete process.env.DASHBOARD_SESSION_SECRET;
  else process.env.DASHBOARD_SESSION_SECRET = ORIGINAL_SECRET;

  resetLoginRateLimiter();
});

describe("panel session", () => {
  it("accepts the configured password and a fresh session only", () => {
    process.env.DASHBOARD_PANEL_PASSWORD = "correct horse";
    delete process.env.DASHBOARD_SESSION_SECRET;
    expect(passwordMatches("correct horse")).toBe(true);
    expect(passwordMatches("wrong")).toBe(false);
    const token = issueSession(1_700_000_000_000);
    expect(sessionValid(token, 1_700_000_000_000)).toBe(true);
    expect(sessionValid(token, 1_700_000_000_000 + 8 * 24 * 60 * 60 * 1000)).toBe(false);
    expect(sessionValid("1.not-a-signature", 1_700_000_000_000)).toBe(false);
  });

  it("stays open when no password is configured", () => {
    delete process.env.DASHBOARD_PANEL_PASSWORD;
    delete process.env.DASHBOARD_SESSION_SECRET;
    expect(sessionValid(undefined)).toBe(true);
  });

  it("uses domain-separated derived secret by default and supports DASHBOARD_SESSION_SECRET", () => {
    process.env.DASHBOARD_PANEL_PASSWORD = "panel-password";
    delete process.env.DASHBOARD_SESSION_SECRET;

    const nowMs = 1_700_000_000_000;
    const derivedToken = issueSession(nowMs);
    const [expiresAt] = derivedToken.split(".");
    const rawPasswordSig = createHmac("sha256", "panel-password").update(expiresAt).digest("base64url");
    expect(derivedToken).not.toBe(`${expiresAt}.${rawPasswordSig}`);
    expect(sessionValid(derivedToken, nowMs)).toBe(true);

    // Explicit separate signing secret supersedes the derived key
    process.env.DASHBOARD_SESSION_SECRET = "separate-signing-secret-v1";
    expect(sessionValid(derivedToken, nowMs)).toBe(false);
    const explicitToken = issueSession(nowMs);
    expect(sessionValid(explicitToken, nowMs)).toBe(true);

    // Rotating the signing secret invalidates previously issued tokens
    process.env.DASHBOARD_SESSION_SECRET = "separate-signing-secret-v2";
    expect(sessionValid(explicitToken, nowMs)).toBe(false);
  });

  it("detects HTTPS requests via x-forwarded-proto or request URL", () => {
    expect(isHttpsRequest(new Request("http://localhost:3000/api/panel-session"))).toBe(false);
    expect(isHttpsRequest(new Request("https://dash.example.com/api/panel-session"))).toBe(true);
    expect(
      isHttpsRequest(
        new Request("http://127.0.0.1:3000/api/panel-session", {
          headers: { "x-forwarded-proto": "https" },
        }),
      ),
    ).toBe(true);
    expect(
      isHttpsRequest(
        new Request("http://127.0.0.1:3000/api/panel-session", {
          headers: { "x-forwarded-proto": "https, http" },
        }),
      ),
    ).toBe(true);
  });

  it("enforces sliding-window rate limiting on failed login attempts", () => {
    const startMs = 1_700_000_000_000;
    const client = "203.0.113.10";

    for (let i = 0; i < PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS - 1; i++) {
      const status = recordFailedLoginAttempt(client, startMs + i * 1000);
      expect(status.limited).toBe(false);
    }

    const hit = recordFailedLoginAttempt(client, startMs + 10_000);
    expect(hit.limited).toBe(true);
    expect(hit.retryAfterSeconds).toBeGreaterThan(0);

    const check = checkLoginRateLimit(client, startMs + 15_000);
    expect(check.limited).toBe(true);
    expect(check.retryAfterSeconds).toBeGreaterThan(0);

    // Other clients remain unaffected
    expect(checkLoginRateLimit("203.0.113.99", startMs + 15_000).limited).toBe(false);

    // Sliding window expires after 5 minutes
    const afterWindow = checkLoginRateLimit(client, startMs + PANEL_LOGIN_RATE_LIMIT_WINDOW_MS + 1000);
    expect(afterWindow.limited).toBe(false);

    // Explicit clear removes failures immediately
    recordFailedLoginAttempt(client, startMs + PANEL_LOGIN_RATE_LIMIT_WINDOW_MS + 2000);
    clearLoginFailures(client);
    expect(checkLoginRateLimit(client, startMs + PANEL_LOGIN_RATE_LIMIT_WINDOW_MS + 2000).remaining).toBe(
      PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
    );
  });
});
