import assert from "node:assert/strict";
import test from "node:test";
import { sign } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { handleSesFeedback, normalizeSesFeedback, reconcileSesFeedback } from "../src/handlers/ses-feedback.ts";
import { canonicalMessage, certificateUrl, parseEnvelope, type SnsEnvelope } from "../src/lib/sns.ts";
import { buildRawEmail } from "../src/lib/email.ts";
import type { Env } from "../src/types.ts";

const topic = "arn:aws:sns:us-east-1:123456789012:ses-events";
const certUrl = "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-0123456789abcdef0123456789abcdef.pem";
const correlation = "e841993e-7b85-499f-a62a-a68aa5da5473";
const providerId = "01000174f87e0fb4-81b97243-56b0-4cd1-90c7-d6987a00fdc1-000000";
const root = mkdtempSync(join(tmpdir(), "gids-sns-test-"));
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=test", "-keyout", join(root, "key.pem"), "-out", join(root, "cert.pem")], { stdio: "ignore" });
const key = readFileSync(join(root, "key.pem"));
const cert = readFileSync(join(root, "cert.pem"), "utf8");
rmSync(root, { recursive: true, force: true });

function fixture() {
  const sqlite = new DatabaseSync(":memory:");
  const migrations = new URL("../migrations/", import.meta.url);
  for (const file of readdirSync(migrations).filter(name => name.endsWith(".sql")).sort()) sqlite.exec(readFileSync(new URL(file, migrations), "utf8"));
  const db = { prepare(query: string) {
    let values: unknown[] = [];
    const statement = { bind(...args: unknown[]) { values = args; return statement; },
      async first() { return sqlite.prepare(query).get(...values as never[]) || null; },
      async all() { return { results: sqlite.prepare(query).all(...values as never[]) }; },
      async run() { return { meta: { changes: Number(sqlite.prepare(query).run(...values as never[]).changes) } }; },
    };
    return statement;
  } };
  const env = { DB: db, SES_SNS_FEEDBACK_ENABLED: "true", SES_SNS_TOPIC_ARN: topic } as unknown as Env;
  const now = new Date().toISOString();
  sqlite.exec(`INSERT INTO subscribers (id,status,created_at,updated_at) VALUES ('user','active','${now}','${now}');
    INSERT INTO subscriber_contacts (id,subscriber_id,channel,address,address_hash,status,created_at,updated_at) VALUES ('contact','user','email','reader@example.invalid','hash','active','${now}','${now}');`);
  function insertDelivery(id: string, provider: string | null = null) {
    sqlite.prepare(`INSERT INTO transactional_email_deliveries (id,subscriber_id,contact_id,delivery_type,recipient,subject,status,provider_message_id,created_at,updated_at)
      VALUES (?,'user','contact','subscription_confirmation','reader@example.invalid','Confirm','queued',?,?,?)`).run(id, provider, now, now);
  }
  return { sqlite, env, insertDelivery };
}

function ses(kind: string, extras: Record<string, unknown> = {}, tags = true) {
  return { eventType: kind, mail: { messageId: providerId, destination: ["reader@example.invalid"],
    ...(tags ? { tags: { "gids-delivery": [correlation] } } : {}) }, ...extras };
}
function envelope(kind: string, message: unknown, extras: Record<string, unknown> = {}): SnsEnvelope {
  const value: SnsEnvelope = { Type: kind as SnsEnvelope["Type"], MessageId: "sns-event-1", TopicArn: topic,
    Message: typeof message === "string" ? message : JSON.stringify(message), Timestamp: new Date().toISOString(),
    SignatureVersion: "2", Signature: "pending", SigningCertURL: certUrl, ...extras };
  value.Signature = sign("RSA-SHA256", Buffer.from(canonicalMessage(value)), key).toString("base64");
  return value;
}
function request(value: unknown) {
  return new Request("https://subscriptions.example.test/api/internal/ses-sns-feedback", {
    method: "POST", headers: { "content-type": "text/plain; charset=UTF-8" }, body: JSON.stringify(value),
  });
}
const certificateFetch: typeof fetch = async (url) => {
  assert.equal(String(url), certUrl);
  return new Response(cert, { status: 200 });
};

test("only exact topic-region AWS certificate URLs are accepted", () => {
  assert.equal(certificateUrl(certUrl, topic).hostname, "sns.us-east-1.amazonaws.com");
  for (const bad of [
    "http://sns.us-east-1.amazonaws.com/SimpleNotificationService-0123456789abcdef0123456789abcdef.pem",
    "https://sns.us-east-1.amazonaws.com.attacker.invalid/SimpleNotificationService-0123456789abcdef0123456789abcdef.pem",
    "https://sns.us-west-2.amazonaws.com/SimpleNotificationService-0123456789abcdef0123456789abcdef.pem",
    "https://sns.us-east-1.amazonaws.com/evil.pem",
    certUrl + "?x=1",
  ]) assert.throws(() => certificateUrl(bad, topic), /invalid_sns_certificate_url/);
});

test("forged signatures, topics, cert redirects and oversized SNS bodies are rejected", async () => {
  const f = fixture();
  const valid = envelope("Notification", ses("Delivery", { delivery: { timestamp: new Date().toISOString() } }));
  assert.equal((await handleSesFeedback(request({ ...valid, Message: valid.Message + " " }), f.env, certificateFetch)).status, 403);
  assert.equal((await handleSesFeedback(request({ ...valid, TopicArn: topic + "-other" }), f.env, certificateFetch)).status, 403);
  assert.equal((await handleSesFeedback(request({ ...valid, SigningCertURL: "https://example.invalid/cert.pem" }), f.env, certificateFetch)).status, 400);
  assert.equal((await handleSesFeedback(request(valid), f.env, async () => new Response(null, { status: 302, headers: { location: "https://example.invalid" } }))).status, 503);
  assert.equal((await handleSesFeedback(new Request("https://example.test", { method: "POST", body: "x".repeat(262145) }), f.env, certificateFetch)).status, 413);
  assert.equal((await handleSesFeedback(request(valid), { ...f.env, SES_SNS_FEEDBACK_ENABLED: "false" }, certificateFetch)).status, 404);
  f.sqlite.close();
});

test("SES event type mapping only suppresses hard bounces and rejects multi-recipient messages", () => {
  const permanent = normalizeSesFeedback(envelope("Notification", ses("Bounce", { bounce: { bounceType: "Permanent" } })));
  const soft = normalizeSesFeedback(envelope("Notification", ses("Bounce", { bounce: { bounceType: "Transient" } })));
  const complaint = normalizeSesFeedback(envelope("Notification", ses("Complaint", { complaint: {} })));
  const delay = normalizeSesFeedback(envelope("Notification", ses("DeliveryDelay", { deliveryDelay: {} })));
  assert.equal(permanent?.eventType, "bounced"); assert.equal(soft?.eventType, "deferred");
  assert.equal(complaint?.eventType, "complained"); assert.equal(delay?.eventType, "deferred");
  assert.throws(() => normalizeSesFeedback(envelope("Notification", { eventType: "Delivery", mail: { messageId: providerId, destination: ["a", "b"] }, delivery: {} })), /invalid_ses_recipient_count/);
});

test("verified early events persist only bounded fields, reconcile after SMTP, and replay idempotently", async () => {
  const f = fixture();
  const value = envelope("Notification", ses("Bounce", { bounce: { bounceType: "Permanent", timestamp: new Date().toISOString() } }));
  assert.equal((await handleSesFeedback(request(value), f.env, certificateFetch)).status, 204);
  const pending = f.sqlite.prepare("SELECT * FROM ses_feedback_pending").get();
  assert.equal(pending?.correlation_id, correlation);
  assert.doesNotMatch(JSON.stringify(pending), /reader@example/);
  assert.equal((await handleSesFeedback(request(value), f.env, certificateFetch)).status, 204);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM ses_feedback_pending").get()?.n, 1);
  f.insertDelivery(correlation);
  await reconcileSesFeedback(f.env);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM ses_feedback_pending").get()?.n, 0);
  assert.equal(f.sqlite.prepare("SELECT status FROM transactional_email_deliveries WHERE id=?").get(correlation)?.status, "failed");
  assert.equal(f.sqlite.prepare("SELECT status FROM subscriber_contacts WHERE id='contact'").get()?.status, "suppressed");
  assert.equal((await handleSesFeedback(request(value), f.env, certificateFetch)).status, 204);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM email_delivery_events").get()?.n, 1);
  f.sqlite.close();
});

test("feedback settles an uncertain confirmation job and fences its old SMTP claim", async () => {
  const f = fixture();
  f.insertDelivery(correlation);
  const now = new Date().toISOString();
  f.sqlite.prepare(`INSERT INTO confirmation_email_outbox
    (id,subscriber_id,contact_id,locale,source,request_ids_json,status,attempts,claim_token,claim_expires_at,created_at,updated_at,transaction_delivery_id)
    VALUES ('job','user','contact','en','website','[]','unknown',1,NULL,NULL,?,?,?)`).run(now, now, correlation);
  const delivered = envelope("Notification", ses("Delivery", { delivery: {} }));
  assert.equal((await handleSesFeedback(request(delivered), f.env, certificateFetch)).status, 204);
  assert.equal(f.sqlite.prepare("SELECT status FROM confirmation_email_outbox WHERE id='job'").get()?.status, "sent");
  f.sqlite.prepare("UPDATE confirmation_email_outbox SET status='retry' WHERE id='job' AND status='sending' AND claim_token='old-claim'").run();
  assert.equal(f.sqlite.prepare("SELECT status FROM confirmation_email_outbox WHERE id='job'").get()?.status, "sent");
  const bounced = envelope("Notification", ses("Bounce", { bounce: { bounceType: "Permanent" } }), { MessageId: "sns-bounce-2" });
  assert.equal((await handleSesFeedback(request(bounced), f.env, certificateFetch)).status, 204);
  assert.equal(f.sqlite.prepare("SELECT status FROM confirmation_email_outbox WHERE id='job'").get()?.status, "failed");
  f.sqlite.close();
});

test("early provider-only event retries when SES message ID becomes available", async () => {
  const f = fixture();
  const value = envelope("Notification", ses("Delivery", { delivery: {} }, false));
  f.insertDelivery(correlation);
  assert.equal((await handleSesFeedback(request(value), f.env, certificateFetch)).status, 204);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM ses_feedback_pending").get()?.n, 1);
  f.sqlite.prepare("UPDATE transactional_email_deliveries SET provider_message_id=? WHERE id=?").run(providerId, correlation);
  await reconcileSesFeedback(f.env);
  assert.equal(f.sqlite.prepare("SELECT status FROM transactional_email_deliveries WHERE id=?").get(correlation)?.status, "delivered");
  f.sqlite.close();
});

test("a signed event cannot pair one delivery's tag with another SES message ID", async () => {
  const f = fixture();
  f.insertDelivery(correlation, "01000174f87e0fb4-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee-000000");
  const value = envelope("Notification", ses("Bounce", { bounce: { bounceType: "Permanent" } }));
  assert.equal((await handleSesFeedback(request(value), f.env, certificateFetch)).status, 403);
  assert.equal(f.sqlite.prepare("SELECT status FROM subscriber_contacts WHERE id='contact'").get()?.status, "active");
  f.sqlite.close();
});

test("signed subscription confirmation can only call the topic's pinned AWS endpoint", async () => {
  const f = fixture();
  const token = "signed-confirmation-token";
  const confirm = new URL("https://sns.us-east-1.amazonaws.com/");
  confirm.searchParams.set("Action", "ConfirmSubscription"); confirm.searchParams.set("TopicArn", topic); confirm.searchParams.set("Token", token);
  const valid = envelope("SubscriptionConfirmation", "Confirm", { Token: token, SubscribeURL: confirm.toString() });
  let calls = 0;
  assert.equal((await handleSesFeedback(request(valid), f.env, async (url) => {
    calls += 1;
    return String(url) === certUrl ? new Response(cert) : new Response("ok");
  })).status, 204);
  assert.equal(calls, 2);
  const invalid = envelope("SubscriptionConfirmation", "Confirm", { Token: token, SubscribeURL: "https://example.invalid/?Action=ConfirmSubscription" });
  assert.equal((await handleSesFeedback(request(invalid), f.env, certificateFetch)).status, 400);
  f.sqlite.close();
});

test("SES headers carry only a validated configuration set and delivery UUID", () => {
  const raw = buildRawEmail({ fromEmail: "a@example.test", fromName: "GIDS", configurationSet: "transactional-v1" },
    { to: "b@example.test", subject: "x", text: "x", html: "x", messageId: `${correlation}@globalinfectiousdisease.com` });
  assert.match(raw, new RegExp(`X-SES-MESSAGE-TAGS: gids-delivery=${correlation}`));
  assert.match(raw, /X-SES-CONFIGURATION-SET: transactional-v1/);
  assert.match(raw, new RegExp(`X-GIDS-Delivery-ID: ${correlation}`));
});
