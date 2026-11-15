

import { t } from "@/lib/i18n";

export const statusLabelKey = {
  pending: "report_status_pending",
  generating: "report_status_generating",
  completed: "report_status_completed",
  failed: "report_status_failed",
  reviewing: "report_status_reviewing",
  approved: "report_status_approved",
  published: "report_status_published",
} as const;

export type FlowGroupId = "queue" | "review" | "published";

export type FlowGroup = {
  id: FlowGroupId;
  titleKey: "flow_queue" | "flow_review" | "flow_published";
  statuses: string[];
};

export const FLOW_GROUPS: FlowGroup[] = [
  { id: "queue", titleKey: "flow_queue", statuses: ["pending", "generating", "failed"] },
  { id: "review", titleKey: "flow_review", statuses: ["reviewing", "completed", "approved"] },
  { id: "published", titleKey: "flow_published", statuses: ["published"] },
];

export function statusLabel(lang: "en" | "zh", status: string) {
  const key = statusLabelKey[status as keyof typeof statusLabelKey];
  return key ? t(lang, key) : status;
}

export function percent(score: number | null | undefined) {
  if (score == null) return "--";
  return `${Math.round(score * 100)}%`;
}

// Reporting windows are UTC calendar dates, not local event timestamps.
export function periodDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}/.exec(value)?.[0] ?? "—";
}

export function extractSectionDisease(sectionTitle: string | null | undefined, fallback: string) {
  const title = (sectionTitle || "").trim();
  if (!title) return fallback;

  const separators = [" - ", " – ", " — ", ":", "："];
  for (const separator of separators) {
    const index = title.indexOf(separator);
    if (index > 0) {
      const candidate = title.slice(0, index).trim();
      if (candidate) return candidate;
    }
  }

  return fallback;
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function asNullableString(value: unknown): string | null {
  const text = typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
  return text || null;
}

export function formatRiskName(item: Record<string, unknown>, lang: "en" | "zh") {
  return asString(lang === "zh" ? item.name_zh : item.name_en) || asString(item.name_en) || asString(item.disease_id) || "-";
}
