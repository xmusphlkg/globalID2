

import { ApiError } from "@/lib/api";

import { type AIRuntimeRoute } from "@/features/ai/api";

export function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function formatDuration(seconds: number, lang: "en" | "zh"): string {
  if (!seconds || seconds <= 0) return lang === "zh" ? "0秒" : "0s";
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (lang === "zh") {
    if (mins > 0 && secs > 0) return `${mins}分 ${secs}秒`;
    if (mins > 0) return `${mins}分`;
    return `${secs}秒`;
  }
  if (mins > 0 && secs > 0) return `${mins}m ${secs}s`;
  if (mins > 0) return `${mins}m`;
  return `${secs}s`;
}

export function queryErrorText(error: unknown, lang: "en" | "zh"): string {
  const fallback = lang === "zh" ? "接口返回失败，请检查模型中心后端服务。" : "Request failed. Please check the model-center backend service.";
  if (error instanceof ApiError) return `Request failed (${error.status}). ${error.message || fallback}`;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function mutationErrorText(error: unknown, lang: "en" | "zh"): string {
  if (error instanceof Error && error.message) return error.message;
  return lang === "zh" ? "操作失败，请检查接口返回。" : "Action failed. Please review the API response.";
}

export function statusTone(status: string): "neutral" | "info" | "success" | "warning" | "danger" | "primary" {
  const normalized = (status || "").toLowerCase();
  if (normalized === "available") return "success";
  if (normalized === "probe_failed") return "info";
  if (normalized === "rate_limited" || normalized === "checking") return "warning";
  if (normalized === "unavailable" || normalized === "failed") return "danger";
  return "neutral";
}

export function routingStatusTone(status: string): "neutral" | "info" | "success" | "warning" | "danger" | "primary" {
  if (status === "routable") return "success";
  if (["cooling_down", "degraded", "rate_limited"].includes(status)) return "warning";
  if (status === "outside_window") return "info";
  if (["missing_credentials", "probe_unavailable"].includes(status)) return "danger";
  return "neutral";
}

export function routingStatusLabel(status: string, isZh: boolean): string {
  const labels: Record<string, [string, string]> = {
    routable: ["实际可用", "routable"],
    cooling_down: ["运行冷却", "cooling down"],
    degraded: ["等待自动恢复", "awaiting recovery"],
    rate_limited: ["限流冷却", "rate limited"],
    missing_credentials: ["缺少密钥", "missing key"],
    probe_unavailable: ["探针失败", "probe unavailable"],
    outside_window: ["时段外", "outside window"],
  };
  const label = labels[status] ?? [status || "未知", status || "unknown"];
  return isZh ? label[0] : label[1];
}

export function routingStatusReason(route: AIRuntimeRoute, isZh: boolean): string {
  if (!isZh) return route.routing_status_reason || "-";
  const reasons: Record<string, string> = {
    routable: "已通过实际路由准入检查，可以接收生产任务。",
    cooling_down: "真实任务刚刚失败，当前处于短暂冷却期。",
    degraded: "真实任务连续失败，正在等待自动恢复探测。",
    rate_limited: "供应商或模型限流，正在等待冷却结束。",
    missing_credentials: "尚未配置供应商 API 密钥。",
    probe_unavailable: "最近一次模型或供应商负载探测未通过。",
    outside_window: "当前不在该模型配置的可用时段内。",
  };
  return reasons[route.routing_status] || route.routing_status_reason || "-";
}
