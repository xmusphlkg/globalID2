import { boundedText, normalizeCode, toArray } from "./input.ts";
import { configInt } from "./http.ts";

export type SubscriptionPayload = Record<string, unknown>;

export const FREQUENCIES = new Set(["instant", "daily", "weekly", "monthly"]);
export const SUBSCRIPTION_STATUSES = new Set(["pending", "active", "paused", "unsubscribed", "expired"]);
export const LOCALE_LABELS: Record<string, { label_en: string; label_zh: string }> = {
  en: { label_en: "English", label_zh: "英文" },
  zh: { label_en: "Chinese", label_zh: "中文" },
  ja: { label_en: "Japanese", label_zh: "日文" },
  ko: { label_en: "Korean", label_zh: "韩文" },
  es: { label_en: "Spanish", label_zh: "西班牙文" },
  fr: { label_en: "French", label_zh: "法文" },
  de: { label_en: "German", label_zh: "德文" },
  pt: { label_en: "Portuguese", label_zh: "葡萄牙文" },
};

export function pendingCutoff(value: string | undefined, nowMs = Date.now()): string {
  const days = configInt(value, 14, 1, 365);
  return new Date(nowMs - days * 24 * 60 * 60 * 1000).toISOString();
}

export function rowsToCounts(rows: Array<{ status: string; count: number }>): Record<string, number> {
  return rows.reduce<Record<string, number>>((acc, row) => {
    acc[row.status] = Number(row.count || 0);
    return acc;
  }, {});
}

export function parseFilterGroups(value: string): Record<string, string[]> {
  const groups: Record<string, string[]> = {};
  for (const item of value.split("|")) {
    const [type, ...rest] = item.split(":");
    const filterValue = rest.join(":");
    if (!type || !filterValue) continue;
    groups[type] = groups[type] || [];
    groups[type].push(filterValue);
  }
  return groups;
}

export function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!local || !domain) return email;
  const visible = local.length <= 2 ? local[0] || "" : local.slice(0, 2);
  return `${visible}${"*".repeat(Math.max(2, local.length - visible.length))}@${domain}`;
}

export function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, "\\$&");
}

export function normalizeListCodes(payload: SubscriptionPayload): string[] {
  const values = toArray(payload.list_codes ?? payload.lists ?? payload.list_code ?? payload.list);
  const unique = [...new Set(values.map(normalizeCode).filter(Boolean))];
  return unique.length ? unique : ["reports"];
}

export function normalizeFilters(payload: SubscriptionPayload): Array<{ type: string; value: string }> {
  const result: Array<{ type: string; value: string }> = [];
  const specs = [
    { type: "country", keys: ["country", "countries"] },
    { type: "disease", keys: ["disease", "diseases"] },
    { type: "report_type", keys: ["report_type", "report_types"] },
    { type: "severity", keys: ["severity", "severities"] },
  ];
  for (const spec of specs) {
    for (const value of spec.keys.flatMap((key) => toArray(payload[key]))) {
      const normalized = normalizeFilter(spec.type, value);
      if (normalized) result.push({ type: spec.type, value: normalized });
    }
  }
  const seen = new Set<string>();
  return result.filter((item) => {
    const key = `${item.type}:${item.value}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function normalizeFilter(type: string, value: string): string {
  const text = boundedText(value, "", 120);
  if (!text) return "";
  return type === "country" ? text.toUpperCase() : text.toLowerCase();
}
