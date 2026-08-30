import assert from "node:assert/strict";
import test from "node:test";

import { matchWorkerRoute, normalizePath } from "../src/lib/router.ts";

test("matches every public and admin route with the established methods", () => {
  assert.deepEqual(matchWorkerRoute("GET", "/health/"), { name: "health" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/subscriptions/options"), { name: "subscription_options" });
  assert.deepEqual(matchWorkerRoute("POST", "/api/subscriptions"), { name: "create_subscription" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/subscriptions/confirm"), { name: "confirm_subscription" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/subscriptions/unsubscribe"), { name: "unsubscribe" });
  assert.deepEqual(matchWorkerRoute("POST", "/api/internal/situation-alerts"), { name: "situation_alert_ingest" });
  assert.deepEqual(matchWorkerRoute("POST", "/api/internal/email-delivery-events"), { name: "email_delivery_feedback" });
  assert.deepEqual(matchWorkerRoute("POST", "/api/admin/audience"), { name: "admin_audience" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/admin/stats"), { name: "admin_stats" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/admin/subscriptions"), { name: "admin_subscriptions" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/admin/notifications"), {
    name: "admin_notifications", operation: "list",
  });
  assert.deepEqual(matchWorkerRoute("POST", "/api/admin/notifications"), {
    name: "admin_notifications", operation: "create",
  });
  assert.deepEqual(matchWorkerRoute("GET", "/api/admin/notifications/campaign%201"), {
    name: "admin_notification", operation: "get", campaignId: "campaign 1",
  });
  assert.deepEqual(matchWorkerRoute("POST", "/api/admin/notifications/campaign%201/process"), {
    name: "admin_notification", operation: "process", campaignId: "campaign 1",
  });
  assert.deepEqual(matchWorkerRoute("GET", "/api/admin/situation-alerts"), {
    name: "admin_situation_alerts", operation: "list",
  });
  assert.deepEqual(matchWorkerRoute("POST", "/api/admin/situation-alerts/process"), {
    name: "admin_situation_alerts", operation: "process",
  });
  assert.deepEqual(matchWorkerRoute("POST", "/api/admin/maintenance"), { name: "admin_maintenance" });
});

test("rejects method mismatches and near-match paths", () => {
  assert.deepEqual(matchWorkerRoute("POST", "/health"), { name: "not_found" });
  assert.deepEqual(matchWorkerRoute("DELETE", "/api/subscriptions"), { name: "not_found" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/admin/notifications/id/process"), { name: "not_found" });
  assert.deepEqual(matchWorkerRoute("GET", "/api/admin/notifications/id/extra"), { name: "not_found" });
  assert.equal(normalizePath("/"), "/");
  assert.equal(normalizePath("/health///"), "/health");
});
