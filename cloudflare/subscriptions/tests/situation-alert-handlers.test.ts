import assert from "node:assert/strict";
import test from "node:test";

import {
  createSituationAlertHandlers,
  processSituationAlertOutbox,
  skipPendingSituationAlertsForSubscription,
} from "../src/handlers/situation-alerts.ts";
import { HttpError } from "../src/lib/http.ts";
import type { D1Database } from "../src/lib/db.ts";
import type { Env } from "../src/types.ts";

type Operation = "first" | "all" | "run";

function database(
  respond: (query: string, operation: Operation, binds: unknown[]) => unknown,
): D1Database {
  return {
    prepare(query) {
      let values: unknown[] = [];
      const statement = {
        bind(...binds: unknown[]) { values = binds; return statement; },
        async first<T>() { return respond(query, "first", values) as T | null; },
        async all<T>() { return respond(query, "all", values) as { results: T[] }; },
        async run() { return respond(query, "run", values); },
      };
      return statement;
    },
  };
}

function validAlert() {
  return {
    schema_version: "situation-alert.v1",
    idempotency_key: "report-2026-08-17:signal-1",
    report: {
      report_id: "situation-v3-daily-2026-08-17-r8",
      as_of: "2026-08-17T12:00:00Z",
      quality_gate_status: "passed",
      publication_status: "published",
      public_url: "https://globalinfectiousdisease.com/situation/2026-08-17/",
    },
    signal: {
      signal_id: "signal-1",
      analysis_status: "analyzed",
      anomaly_state: "alert",
      signal_type: "statistical_signal",
      temporal_relevance: "current",
      data_status: "current",
      completeness: 0.99,
      q_value: 0.005,
      model: "robust_quasi_poisson_v1",
      fit_status: "completed",
      detector_tier: "rare_count",
      effect_threshold_passed: true,
      verification_status: "verified",
      verification_basis: "analyst_review",
      verification_policy_version: null,
      automation_decision: null as null | {
        status: string;
        basis: string;
        policy_version: string;
        calibration_hash: string;
        gate_reasons: string[];
        matched_event_ids: string[];
        decided_at: string;
      },
      verified_by: "analyst:17",
      verified_at: "2026-08-17T11:55:00Z",
      observed_at: "2026-08-16T00:00:00Z",
      title: "Influenza signal",
      summary: "A manually reviewed statistical signal.",
      countries: ["US"],
      diseases: ["influenza"],
      evidence_urls: ["https://cdc.gov/example"],
    },
  };
}

function tieredAutoAlert() {
  const payload = validAlert();
  return {
    ...payload,
    signal: {
      ...payload.signal,
      verification_basis: "automated_policy",
      verification_policy_version: "tiered_auto_v3.2",
      verified_by: "policy:tiered_auto_v3.2",
      model: "multi_horizon_gamma_poisson_v1",
      detector_tier: "common_count",
      automation_decision: {
        status: "auto_verified",
        basis: "calibrated_statistical",
        policy_version: "tiered_auto_v3.2",
        calibration_hash: "artifact-hash",
        gate_reasons: [],
        matched_event_ids: [],
        decided_at: "2026-08-17T11:55:00Z",
      },
    },
  };
}

const baseDeps = {
  async createSignedToken() { return "unsubscribe-token"; },
  smtpConfig() {
    return {
      host: "smtp.example.test", port: 587, username: "user", password: "secret",
      fromEmail: "alerts@example.test", fromName: "GIDS", useTls: true,
    };
  },
  async sendSmtpEmail() {},
};

test("ingest authentication fails before parsing or querying D1", async () => {
  let queried = false;
  const env: Env = {
    DB: database(() => { queried = true; return null; }),
    SITUATION_ALERT_INGEST_TOKEN: "ingest-secret",
  };
  const handlers = createSituationAlertHandlers(baseDeps);
  await assert.rejects(
    handlers.ingest(new Request("https://worker.example.test/api/internal/situation-alerts", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(validAlert()),
    }), env),
    (error: unknown) => error instanceof HttpError && error.status === 401,
  );
  assert.equal(queried, false);
});

test("ingest rejects an unknown automatic verification policy before writing to D1", async () => {
  let queried = false;
  const env: Env = {
    DB: database(() => { queried = true; return null; }),
    SITUATION_ALERT_INGEST_TOKEN: "ingest-secret",
  };
  const payload = validAlert();
  payload.signal.verification_basis = "automated_policy";
  payload.signal.verification_policy_version = "permissive_auto_v2";
  payload.signal.verified_by = "policy:permissive_auto_v2";
  const handlers = createSituationAlertHandlers(baseDeps);
  await assert.rejects(
    handlers.ingest(new Request("https://worker.example.test/api/internal/situation-alerts", {
      method: "POST",
      headers: { authorization: "Bearer ingest-secret", "content-type": "application/json" },
      body: JSON.stringify(payload),
    }), env),
    (error: unknown) => error instanceof HttpError && error.status === 422 &&
      error.message === "tiered_auto_policy_required",
  );
  assert.equal(queried, false);
});

test("ingest rejects tiered automatic policy by default before writing to D1", async () => {
  let queried = false;
  const env: Env = {
    DB: database(() => { queried = true; return null; }),
    SITUATION_ALERT_INGEST_TOKEN: "ingest-secret",
  };
  const handlers = createSituationAlertHandlers(baseDeps);
  await assert.rejects(
    handlers.ingest(new Request("https://worker.example.test/api/internal/situation-alerts", {
      method: "POST",
      headers: { authorization: "Bearer ingest-secret", "content-type": "application/json" },
      body: JSON.stringify(tieredAutoAlert()),
    }), env),
    (error: unknown) => error instanceof HttpError && error.status === 422
      && error.message === "automated_policy_dispatch_disabled",
  );
  assert.equal(queried, false);
});

test("an explicit Worker flag lets calibrated automatic policy reach persistence", async () => {
  let queried = false;
  const env: Env = {
    DB: database(() => { queried = true; return null; }),
    SITUATION_ALERT_INGEST_TOKEN: "ingest-secret",
    SITUATION_ALERT_AUTOMATED_POLICY_ENABLED: "true",
  };
  const handlers = createSituationAlertHandlers(baseDeps);
  await assert.rejects(
    handlers.ingest(new Request("https://worker.example.test/api/internal/situation-alerts", {
      method: "POST",
      headers: { authorization: "Bearer ingest-secret", "content-type": "application/json" },
      body: JSON.stringify(tieredAutoAlert()),
    }), env),
    (error: unknown) => error instanceof HttpError && error.status === 500
      && error.message === "situation_alert_not_persisted",
  );
  assert.equal(queried, true);
});

test("ingest persists once, fans out with filter SQL, and falls back to the D1 outbox", async () => {
  let insertedEventId = "";
  const queries: Array<{ query: string; binds: unknown[] }> = [];
  const payload = validAlert();
  const env: Env = {
    SITUATION_ALERT_INGEST_TOKEN: "ingest-secret",
    SITUATION_PUBLIC_ORIGINS: "https://globalinfectiousdisease.com",
    DB: database((query, operation, binds) => {
      queries.push({ query, binds });
      if (operation === "run" && query.includes("INSERT INTO situation_alert_events")) {
        insertedEventId = String(binds[0]);
        return {};
      }
      if (operation === "first" && query.includes("WHERE idempotency_key")) {
        return {
          id: insertedEventId,
          idempotency_key: payload.idempotency_key,
          report_id: payload.report.report_id,
          signal_id: payload.signal.signal_id,
          payload_sha256: binds.length ? "" : "",
          payload_json: "{}",
          status: "received",
          received_at: "now",
          updated_at: "now",
          queued_count: 0,
          sent_count: 0,
          skipped_count: 0,
          failed_count: 0,
        };
      }
      if (operation === "first" && query.includes("COUNT(*) AS count FROM situation_alert_deliveries")) {
        return { count: 2 };
      }
      return operation === "all" ? { results: [] } : null;
    }),
  };

  // The payload digest is computed inside the handler. Mirror it in the selected row after the insert.
  const originalPrepare = env.DB.prepare.bind(env.DB);
  let digest = "";
  env.DB.prepare = (query: string) => {
    const statement = originalPrepare(query);
    if (!query.includes("INSERT INTO situation_alert_events")) return statement;
    const originalBind = statement.bind.bind(statement);
    statement.bind = (...values: unknown[]) => {
      digest = String(values[6]);
      return originalBind(...values);
    };
    return statement;
  };
  const selectedPrepare = env.DB.prepare.bind(env.DB);
  env.DB.prepare = (query: string) => {
    const statement = selectedPrepare(query);
    if (!query.includes("WHERE idempotency_key")) return statement;
    statement.first = async <T>() => ({
      id: insertedEventId,
      idempotency_key: payload.idempotency_key,
      report_id: payload.report.report_id,
      signal_id: payload.signal.signal_id,
      verification_basis: payload.signal.verification_basis,
      verification_policy_version: payload.signal.verification_policy_version,
      payload_sha256: digest,
      payload_json: JSON.stringify(payload),
      status: "received",
      received_at: "now",
      updated_at: "now",
      queued_count: 0,
      sent_count: 0,
      skipped_count: 0,
      failed_count: 0,
    }) as T;
    return statement;
  };

  const handlers = createSituationAlertHandlers(baseDeps);
  const response = await handlers.ingest(new Request("https://worker.example.test/api/internal/situation-alerts", {
    method: "POST",
    headers: { authorization: "Bearer ingest-secret", "content-type": "application/json" },
    body: JSON.stringify(payload),
  }), env);
  assert.equal(response.status, 202);
  const body = await response.json() as { duplicate: boolean; queued_deliveries: number; dispatch: string };
  assert.equal(body.duplicate, false);
  assert.equal(body.queued_deliveries, 2);
  assert.equal(body.dispatch, "d1_outbox");
  const fanout = queries.find(({ query }) => query.includes("INSERT INTO situation_alert_deliveries"));
  assert.ok(fanout);
  assert.match(fanout.query, /s\.frequency = 'instant'/);
  assert.match(fanout.query, /ON CONFLICT\(event_id, subscription_id\) DO NOTHING/);
  assert.ok(fanout.binds.includes("US"));
  assert.ok(fanout.binds.includes("influenza"));
  const eventInsert = queries.find(({ query }) => query.includes("INSERT INTO situation_alert_events"));
  assert.equal(eventInsert?.binds[4], "analyst_review");
  assert.equal(eventInsert?.binds[5], null);
});

test("an idempotency key cannot be reused for different alert content", async () => {
  let fannedOut = false;
  const env: Env = {
    SITUATION_ALERT_INGEST_TOKEN: "ingest-secret",
    DB: database((query, operation) => {
      if (query.includes("INSERT INTO situation_alert_deliveries")) fannedOut = true;
      if (operation === "first" && query.includes("WHERE idempotency_key")) {
        return {
          id: "existing-event",
          idempotency_key: validAlert().idempotency_key,
          report_id: validAlert().report.report_id,
          signal_id: validAlert().signal.signal_id,
          payload_sha256: "digest-for-different-content",
          payload_json: "{}",
          status: "queued",
          received_at: "now",
          updated_at: "now",
          queued_count: 1,
          sent_count: 0,
          skipped_count: 0,
          failed_count: 0,
        };
      }
      return operation === "all" ? { results: [] } : null;
    }),
  };
  const handlers = createSituationAlertHandlers(baseDeps);
  await assert.rejects(
    handlers.ingest(new Request("https://worker.example.test/api/internal/situation-alerts", {
      method: "POST",
      headers: { authorization: "Bearer ingest-secret", "content-type": "application/json" },
      body: JSON.stringify(validAlert()),
    }), env),
    (error: unknown) => error instanceof HttpError && error.status === 409 &&
      error.message === "idempotency_key_payload_conflict",
  );
  assert.equal(fannedOut, false);
});

test("outbox failure schedules an exponential retry and never logs an email address", async () => {
  const updates: unknown[][] = [];
  let transactionalMetadata = "";
  const env: Env = {
    PUBLIC_BASE_URL: "https://worker.example.test",
    DB: database((query, operation, binds) => {
      if (operation === "all" && query.includes("SELECT id FROM situation_alert_deliveries")) {
        return { results: [{ id: "delivery-1" }] };
      }
      if (operation === "first" && query.includes("d.claim_token")) {
        return {
          id: "delivery-1",
          event_id: "event-1",
          subscription_id: "subscription-1",
          contact_id: "contact-1",
          attempts: 1,
          payload_json: JSON.stringify(validAlert()),
          subscriber_id: "subscriber-1",
          subscriber_status: "active",
          subscription_status: "active",
          contact_status: "active",
          email: "private@example.test",
          locale: "en",
        };
      }
      if (operation === "all" && query.includes("GROUP BY status")) {
        return { results: [{ status: "retry", count: 1 }] };
      }
      if (operation === "run" && query.includes("SET status = ?, next_attempt_at")) updates.push(binds);
      if (operation === "run" && query.includes("INSERT INTO transactional_email_deliveries")) {
        transactionalMetadata = String(binds[13]);
      }
      return operation === "all" ? { results: [] } : operation === "first" ? null : {};
    }),
  };
  const result = await processSituationAlertOutbox(env, {
    ...baseDeps,
    async sendSmtpEmail() { throw new Error("temporary smtp failure"); },
  });
  assert.equal(result.processed, 1);
  assert.equal(result.retried, 1);
  assert.equal(result.dead_letter, 0);
  assert.equal(updates[0]?.[0], "retry");
  assert.equal(updates[0]?.[2], "temporary smtp failure");
  assert.equal(updates.flat().includes("private@example.test"), false);
  assert.deepEqual(JSON.parse(transactionalMetadata), {
    event_id: "event-1",
    outbox_delivery_id: "delivery-1",
    report_id: validAlert().report.report_id,
    signal_id: validAlert().signal.signal_id,
    verification_basis: "analyst_review",
    verification_policy_version: null,
  });
});

test("unsubscribe cancellation only changes queued or retrying Situation deliveries", async () => {
  let updateQuery = "";
  let updateBinds: unknown[] = [];
  const env: Env = {
    DB: database((query, operation, binds) => {
      if (operation === "run") { updateQuery = query; updateBinds = binds; }
      return {};
    }),
  };
  await skipPendingSituationAlertsForSubscription(env, "subscription-1");
  assert.match(updateQuery, /status IN \('queued', 'retry'\)/);
  assert.equal(updateBinds.at(-1), "subscription-1");
});
