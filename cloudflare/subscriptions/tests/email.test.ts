import assert from "node:assert/strict";
import test from "node:test";

import {
  buildConfirmationEmail,
  buildManagementEmail,
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

test("notification variants retain readable linked content and subscriber controls in both formats", () => {
  const unsubscribe = "https://example.test/unsubscribe?token=abc";
  const manage = "https://example.test/manage?token=abc";
  for (const [kind, label] of [["report", "监测报告"], ["research", "研究雷达"], ["alert", "官方通报"]] as const) {
    const email = buildNotificationEmail("zh", {
      subject: "本周动态", markdown: "# 标题\n\n- [原始来源](https://example.test/article?id=1&lang=zh)",
    }, unsubscribe, manage, kind);
    assert.match(email.html, new RegExp(label));
    assert.match(email.html, /<html lang="zh">/);
    assert.match(email.html, /name="viewport"/);
    assert.match(email.html, /role="presentation"/);
    assert.match(email.html, /href="https:\/\/example\.test\/article\?id=1&amp;lang=zh"/);
    assert.match(email.text, /原始来源 \(https:\/\/example\.test\/article\?id=1&lang=zh\)/);
    assert.ok(email.text.includes(unsubscribe));
    assert.ok(email.text.includes(manage));
  }
});

test("management messages localize expiry and keep each escaped list name tied to its link", () => {
  const links = [
    { name: "研究 & <雷达>", url: "https://example.test/manage?token=a&locale=zh" },
    { name: "报告更新", url: "https://example.test/manage?token=b" },
  ];
  const email = buildManagementEmail("zh", links);
  assert.match(email.subject, /管理/);
  assert.match(email.text, /30 分钟/);
  assert.ok(email.text.includes(links[0].url));
  assert.ok(email.text.includes(links[1].url));
  assert.match(email.html, /研究 &amp; &lt;雷达&gt;/);
  assert.doesNotMatch(email.html, /<雷达>/);
  assert.match(email.html, /href="https:\/\/example\.test\/manage\?token=a&amp;locale=zh"/);
  assert.match(email.html, /管理订阅: 研究 &amp; &lt;雷达&gt;/);
  const english = buildManagementEmail("en", links);
  assert.match(english.text, /expire in 30 minutes/);
  assert.match(english.html, /<html lang="en">/);
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

test("adds a sanitized correlation Message-ID when supplied", () => {
  const raw = buildRawEmail(
    { fromEmail: "noreply@example.test", fromName: "GIDS" },
    {
      to: "reader@example.test", subject: "Update", text: "Text", html: "<p>Text</p>",
      messageId: "delivery-1@example.test\r\nBcc:evil@example.test",
    },
  );
  assert.match(raw, /Message-ID: <delivery-1@example.testBccevilexample.test>/);
  assert.equal(raw.includes("\r\nBcc:evil"), false);
});

test("notification MIME provides an HTTPS one-click unsubscribe endpoint without header injection", () => {
  const config = { fromEmail: "noreply@example.test", fromName: "GIDS" };
  const content = buildNotificationEmail("en", { subject: "Update", markdown: "News" }, "https://example.test/api/subscriptions/unsubscribe?token=abc");
  const raw = buildRawEmail(config, { ...content, to: "reader@example.test" });
  assert.match(raw, /\r\nList-Unsubscribe: <https:\/\/example\.test\/api\/subscriptions\/unsubscribe\?token=abc>\r\n/);
  assert.match(raw, /\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n/);
  for (const bad of ["http://example.test/", "https://example.test/\r\nBcc:evil", "https://user@example.test/"]) {
    const invalid = buildRawEmail(config, { ...content, to: "reader@example.test", unsubscribeUrl: bad });
    assert.doesNotMatch(invalid, /\r\nList-Unsubscribe/);
  }
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
