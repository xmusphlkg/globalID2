import type { DiseaseAuditLogEntry } from "@/features/ai/api";

export type DiseaseAuditLang = "en" | "zh";

export const diseaseAuditCopy = {
  en: {
    eyebrow: "Data Governance",
    title: "Disease Ontology Audit",
    subtitle:
      "Use the model center to review duplicate disease concepts and newly observed unmapped disease terms.",
    runLocal: "Run local audit",
    runAI: "Run with AI",
    includeNew: "Scan current data for new disease candidates",
    maxCandidates: "Max AI candidates",
    modelRoutes: "Active model routes",
    noRoutes: "No active model-center routes available.",
    routeMissing:
      "Audit API route was not found. Restart the FastAPI backend so it loads the latest code.",
    highDuplicates: "High-confidence duplicates",
    mappingCandidates: "Mapping-term review",
    similarCandidates: "Similar-name review",
    newCandidates: "New disease candidates",
    aiReview: "AI recommendations",
    noData: "Run an audit to see findings.",
    noFindings: "No findings in this section.",
    generatedAt: "Generated",
    modelUsed: "Model used",
    logs: "Audit logs",
    degradedRouteHint:
      "No healthy route is available; the run will still try enabled, non-rate-limited routes and record each attempt.",
    loading: "Analyzing...",
    error: "Audit failed",
    runError500: "Disease audit request failed on server (500).",
    runError500Hint:
      "Common causes: model center route base URL points to a web page, proxy timeout, model quota/permission, or backend/frontend version mismatch. " +
      "Please check model route test results, proxy timeout settings, and backend logs around the request time.",
    runErrorTimeout:
      "Request timeout. The AI audit may be slow; please reduce AI candidates or check proxy timeout.",
    networkError:
      "Cannot reach the AI audit API route. Check that Next.js proxy and backend are both running.",
    runErrorUnknown:
      "Unable to get a clear error message. Retry once, then check browser console + backend logs.",
    rawErrorPrefix: "Raw error/response",
    merge: "Merge",
    keep: "Keep separate",
    add: "Add disease",
    review: "Human review",
  },
  zh: {
    eyebrow: "数据治理",
    title: "疾病本体审计",
    subtitle: "通过模型中心复核疾病重复实体，并识别当前数据中新出现但未映射的传染病候选。",
    runLocal: "仅运行本地审计",
    runAI: "使用 AI 分析",
    includeNew: "扫描当前数据中的新增疾病候选",
    maxCandidates: "AI 候选上限",
    modelRoutes: "可用模型路由",
    noRoutes: "当前没有可用的模型中心路由。",
    routeMissing: "没有找到审计 API 路由。请重启 FastAPI 后端，让它加载最新代码。",
    highDuplicates: "高置信重复",
    mappingCandidates: "映射术语复核",
    similarCandidates: "相似名称复核",
    newCandidates: "新增疾病候选",
    aiReview: "AI 建议",
    noData: "运行一次审计后查看结果。",
    noFindings: "本区暂无发现。",
    generatedAt: "生成时间",
    modelUsed: "使用模型",
    logs: "审计日志",
    degradedRouteHint:
      "当前没有健康路由；运行时仍会尝试已启用且未限流的路由，并记录每一次尝试。",
    loading: "分析中...",
    error: "审计失败",
    runError500: "服务器返回 500（内部错误）。",
    runError500Hint:
      "常见原因：模型中心路由 base_url 配置成了网页地址、代理超时、模型配额/鉴权异常、前后端版本不一致。" +
      "请先检查模型路由测试结果、代理超时配置，并查看对应时刻后端日志。",
    runErrorTimeout: "请求超时。AI 审计可能耗时过长，请减小候选上限或检查代理超时配置。",
    networkError: "无法访问 AI 审计接口，请确认 Next.js 代理与 FastAPI 后端均已启动且可通信。",
    runErrorUnknown: "暂未提取到完整报错，请重试后查看浏览器控制台与后端日志。",
    rawErrorPrefix: "原始错误/响应",
    merge: "建议合并",
    keep: "建议保留",
    add: "建议新增",
    review: "人工复核",
  },
} as const;

export type DiseaseAuditCopy = (typeof diseaseAuditCopy)[DiseaseAuditLang];

export function decisionTone(decision?: string) {
  if (decision === "merge") return "success" as const;
  if (decision === "add_standard_disease") return "info" as const;
  if (decision === "keep_separate") return "neutral" as const;
  return "warning" as const;
}

export function decisionLabel(decision: string | undefined, lang: DiseaseAuditLang): string {
  if (decision === "merge") return diseaseAuditCopy[lang].merge;
  if (decision === "add_standard_disease") return diseaseAuditCopy[lang].add;
  if (decision === "keep_separate") return diseaseAuditCopy[lang].keep;
  return diseaseAuditCopy[lang].review;
}

export function logTone(level: string) {
  if (level === "error") return "danger" as const;
  if (level === "warning") return "warning" as const;
  return "neutral" as const;
}

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Object.prototype.toString.call(value) === "[object Object]";
}

export function trimLongText(value: string, max = 1600): string {
  const safe = value.trim();
  return safe.length <= max ? safe : `${safe.slice(0, max)}…`;
}

export function parseRunError(error: unknown) {
  const message =
    error instanceof Error
      ? error.message
      : isPlainObject(error) && typeof error.message === "string"
        ? error.message
        : String(error || "Unknown error");
  const status =
    isPlainObject(error) && Number.isInteger(error.status as number)
      ? Number(error.status)
      : undefined;
  return {
    status,
    message: message || "Unknown error",
    lowerMessage: message.toLowerCase(),
  };
}

export function formatDiseaseRunError(error: unknown, ui: DiseaseAuditCopy) {
  const { status, message, lowerMessage } = parseRunError(error);

  if (status === 404 || lowerMessage.includes("not found")) {
    return {
      message: ui.routeMissing,
      details: trimLongText(message),
    };
  }

  if (status === 408 || lowerMessage.includes("timeout")) {
    return {
      message: ui.runErrorTimeout,
      details: trimLongText(message),
    };
  }

  if (status === 500 || lowerMessage.includes("internal server error")) {
    return {
      message: ui.runError500,
      details: trimLongText(`${ui.runError500Hint}\n\n${ui.rawErrorPrefix}：\n${message}`),
    };
  }

  if (
    lowerMessage.includes("failed to fetch") ||
    lowerMessage.includes("networkerror") ||
    lowerMessage.includes("network error")
  ) {
    return {
      message: ui.networkError,
      details: trimLongText(message),
    };
  }

  if (!message || message === "Unknown error") {
    return {
      message: ui.runErrorUnknown,
      details: undefined,
    };
  }

  return {
    message,
    details: undefined,
  };
}

export function formatMetadata(metadata: Record<string, unknown> | undefined): string | null {
  if (!metadata || Object.keys(metadata).length === 0) return null;
  return JSON.stringify(metadata, null, 2);
}

export function getVisibleAuditLogs(
  logs: ReadonlyArray<DiseaseAuditLogEntry>,
  limit = 80,
): DiseaseAuditLogEntry[] {
  return [...logs].reverse().slice(0, limit);
}

export function getActiveAuditRoutes<T extends { available_for_routing?: boolean }>(
  routes?: ReadonlyArray<T>,
): T[] {
  return (routes ?? []).filter((route) => Boolean(route.available_for_routing));
}

export function resolveAuditLogs(
  resultLogs?: DiseaseAuditLogEntry[],
  persistedLogs?: DiseaseAuditLogEntry[],
): DiseaseAuditLogEntry[] {
  return resultLogs?.length ? resultLogs : (persistedLogs ?? []);
}
