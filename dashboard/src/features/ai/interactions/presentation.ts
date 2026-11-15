import { CHART_TOKENS } from "@/lib/chart-theme";
import { ApiError } from "@/lib/api";

export function queryErrorText(error: unknown, lang: "en" | "zh"): string {
  const fallback = lang === "zh" ? "请检查后端 API 路由和服务状态。" : "Please check backend API route and server status.";

  if (error instanceof ApiError) {
    return `Request failed (${error.status}). ${error.message || fallback}`;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return lang === "zh" ? "加载 AI 交互数据失败。" : "Failed to load AI interaction data.";
}

export function jsonString(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

export function qualityText(value: number | null): string {
  if (value == null) return "-";
  return value.toFixed(2);
}

export function shortUuid(value: string | null): string {
  if (!value) return "-";
  if (value.length <= 12) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

export function metadataString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

export function stageLabel(value: string | null): string | null {
  if (!value) return null;
  return value.replace(/_/g, " ");
}

export function rowStatusColor(status: string | null): string {
  if (!status) return CHART_TOKENS.neutral;
  const key = status.toLowerCase();
  if (key === "completed" || key === "approved" || key === "published") return CHART_TOKENS.success;
  if (key === "failed" || key === "cancelled") return CHART_TOKENS.destructive;
  if (key === "running" || key === "generating" || key === "reviewing") return CHART_TOKENS.warning;
  return CHART_TOKENS.info;
}
