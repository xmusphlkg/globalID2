import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cookieSetMock = vi.fn();
const cookieGetMock = vi.fn();

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    set: cookieSetMock,
    get: cookieGetMock,
  })),
}));

import { DELETE, POST } from "./route";
import {
  PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
  PANEL_SESSION_COOKIE,
  resetLoginRateLimiter,
} from "@/lib/panel-session";

const ORIGINAL_PASSWORD = process.env.DASHBOARD_PANEL_PASSWORD;
const ORIGINAL_SECRET = process.env.DASHBOARD_SESSION_SECRET;

beforeEach(() => {
  cookieSetMock.mockReset();
  cookieGetMock.mockReset();
  resetLoginRateLimiter();
  process.env.DASHBOARD_PANEL_PASSWORD = "top-secret-password";
  delete process.env.DASHBOARD_SESSION_SECRET;
});

afterEach(() => {
  if (ORIGINAL_PASSWORD === undefined) delete process.env.DASHBOARD_PANEL_PASSWORD;
  else process.env.DASHBOARD_PANEL_PASSWORD = ORIGINAL_PASSWORD;

  if (ORIGINAL_SECRET === undefined) delete process.env.DASHBOARD_SESSION_SECRET;
  else process.env.DASHBOARD_SESSION_SECRET = ORIGINAL_SECRET;

  resetLoginRateLimiter();
});

function loginRequest(
  password: string,
  {
    url = "http://localhost:3000/api/panel-session",
    headers = {},
  }: { url?: string; headers?: Record<string, string> } = {},
): Request {
  return new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify({ password }),
  });
}

describe("/api/panel-session route security", () => {
  it("sets secure cookie flag on HTTPS requests and leaves it false on HTTP localhost", async () => {
    const httpRes = await POST(loginRequest("top-secret-password"));
    expect(httpRes.status).toBe(200);
    expect(cookieSetMock).toHaveBeenLastCalledWith(
      PANEL_SESSION_COOKIE,
      expect.any(String),
      expect.objectContaining({
        httpOnly: true,
        sameSite: "lax",
        secure: false,
        path: "/",
      }),
    );

    const proxyHttpsRes = await POST(
      loginRequest("top-secret-password", {
        url: "http://127.0.0.1:3000/api/panel-session",
        headers: { "x-forwarded-proto": "https" },
      }),
    );
    expect(proxyHttpsRes.status).toBe(200);
    expect(cookieSetMock).toHaveBeenLastCalledWith(
      PANEL_SESSION_COOKIE,
      expect.any(String),
      expect.objectContaining({
        httpOnly: true,
        sameSite: "lax",
        secure: true,
        path: "/",
      }),
    );

    const directHttpsRes = await POST(
      loginRequest("top-secret-password", {
        url: "https://dashboard.example.com/api/panel-session",
      }),
    );
    expect(directHttpsRes.status).toBe(200);
    expect(cookieSetMock).toHaveBeenLastCalledWith(
      PANEL_SESSION_COOKIE,
      expect.any(String),
      expect.objectContaining({ secure: true }),
    );
  });

  it("rate limits repeated failed password attempts with 429 and Retry-After, clearing on valid login", async () => {
    const ipHeader = { "x-forwarded-for": "198.51.100.42" };

    // First 9 failures still allow a valid login and clear the counter
    for (let i = 0; i < PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS - 1; i++) {
      const res = await POST(loginRequest("wrong", { headers: ipHeader }));
      expect(res.status).toBe(401);
    }

    const okRes = await POST(loginRequest("top-secret-password", { headers: ipHeader }));
    expect(okRes.status).toBe(200);

    // Now exhaust all 10 attempts to trigger HTTP 429
    for (let i = 0; i < PANEL_LOGIN_RATE_LIMIT_MAX_ATTEMPTS; i++) {
      const res = await POST(loginRequest("wrong", { headers: ipHeader }));
      expect(res.status).toBe(401);
    }

    const blocked = await POST(loginRequest("top-secret-password", { headers: ipHeader }));
    expect(blocked.status).toBe(429);
    const retryAfter = Number(blocked.headers.get("Retry-After"));
    expect(Number.isFinite(retryAfter) && retryAfter > 0).toBe(true);

    // Another client IP can still authenticate
    const otherClient = await POST(
      loginRequest("top-secret-password", { headers: { "x-forwarded-for": "198.51.100.99" } }),
    );
    expect(otherClient.status).toBe(200);
  });

  it("propagates secure flag when clearing cookie on DELETE", async () => {
    const res = await DELETE(
      new Request("http://127.0.0.1:3000/api/panel-session", {
        method: "DELETE",
        headers: { "x-forwarded-proto": "https" },
      }),
    );
    expect(res.status).toBe(200);
    expect(cookieSetMock).toHaveBeenLastCalledWith(
      PANEL_SESSION_COOKIE,
      "",
      expect.objectContaining({
        httpOnly: true,
        sameSite: "lax",
        secure: true,
        maxAge: 0,
      }),
    );
  });
});
