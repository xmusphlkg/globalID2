import assert from "node:assert/strict";
import test from "node:test";

import {
  HttpError,
  configInt,
  corsHeaders,
  htmlPage,
  json,
  publicBaseUrl,
  requireAdmin,
} from "../src/lib/http.ts";

test("requires an exactly matching bearer token without exposing it", () => {
  const request = new Request("https://worker.test/api/admin/stats");
  assert.throws(
    () => requireAdmin(request, {}),
    (error) => error instanceof HttpError && error.status === 500 && error.message === "ADMIN_API_TOKEN_not_configured",
  );
  assert.throws(
    () => requireAdmin(request, { ADMIN_API_TOKEN: "secret" }),
    (error) => error instanceof HttpError && error.status === 401 && error.message === "unauthorized",
  );
  assert.doesNotThrow(() => requireAdmin(new Request(request, {
    headers: { authorization: "Bearer secret" },
  }), { ADMIN_API_TOKEN: "secret" }));
});

test("preserves CORS and JSON response behavior", async () => {
  const request = new Request("https://worker.test/health", { headers: { origin: "https://allowed.test" } });
  const headers = new Headers(corsHeaders(request, { ALLOWED_ORIGINS: "https://allowed.test, https://other.test" }));
  assert.equal(headers.get("access-control-allow-origin"), "https://allowed.test");
  const fallback = new Headers(corsHeaders(
    new Request(request, { headers: { origin: "https://blocked.test" } }),
    { ALLOWED_ORIGINS: "https://allowed.test,https://other.test" },
  ));
  assert.equal(fallback.get("access-control-allow-origin"), "https://allowed.test");

  const response = json({ error: "not_found" }, request, {}, 404);
  assert.equal(response.status, 404);
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(await response.json(), { error: "not_found" });
});

test("escapes HTML and normalizes public URLs and numeric config", async () => {
  const response = htmlPage("<unsafe>", "message & more");
  const body = await response.text();
  assert.match(body, /&lt;unsafe&gt;/);
  assert.match(body, /message &amp; more/);
  assert.doesNotMatch(body, /<unsafe>/);
  assert.equal(publicBaseUrl(new Request("https://worker.test/path"), {}), "https://worker.test");
  assert.equal(publicBaseUrl(new Request("https://worker.test/path"), { PUBLIC_BASE_URL: "https://public.test///" }), "https://public.test");
  assert.equal(configInt(undefined, 30, 1, 1000), 30);
  assert.equal(configInt("not-a-number", 30, 1, 1000), 30);
  assert.equal(configInt("2.9", 30, 1, 1000), 2);
  assert.equal(configInt("9999", 30, 1, 1000), 1000);
});
