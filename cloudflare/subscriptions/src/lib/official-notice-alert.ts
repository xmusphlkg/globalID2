import { isRecord, normalizeLocale } from "./input.ts";
import { SituationAlertValidationError } from "./situation-alert.ts";
import type { JsonValue } from "./http.ts";

/** A public official-authority notice, not a retired statistical signal. */
export interface OfficialNoticeAlert extends Record<string, JsonValue> {
  schema_version: "official-notice-alert.v1";
  idempotency_key: string;
  release_id: string;
  public_url: string;
  alert_id: string;
  notice_id: string;
  outbreak_id: string;
  notice_status: "new" | "imported" | "ongoing" | "ended";
  published_date: string;
  source_url: string;
  title: string;
  summary_en: string;
  summary_zh: string;
  countries: string[];
  diseases: string[];
  review_verdict: "accept";
  review_mode: "cross_model";
  publication_status: "published";
}

function fail(message: string): never { throw new SituationAlertValidationError(message); }
function text(value: unknown, field: string, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.trim().length > max) fail(`invalid_official_${field}`);
  return value.trim();
}
function https(value: unknown, field: string): string {
  const raw = text(value, field, 2048);
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password) fail(`invalid_official_${field}`);
    return url.toString();
  } catch { return fail(`invalid_official_${field}`); }
}
function values(value: unknown, field: string, upper = false): string[] {
  if (!Array.isArray(value) || value.length > 50) fail(`invalid_official_${field}`);
  return [...new Set(value.map((item) => {
    const itemText = text(item, field, 120);
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(itemText)) fail(`invalid_official_${field}`);
    return upper ? itemText.toUpperCase() : itemText.toLowerCase();
  }))];
}

export function parseOfficialNoticeAlert(value: unknown): OfficialNoticeAlert {
  if (!isRecord(value) || value.schema_version !== "official-notice-alert.v1") fail("unsupported_official_notice_schema");
  const key = text(value.idempotency_key, "idempotency_key", 160);
  if (!/^(?:pilot:)?official-notice:[A-Za-z0-9._:-]+$/.test(key)) fail("invalid_official_idempotency_key");
  const alertId = text(value.alert_id, "alert_id", 160);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,129}$/.test(alertId) || key !== `official-notice:${alertId}:r1` && key !== `pilot:official-notice:${alertId}:r1`) fail("invalid_official_idempotency_key");
  const publishedDate = text(value.published_date, "published_date", 10);
  const publishedTime = Date.parse(`${publishedDate}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(publishedDate) || !Number.isFinite(publishedTime) ||
      new Date(publishedTime).toISOString().slice(0, 10) !== publishedDate) fail("invalid_official_published_date");
  if (value.review_verdict !== "accept" || value.review_mode !== "cross_model" || value.publication_status !== "published") fail("unapproved_official_notice");
  if (value.notice_status !== "new" && value.notice_status !== "imported" && value.notice_status !== "ongoing" && value.notice_status !== "ended") fail("invalid_official_notice_status");
  return {
    schema_version: "official-notice-alert.v1", idempotency_key: key,
    release_id: text(value.release_id, "release_id", 160),
    public_url: https(value.public_url, "public_url"), source_url: https(value.source_url, "source_url"),
    alert_id: alertId, notice_id: text(value.notice_id, "notice_id", 300),
    outbreak_id: text(value.outbreak_id, "outbreak_id", 160), notice_status: value.notice_status, published_date: publishedDate,
    title: text(value.title, "title", 200), summary_en: text(value.summary_en, "summary_en", 1200),
    summary_zh: text(value.summary_zh, "summary_zh", 1200),
    countries: values(value.countries, "countries", true), diseases: values(value.diseases, "diseases"),
    review_verdict: "accept", review_mode: "cross_model", publication_status: "published",
  };
}

export function officialNoticeContent(alert: OfficialNoticeAlert, locale: string): {subject: string; markdown: string} {
  const zh = normalizeLocale(locale, "en") === "zh";
  // Source summaries are treated as plain text, not trusted Markdown.
  const plain = (value: string) => value.replace(/[\[\]`*_]/g, "").replace(/[\r\n]+/g, " ");
  return {
    subject: `${zh ? "GIDS 官方通报动态" : "GIDS official notice update"}: ${plain(alert.title)}`,
    markdown: [
      plain(zh ? alert.summary_zh : alert.summary_en), "",
      `${zh ? "通报日期" : "Notice date"}: ${alert.published_date}`,
      `${zh ? "通报状态" : "Notice status"}: ${zh ? { new: "新发", imported: "输入", ongoing: "持续", ended: "结束" }[alert.notice_status] : alert.notice_status}`,
      `[${zh ? "查看 GIDS Situation" : "View GIDS Situation"}](${alert.public_url})`,
      `[${zh ? "查看官方来源" : "View official source"}](${alert.source_url})`, "",
      zh ? "此消息汇总已发布的官方通报，不构成公共卫生风险评级。" :
        "This summarizes a published official notice and is not a public-health risk rating.",
    ].join("\n"),
  };
}
