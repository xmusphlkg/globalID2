import assert from "node:assert/strict";
import test from "node:test";

import {
  buildConfirmationEmail,
  buildNotificationEmail,
  buildRawEmail,
} from "../src/lib/email.ts";

const subscription = {
  id: "sub-1",
  listCode: "alerts",
  frequency: "instant",
  confirmUrl: "https://example.test/subscriptions/confirm?token=abc&locale=zh",
};

test("renders the established English and Chinese confirmation messages", () => {
  const english = buildConfirmationEmail("en", [subscription]);
  assert.equal(english.subject, "Confirm your GIDS subscription");
  assert.match(english.text, /Priority alerts: https:\/\/example\.test/);
  assert.match(english.html, /Confirm subscription/);
  assert.match(english.html, /token=abc&amp;locale=zh/);

  const chinese = buildConfirmationEmail("zh", [subscription]);
  assert.equal(chinese.subject, "请确认你的 GIDS 订阅");
  assert.match(chinese.text, /重点提醒：https:\/\/example\.test/);
  assert.match(chinese.html, /确认订阅/);
});

test("renders notification markdown and localized unsubscribe copy", () => {
  const email = buildNotificationEmail(
    "zh",
    { subject: "每周疫情更新", markdown: "## 摘要\n\n- 第一项\n- 第二项" },
    "https://example.test/unsubscribe?token=a&locale=zh",
  );

  assert.equal(email.subject, "每周疫情更新");
  assert.match(email.text, /退订: https:\/\/example\.test\/unsubscribe/);
  assert.match(email.html, /<h3[^>]*>摘要<\/h3>/);
  assert.match(email.html, /token=a&amp;locale=zh/);
  assert.match(email.html, />退订<\/a>/);
});

test("builds the established multipart MIME shape", () => {
  const raw = buildRawEmail(
    { fromEmail: "alerts@example.test", fromName: "GIDS Alerts" },
    { to: "reader@example.test", subject: "Weekly update", text: "Plain body", html: "<p>HTML body</p>" },
  );

  assert.match(raw, /^From: "GIDS Alerts" <alerts@example\.test>\r\nTo: <reader@example\.test>\r\nSubject: Weekly update\r\nMIME-Version: 1\.0\r\n/);
  assert.match(raw, /Content-Type: multipart\/alternative; boundary="gids-[^"]+"/);
  assert.match(raw, /Content-Type: text\/plain; charset=UTF-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\nPlain body/);
  assert.match(raw, /Content-Type: text\/html; charset=UTF-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n<p>HTML body<\/p>/);
});

test("does not allow address or subject values to inject MIME headers", () => {
  const raw = buildRawEmail(
    { fromEmail: "alerts@example.test\r\nX-Injected: yes", fromName: "GIDS\r\nBcc: hidden@example.test" },
    {
      to: "reader@example.test\r\nBcc: hidden@example.test",
      subject: "Update\r\nX-Injected: yes",
      text: "Body",
      html: "<p>Body</p>",
    },
  );

  const headerBlock = raw.split("\r\n\r\n", 1)[0];
  assert.doesNotMatch(headerBlock, /\r\n(?:Bcc|X-Injected):/i);
  assert.equal(headerBlock.split("\r\n").length, 5);
});
