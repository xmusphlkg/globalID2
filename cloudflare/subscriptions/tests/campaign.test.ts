import assert from "node:assert/strict";
import test from "node:test";

import {
  campaignContentFingerprint,
  campaignProgressFromRows,
  campaignStatusFromProgress,
  localizedNotificationContent,
  normalizeCampaignListCodes,
  normalizeCampaignIdempotencyKey,
  normalizeNotificationContents,
  normalizeTargetLocales,
  notificationCampaignDeliveryProjection,
  notificationCampaignMetadataProjection,
  notificationCampaignSummaryProjection,
  parseNotificationMetadata,
} from "../src/lib/campaign.ts";

test("validates campaign idempotency keys and fingerprints canonical content", async () => {
  assert.equal(normalizeCampaignIdempotencyKey(" research-digest:2026-W33:r1 "), "research-digest:2026-W33:r1");
  assert.equal(normalizeCampaignIdempotencyKey("bad key"), "");
  assert.equal(normalizeCampaignIdempotencyKey(""), "");
  assert.equal(
    await campaignContentFingerprint({ b: [2, { y: true, x: "value" }], a: 1 }),
    await campaignContentFingerprint({ a: 1, b: [2, { x: "value", y: true }] }),
  );
  assert.notEqual(
    await campaignContentFingerprint({ subject: "First" }),
    await campaignContentFingerprint({ subject: "Changed" }),
  );
});

test("normalizes campaign contents without changing top-level override rules", () => {
  assert.deepEqual(normalizeNotificationContents({
    contents: {
      "ZH-cn": { subject: " 中文\r\n标题 ", body: " 正文 " },
      "pt-BR": { subject: "Português", markdown: " Atualização " },
      ru: { subject: "ignored", markdown: "unsupported locale" },
      en: { subject: "ignored", markdown: "" },
    },
    source_locale: "zh",
    subject: " Top-level title ",
    markdown: " Top-level body ",
  }), {
    zh: { subject: "Top-level title", markdown: "Top-level body" },
    pt: { subject: "Português", markdown: "Atualização" },
  });

  assert.deepEqual(normalizeNotificationContents({
    default_locale: "ja-JP",
    markdown: "# Derived subject\n\nBody",
  }), {
    ja: { subject: "Derived subject", markdown: "# Derived subject\n\nBody" },
  });
});

test("normalizes list codes and target locales with stable order and deduplication", () => {
  assert.deepEqual(
    normalizeCampaignListCodes({ list_codes: [" Reports ", "alerts,Reports", "bad / code"] }),
    ["reports", "alerts", "badcode"],
  );
  const contents = normalizeNotificationContents({
    contents: {
      en: { subject: "English", markdown: "Body" },
      zh: { subject: "中文", markdown: "正文" },
    },
  });
  assert.deepEqual(normalizeTargetLocales({}, contents), ["en", "zh"]);
  assert.deepEqual(
    normalizeTargetLocales({ locales: ["ZH-cn", "en,zh", "ru"] }, contents),
    ["zh", "en"],
  );
});

test("parses and sanitizes notification metadata while preserving extension fields", () => {
  const metadata = parseNotificationMetadata(JSON.stringify({
    source_locale: "pt-BR",
    default_locale: "ZH_cn",
    target_locales: ["pt-BR", "ru", "pt"],
    list_codes: [" Reports ", "bad / code"],
    audience_count: 12,
    extra: { retained: true },
    contents: {
      "pt-BR": { subject: " Olá\r\nBcc: no ", markdown: " Corpo " },
      ru: { subject: "ignored", markdown: "ignored" },
      en: { subject: "missing body" },
    },
  }));

  assert.equal(metadata.source_locale, "pt");
  assert.equal(metadata.default_locale, "zh");
  assert.deepEqual(metadata.target_locales, ["pt", "pt"]);
  assert.deepEqual(metadata.list_codes, ["reports", "badcode"]);
  assert.deepEqual(metadata.contents, {
    pt: { subject: "Olá Bcc: no", markdown: "Corpo" },
  });
  assert.equal(metadata.audience_count, 12);
  assert.deepEqual(metadata.extra, { retained: true });
  assert.deepEqual(parseNotificationMetadata("not-json").contents, {});
});

test("selects localized content using the established fallback order", () => {
  const metadata = parseNotificationMetadata(JSON.stringify({
    default_locale: "ja",
    contents: {
      en: { subject: "English", markdown: "English body" },
      zh: { subject: "中文", markdown: "中文正文" },
      ja: { subject: "日本語", markdown: "日本語本文" },
    },
  }));
  assert.equal(localizedNotificationContent(metadata, "zh-CN").subject, "中文");
  assert.equal(localizedNotificationContent(metadata, "fr").subject, "日本語");
  assert.deepEqual(localizedNotificationContent({}, "de"), {
    subject: "GIDS Update",
    markdown: "GIDS update.",
  });
});

test("calculates progress and campaign status for every terminal branch", () => {
  const queued = campaignProgressFromRows([
    { status: "queued", count: 3 },
    { status: "sent", count: 2 },
    { status: "failed", count: 1 },
    { status: "skipped", count: 1 },
    { status: "custom", count: 1 },
  ]);
  assert.deepEqual(queued, {
    total: 8,
    queued: 3,
    sent: 2,
    delivered: 0,
    deferred: 0,
    failed: 1,
    skipped: 1,
    completed: 4,
    percent: 50,
  });
  assert.equal(campaignStatusFromProgress(queued), "sending");
  assert.equal(campaignStatusFromProgress(campaignProgressFromRows([{ status: "queued", count: 2 }])), "queued");
  assert.equal(campaignStatusFromProgress(campaignProgressFromRows([{ status: "deferred", count: 2 }])), "sending");
  assert.equal(campaignStatusFromProgress(campaignProgressFromRows([{ status: "sent", count: 2 }, { status: "failed", count: 1 }])), "partial_failed");
  assert.equal(campaignStatusFromProgress(campaignProgressFromRows([{ status: "failed", count: 1 }])), "failed");
  assert.equal(campaignStatusFromProgress(campaignProgressFromRows([{ status: "sent", count: 2 }])), "sent");
  assert.equal(campaignStatusFromProgress(campaignProgressFromRows([])), "sent");
  assert.equal(campaignProgressFromRows([]).percent, 100);
});

test("projects the established campaign summary defaults", () => {
  const progress = campaignProgressFromRows([{ status: "queued", count: 4 }]);
  const summary = notificationCampaignSummaryProjection({
    id: "campaign-1",
    subject: "Update",
    status: "queued",
    created_at: "2026-08-05T00:00:00.000Z",
    scheduled_at: "",
    sent_at: undefined,
    metadata_json: JSON.stringify({
      source_locale: "zh-CN",
      audience_count: 0,
      contents: { zh: { subject: "更新", markdown: "正文" } },
    }),
  }, progress);

  assert.deepEqual(summary, {
    id: "campaign-1",
    subject: "Update",
    status: "queued",
    created_at: "2026-08-05T00:00:00.000Z",
    scheduled_at: null,
    sent_at: null,
    source_locale: "zh",
    default_locale: "en",
    target_locales: [],
    list_codes: [],
    audience_count: 4,
    progress,
  });
});

test("projects detail metadata and delivery fields with established defaults", () => {
  const metadata = parseNotificationMetadata(JSON.stringify({
    default_locale: "zh-CN",
    template_version: "",
    created_by: "",
    ai: false,
    contents: { zh: { subject: "更新", markdown: "正文" } },
  }));
  assert.deepEqual(notificationCampaignMetadataProjection(metadata), {
    source_locale: null,
    default_locale: "zh",
    target_locales: [],
    list_codes: [],
    template_version: "admin-notification-v1",
    created_by: "dashboard",
    ai: null,
    audience_filters: [],
    idempotency_key: null,
    source_ref: null,
    frequency: null,
  });

  assert.deepEqual(notificationCampaignDeliveryProjection({
    id: "delivery-1",
    status: "failed",
    provider: "",
    attempts: 0,
    last_error: "",
    queued_at: "2026-08-05T00:00:00.000Z",
    sent_at: "",
    delivered_at: undefined,
    failed_at: "2026-08-05T00:01:00.000Z",
    email: "reader@example.test",
    locale: "ru",
    list_code: "reports",
  }, metadata, "re****@example.test"), {
    id: "delivery-1",
    status: "failed",
    provider: "smtp",
    attempts: 0,
    last_error: null,
    queued_at: "2026-08-05T00:00:00.000Z",
    sent_at: null,
    delivered_at: null,
    failed_at: "2026-08-05T00:01:00.000Z",
    email_masked: "re****@example.test",
    locale: "zh",
    list_code: "reports",
  });
});
