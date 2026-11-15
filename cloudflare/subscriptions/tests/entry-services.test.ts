import assert from "node:assert/strict";
import test from "node:test";
import { createConfirmationDelivery } from "../src/lib/confirmation-delivery.ts";
import { createSignedToken, verifySignedToken } from "../src/lib/token-service.ts";
import { subscriptionFixture } from "./helpers/subscription-fixture.ts";

const config = { host: "smtp.invalid", port: 587, username: "mock", password: "mock", fromEmail: "noreply@example.test", fromName: "Test", useTls: true };
const input = { email: "reader@example.test", locale: "en", subscriberId: "reader", contactId: "contact", source: "test", outboxId: "outbox", outboxClaimToken: "claim-one", subscriptions: [{ id: "subscription", listCode: "reports", frequency: "weekly", confirmUrl: "https://worker.example.test/confirm?token=mock" }] };
const request = () => new Request("https://worker.example.test/api/subscriptions");

test("management tokens expire on generation changes and remain purpose-specific", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  const token = await createSignedToken(env, "manage", "subscription", 3600);
  assert.deepEqual(await verifySignedToken(env, token, "manage"), { subscriptionId: "subscription", version: "generation-one" });
  assert.equal(await verifySignedToken(env, token, "unsubscribe"), null);
  sqlite.exec("UPDATE subscriptions SET management_generation='generation-two'");
  assert.equal(await verifySignedToken(env, token, "manage"), null);
  await assert.rejects(createSignedToken(env, "manage", "missing", 3600), /subscription_not_found/);
});

test("confirmation tokens retain TTL and fail closed without a signing secret", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  const token = await createSignedToken(env, "confirm", "subscription", -1);
  assert.equal(await verifySignedToken(env, token, "confirm"), null);
  await assert.rejects(createSignedToken({ ...env, TOKEN_SIGNING_SECRET: "" }, "confirm", "subscription", 3600), /TOKEN_SIGNING_SECRET_not_configured/);
});

test("confirmation SMTP sees a linked durable ledger and feedback is not downgraded", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  let calls = 0;
  const send = createConfirmationDelivery({ smtpConfig: () => config, async sendSmtpEmail(_config, message, purpose) {
    calls += 1;
    const ledger = sqlite.prepare("SELECT d.id, d.status FROM transactional_email_deliveries d JOIN confirmation_email_outbox o ON o.transaction_delivery_id=d.id WHERE o.id='outbox'").get();
    assert.equal(ledger?.status, "queued");
    assert.equal(message.messageId, `${ledger?.id}@globalinfectiousdisease.com`);
    assert.equal(purpose, "transactional");
    sqlite.prepare("UPDATE transactional_email_deliveries SET status='delivered' WHERE id=?").run(String(ledger?.id));
    return { providerMessageId: "mock-provider-id" };
  } });
  const result = await send(env, request(), input);
  assert.equal(result.status, "sent"); assert.equal(calls, 1);
  assert.equal(sqlite.prepare("SELECT status FROM transactional_email_deliveries").get()?.status, "delivered");
});

test("a lost confirmation outbox claim blocks SMTP", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  const send = createConfirmationDelivery({ smtpConfig: () => config, async sendSmtpEmail() { assert.fail("SMTP must not run after a lost claim"); } });
  const result = await send(env, request(), { ...input, outboxClaimToken: "stale-claim" });
  assert.equal(result.reason, "confirmation_claim_lost");
});

for (const [error, retryable, permanent] of [["smtp_DATA_rejected:451", true, false], ["smtp_RCPT_rejected:550", false, true], ["smtp_timeout", false, false]] as const) {
  test(`confirmation classifies ${error} for conservative outbox recovery`, async (t) => {
    const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
    const send = createConfirmationDelivery({ smtpConfig: () => config, async sendSmtpEmail() { throw new Error(error); } });
    const result = await send(env, request(), input);
    assert.equal(result.status, "failed"); assert.equal(result.retryable, retryable); assert.equal(result.permanent_failure, permanent);
  });
}

test("an accepted confirmation with a failed ledger commit propagates uncertainty", async (t) => {
  const { sqlite, env } = subscriptionFixture(); t.after(() => sqlite.close());
  sqlite.exec("CREATE TRIGGER fail_sent_commit BEFORE UPDATE ON transactional_email_deliveries WHEN NEW.status='sent' BEGIN SELECT RAISE(FAIL, 'ledger unavailable'); END");
  let calls = 0;
  const send = createConfirmationDelivery({ smtpConfig: () => config, async sendSmtpEmail() { calls += 1; return { providerMessageId: "accepted" }; } });
  await assert.rejects(send(env, request(), input), /ledger unavailable/);
  assert.equal(calls, 1);
  assert.equal(sqlite.prepare("SELECT status FROM transactional_email_deliveries").get()?.status, "queued");
});
