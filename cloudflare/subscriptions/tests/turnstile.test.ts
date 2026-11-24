import assert from "node:assert/strict";
import test from "node:test";
import { publicTurnstile, verifyTurnstileIfConfigured } from "../src/lib/turnstile.ts";
import { HttpError } from "../src/lib/http.ts";
import type { Env } from "../src/types.ts";

const env = {
  ENVIRONMENT: "production", TURNSTILE_SITE_KEY: "public-sitekey", TURNSTILE_SECRET_KEY: "server-secret",
  TURNSTILE_HOSTNAMES: "globalinfectiousdisease.com,subscriptions.globalinfectiousdisease.com",
} as Env;
const request = new Request("https://subscriptions.globalinfectiousdisease.com/api/subscriptions", {
  headers: { "CF-Connecting-IP": "192.0.2.1" },
});
const verify = (payload: Record<string, unknown>, result: unknown, overrides: Partial<Env> = {}) =>
  verifyTurnstileIfConfigured(payload, request, { ...env, ...overrides }, "subscribe",
    async () => Response.json(result));

test("Turnstile publishes only the public sitekey and supports an explicitly unconfigured deployment", async () => {
  assert.deepEqual(publicTurnstile(env), { enabled: true, sitekey: "public-sitekey" });
  assert.deepEqual(publicTurnstile({} as Env), { enabled: false });
  assert.deepEqual(publicTurnstile({ ...env, TURNSTILE_ENABLED: "false" }), { enabled: false });
  await verifyTurnstileIfConfigured({}, request, { ...env, TURNSTILE_ENABLED: "false" }, "subscribe",
    async () => { throw new Error("must not fetch while staged"); });
  await verifyTurnstileIfConfigured({}, request, {} as Env, "subscribe", async () => { throw new Error("must not fetch"); });
});

test("invalid tokens and incomplete production configuration fail before contacting Siteverify", async () => {
  for (const token of [undefined, "", "   ", 123, ["token"], "a".repeat(2049)]) {
    await assert.rejects(verifyTurnstileIfConfigured({ turnstileToken: token }, request, env, "subscribe",
      async () => { assert.fail("must not call Siteverify"); }), /turnstile_token_required/);
  }
  for (const overrides of [
    { TURNSTILE_SECRET_KEY: "" }, { TURNSTILE_SITE_KEY: "" }, { TURNSTILE_HOSTNAMES: "" },
    { TURNSTILE_HOSTNAMES: "localhost" }, { TURNSTILE_HOSTNAMES: "127.0.0.1,globalinfectiousdisease.com" },
  ]) {
    await assert.rejects(verify({ turnstileToken: "token" }, {}, overrides), /turnstile_not_configured/);
  }
});

test("verification requires an exact successful result, matching action and approved frontend hostname", async () => {
  for (const result of [
    null, {}, { success: "true" },
    { success: false, "error-codes": ["timeout-or-duplicate"] },
    { success: true, action: "subscribe", hostname: "attacker.test" },
    { success: true, action: "subscribe", hostname: "localhost" },
    { success: true, action: "manage_link", hostname: "globalinfectiousdisease.com" },
    { success: true, hostname: "globalinfectiousdisease.com" },
  ]) await assert.rejects(verify({ turnstileToken: "token" }, result), /turnstile_verification_failed/);
  await verify({ turnstileToken: "token" }, { success: true, action: "subscribe", hostname: "globalinfectiousdisease.com" });
});

test("Siteverify uses a bounded server-side request and the edge-provided client IP", async () => {
  await verifyTurnstileIfConfigured({ "cf-turnstile-response": "fresh-token" }, request, env, "manage_link", async (url, init) => {
    assert.equal(url, "https://challenges.cloudflare.com/turnstile/v0/siteverify");
    assert.equal(init?.method, "POST");
    assert.ok(init?.signal instanceof AbortSignal);
    const form = new URLSearchParams(String(init?.body));
    assert.equal(form.get("secret"), "server-secret");
    assert.equal(form.get("response"), "fresh-token");
    assert.equal(form.get("remoteip"), "192.0.2.1");
    return Response.json({ success: true, action: "manage_link", hostname: "subscriptions.globalinfectiousdisease.com" });
  });
});

test("network errors, upstream failures and non-JSON results fail closed with a safe retryable error", async () => {
  for (const fetcher of [
    async () => { throw new Error("private upstream detail"); },
    async () => new Response("unavailable", { status: 500 }),
    async () => new Response("<html>challenge</html>"),
  ]) await assert.rejects(verifyTurnstileIfConfigured({ turnstileToken: "token" }, request, env, "subscribe", fetcher),
    (error: unknown) => error instanceof HttpError && error.status === 503 && error.message === "turnstile_unavailable");
});
