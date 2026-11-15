import assert from "node:assert/strict";
import test from "node:test";
import { consumeSituationAlertBatch } from "../src/handlers/situation-alerts.ts";
import { situationAlertJob, type SituationAlertJob } from "../src/lib/situation-alert.ts";
import { subscriptionFixture } from "./helpers/subscription-fixture.ts";

function batch(body: unknown, attempts = 1) {
  const state = { acknowledged: 0, delays: [] as number[] };
  const value = { queue: "local-test", messages: [{ id: "message-one", body, attempts, timestamp: new Date(), ack() { state.acknowledged += 1; }, retry(options: { delaySeconds: number }) { state.delays.push(options.delaySeconds); } }] } as MessageBatch<SituationAlertJob>;
  return { value, state };
}
const config = { host: "smtp.invalid", port: 587, username: "mock", password: "mock", fromEmail: "noreply@example.test", fromName: "Test", useTls: true };
const deps = { async createSignedToken() { return "mock-token"; }, smtpConfig: () => config, async sendSmtpEmail() { assert.fail("No recipient is present in this fixture"); } };

test("invalid queue jobs are acknowledged without accessing SMTP or D1", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  const { value, state } = batch({ schema_version: "invalid" });
  await consumeSituationAlertBatch(value, env, { ...deps, smtpConfig() { assert.fail("Invalid job must not enter delivery"); } });
  assert.equal(state.acknowledged, 1); assert.deepEqual(state.delays, []);
});

test("missing SMTP configuration retries the queue job after 300 seconds", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  const { value, state } = batch(situationAlertJob("event-one"));
  await consumeSituationAlertBatch(value, env, { ...deps, smtpConfig: () => null });
  assert.equal(state.acknowledged, 0); assert.deepEqual(state.delays, [300]);
});

test("finished jobs are acknowledged and storage failures retry with the attempt delay", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  const completed = batch(situationAlertJob("event-one"));
  await consumeSituationAlertBatch(completed.value, env, deps);
  assert.equal(completed.state.acknowledged, 1);
  const failure = batch(situationAlertJob("event-one"), 3);
  sqlite.exec("DROP TABLE situation_alert_deliveries");
  await consumeSituationAlertBatch(failure.value, env, deps);
  assert.equal(failure.state.acknowledged, 0); assert.deepEqual(failure.state.delays, [1800]);
});
