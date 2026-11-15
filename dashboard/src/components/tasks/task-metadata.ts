import type { Color } from "@/components/ui/tremor";
import type { TaskDetail } from "@/lib/hooks/useTasks";
import { getSourceDisplayLabel } from "@/lib/source-labels";

export function asDisplayString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }

  return null;
}

export function extractStructuredRows(value: string | null): Array<{ label: string; value: string }> {
  if (!value) return [];

  const lines = value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const source = lines.length === 1 ? lines[0].split(",").map((part) => part.trim()) : lines;
  const rows = source
    .map((part) => {
      const separator = part.indexOf(":");
      if (separator <= 0 || separator >= part.length - 1) {
        return null;
      }
      return {
        label: part.slice(0, separator).trim(),
        value: part.slice(separator + 1).trim(),
      };
    })
    .filter((row): row is { label: string; value: string } => !!row && !!row.label && !!row.value);

  return rows.length >= 2 ? rows : [];
}

export function normalizeStructuredRows(rows: Array<{ label: string; value: string }>) {
  return rows.map((row) => {
    if (row.label.trim().toLowerCase() === "source") {
      return { ...row, value: getSourceDisplayLabel(row.value, "en") };
    }
    return row;
  });
}

export function metadataString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  return null;
}

export function booleanLabel(value: unknown): string | null {
  if (typeof value === "boolean") return value ? "yes" : "no";
  return null;
}

export function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asDisplayString(item))
    .filter((item): item is string => !!item);
}

export function deriveRawArchivePath(taskDetail?: TaskDetail): string | null {
  if (!taskDetail) return null;
  const input = (taskDetail.input_data as Record<string, unknown> | null) ?? null;
  if (!input || input.save_raw !== true) return null;
  const countryCode =
    asDisplayString(input.country_code) ??
    asDisplayString(input.country) ??
    taskDetail.country_code ??
    null;
  if (!countryCode) return null;
  return `data/raw/${countryCode.toLowerCase()}`;
}

export function tokenTotal(tokens: Record<string, unknown> | null | undefined): number {
  if (!tokens || typeof tokens !== "object") return 0;
  const total = tokens.total;
  return typeof total === "number" ? total : 0;
}

export function qualityOverall(qualityScores: Record<string, unknown> | null | undefined): string {
  if (!qualityScores || typeof qualityScores !== "object") return "-";
  for (const key of ["overall", "quality", "score", "final"]) {
    const value = qualityScores[key];
    if (typeof value === "number") {
      return value.toFixed(2);
    }
  }
  return "-";
}

export interface StageSnapshot {
  stage: string;
  attempt: number;
  status: string;
  error?: string | null;
  metrics?: Record<string, unknown> | null;
}

export function latestStageSnapshots(stages: StageSnapshot[]): StageSnapshot[] {
  if (!stages.length) return [];
  const attempt = Math.max(...stages.map((stage) => stage.attempt));
  const order = new Map(["collect", "organize", "store"].map((name, index) => [name, index]));
  return stages
    .filter((stage) => stage.attempt === attempt)
    .sort((left, right) => (order.get(left.stage) ?? 99) - (order.get(right.stage) ?? 99) || left.stage.localeCompare(right.stage));
}

export function runStatusColor(status: string): Color {
  const normalized = status.toLowerCase();
  if (normalized === "completed") return "emerald";
  if (normalized === "running") return "amber";
  if (normalized === "failed") return "rose";
  if (normalized === "cancelled") return "slate";
  return "blue";
}
