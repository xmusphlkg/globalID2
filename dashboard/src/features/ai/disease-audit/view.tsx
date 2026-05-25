"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, FileSearch, GitMerge, PlusCircle, ShieldCheck } from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  type DiseaseAuditLogEntry,
  type DiseaseAuditFinding,
  type DiseaseAuditRecommendation,
  useDiseaseDuplicateAuditLogs,
  useDiseaseDuplicateAuditStatus,
  useRunDiseaseDuplicateAudit,
} from "@/features/ai/api";
import { useAppStore } from "@/stores/app-store";

const copy = {
  en: {
    eyebrow: "AI Programs",
    title: "Disease Ontology Audit",
    subtitle: "Use the model center to review duplicate disease concepts and newly observed unmapped disease terms.",
    runLocal: "Run local audit",
    runAI: "Run with AI",
    includeNew: "Scan current data for new disease candidates",
    maxCandidates: "Max AI candidates",
    modelRoutes: "Active model routes",
    noRoutes: "No active model-center routes available.",
    routeMissing: "Audit API route was not found. Restart the FastAPI backend so it loads the latest code.",
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
    degradedRouteHint: "No healthy route is available; the run will still try enabled, non-rate-limited routes and record each attempt.",
    loading: "Analyzing...",
    error: "Audit failed",
    runError500: "Disease audit request failed on server (500).",
    runError500Hint:
      "Common causes: model center route base URL points to a web page, proxy timeout, model quota/permission, or backend/frontend version mismatch. "
      + "Please check model route test results, proxy timeout settings, and backend logs around the request time.",
    runErrorTimeout: "Request timeout. The AI audit may be slow; please reduce AI candidates or check proxy timeout.",
    networkError: "Cannot reach the AI audit API route. Check that Next.js proxy and backend are both running.",
    runErrorUnknown: "Unable to get a clear error message. Retry once, then check browser console + backend logs.",
    rawErrorPrefix: "Raw error/response",
    merge: "Merge",
    keep: "Keep separate",
    add: "Add disease",
    review: "Human review",
  },
  zh: {
    eyebrow: "AI 程序",
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
    degradedRouteHint: "当前没有健康路由；运行时仍会尝试已启用且未限流的路由，并记录每一次尝试。",
    loading: "分析中...",
    error: "审计失败",
    runError500: "服务器返回 500（内部错误）。",
    runError500Hint:
      "常见原因：模型中心路由 base_url 配置成了网页地址、代理超时、模型配额/鉴权异常、前后端版本不一致。"
      + "请先检查模型路由测试结果、代理超时配置，并查看对应时刻后端日志。",
    runErrorTimeout: "请求超时。AI 审计可能耗时过长，请减小候选上限或检查代理超时配置。",
    networkError: "无法访问 AI 审计接口，请确认 Next.js 代理与 FastAPI 后端均已启动且可通信。",
    runErrorUnknown: "暂未提取到完整报错，请重试后查看浏览器控制台与后端日志。",
    rawErrorPrefix: "原始错误/响应",
    merge: "建议合并",
    keep: "建议保留",
    add: "建议新增",
    review: "人工复核",
  },
};

function decisionTone(decision?: string) {
  if (decision === "merge") return "success" as const;
  if (decision === "add_standard_disease") return "info" as const;
  if (decision === "keep_separate") return "neutral" as const;
  return "warning" as const;
}

function decisionLabel(decision: string | undefined, lang: "en" | "zh") {
  if (decision === "merge") return copy[lang].merge;
  if (decision === "add_standard_disease") return copy[lang].add;
  if (decision === "keep_separate") return copy[lang].keep;
  return copy[lang].review;
}

function AlertPanel({
  title,
  message,
  details,
  tone,
}: {
  title: string;
  message: string;
  details?: string | null;
  tone: "warning" | "danger";
}) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200"
      : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200";

  return (
    <section className={`rounded-tremor-default border p-4 ${toneClass}`}>
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-1 whitespace-pre-wrap text-sm">{message}</p>
          {details ? (
            <pre className="mt-2 max-h-48 overflow-auto rounded-tremor-default bg-white/80 px-3 py-2 text-xs dark:bg-black/10">
              {details}
            </pre>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function FindingList({
  title,
  items,
  empty,
}: {
  title: string;
  items?: DiseaseAuditFinding[];
  empty: string;
}) {
  return (
    <section className="app-panel p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{title}</h2>
        <StatusBadge tone={items?.length ? "warning" : "success"}>{items?.length ?? 0}</StatusBadge>
      </div>
      {!items?.length ? (
        <p className="mt-4 text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{empty}</p>
      ) : (
        <div className="mt-4 max-h-[420px] space-y-3 overflow-y-auto pr-1">
          {items.map((item, index) => (
            <div
              key={`${item.category}-${index}`}
              className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle/60 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/50"
            >
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge>{item.category}</StatusBadge>
                {item.candidate_ids?.map((id) => (
                  <StatusBadge key={id} tone="info">{id}</StatusBadge>
                ))}
                {item.country_code ? <StatusBadge tone="success">{item.country_code}</StatusBadge> : null}
                {item.row_count ? <StatusBadge tone="warning">{item.row_count} rows</StatusBadge> : null}
              </div>
              <p className="mt-2 text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.finding}
              </p>
              {item.raw_terms?.length ? (
                <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  Examples: {item.raw_terms.slice(0, 4).join(", ")}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function RecommendationList({
  items,
  lang,
}: {
  items?: DiseaseAuditRecommendation[];
  lang: "en" | "zh";
}) {
  if (!items?.length) {
    return <p className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy[lang].noFindings}</p>;
  }

  return (
    <div className="space-y-3">
      {items.map((item, index) => (
        <div
          key={`${item.decision}-${index}`}
          className="app-panel p-4"
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge tone={decisionTone(item.decision)}>{decisionLabel(item.decision, lang)}</StatusBadge>
            {item.confidence ? <StatusBadge>{item.confidence}</StatusBadge> : null}
            {item.canonical_id ? <StatusBadge tone="info">canonical {item.canonical_id}</StatusBadge> : null}
            {item.merge_ids?.map((id) => <StatusBadge key={id} tone="success">merge {id}</StatusBadge>)}
          </div>
          <p className="mt-3 text-sm font-medium leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {item.finding}
          </p>
          {item.proposed_standard_name_en || item.proposed_standard_name_zh ? (
            <p className="mt-2 text-sm text-tremor-content dark:text-dark-tremor-content">
              Proposed: {item.proposed_standard_name_en || "-"} / {item.proposed_standard_name_zh || "-"}
            </p>
          ) : null}
          <p className="mt-2 text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
            {lang === "zh" ? item.rationale_zh || item.rationale_en : item.rationale_en || item.rationale_zh}
          </p>
        </div>
      ))}
    </div>
  );
}

function logTone(level: string) {
  if (level === "error") return "danger" as const;
  if (level === "warning") return "warning" as const;
  return "neutral" as const;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Object.prototype.toString.call(value) === "[object Object]";
}

function trimLongText(value: string, max = 1600): string {
  const safe = value.trim();
  return safe.length <= max ? safe : `${safe.slice(0, max)}…`;
}

function parseRunError(error: unknown) {
  const message = error instanceof Error
    ? error.message
    : isPlainObject(error) && typeof error.message === "string"
      ? error.message
      : String(error || "Unknown error");
  const status = isPlainObject(error) && Number.isInteger(error.status as number)
    ? Number(error.status)
    : undefined;
  return {
    status,
    message: message || "Unknown error",
    lowerMessage: message.toLowerCase(),
  };
}

function formatDiseaseRunError(error: unknown, ui: (typeof copy)["en" | "zh"]) {
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
    lowerMessage.includes("failed to fetch")
    || lowerMessage.includes("networkerror")
    || lowerMessage.includes("network error")
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

function formatMetadata(metadata: Record<string, unknown> | undefined): string | null {
  if (!metadata || Object.keys(metadata).length === 0) return null;
  return JSON.stringify(metadata, null, 2);
}

function AuditLogPanel({
  title,
  logs,
}: {
  title: string;
  logs: DiseaseAuditLogEntry[];
}) {
  if (!logs.length) return null;
  const visibleLogs = [...logs].reverse().slice(0, 80);

  return (
    <section className="app-panel p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {title}
        </h2>
        <StatusBadge>{logs.length}</StatusBadge>
      </div>
      <div className="mt-4 max-h-[360px] space-y-2 overflow-y-auto pr-1">
        {visibleLogs.map((entry, index) => {
          const metadata = formatMetadata(entry.metadata);
          return (
            <div
              key={`${entry.run_id}-${entry.timestamp}-${entry.event}-${index}`}
              className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle"
            >
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge tone={logTone(entry.level)}>{entry.level}</StatusBadge>
                <StatusBadge>{entry.event}</StatusBadge>
                <span className="font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {new Date(entry.timestamp).toLocaleString()}
                </span>
                <span className="font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {entry.run_id.slice(0, 8)}
                </span>
              </div>
              <p className="mt-2 text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {entry.message}
              </p>
              {metadata ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    metadata
                  </summary>
                  <pre className="mt-2 max-h-44 overflow-auto rounded-tremor-default bg-tremor-background px-3 py-2 text-xs text-tremor-content dark:bg-dark-tremor-background dark:text-dark-tremor-content">
                    {metadata}
                  </pre>
                </details>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function DiseaseAuditPage() {
  const { lang } = useAppStore();
  const ui = copy[lang];
  const [includeNew, setIncludeNew] = useState(true);
  const [maxCandidates, setMaxCandidates] = useState(40);
  const { data: status, error: statusError } = useDiseaseDuplicateAuditStatus(includeNew);
  const { data: persistedLogs } = useDiseaseDuplicateAuditLogs(120);
  const runAudit = useRunDiseaseDuplicateAudit();
  const result = runAudit.data;

  const activeRoutes = useMemo(
    () => (status?.model_center.routes ?? []).filter((route) => route.available_for_routing),
    [status],
  );
  const auditLogs = result?.logs?.length ? result.logs : persistedLogs ?? [];
  const runAuditError = runAudit.error ? formatDiseaseRunError(runAudit.error, ui) : null;
  const statusApiError = statusError ? formatDiseaseRunError(statusError, ui) : null;

  const run = (includeAI: boolean) => {
    runAudit.mutate({
      include_ai: includeAI,
      include_new_disease_candidates: includeNew,
      max_ai_candidates: maxCandidates,
    });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={ui.eyebrow}
        title={ui.title}
        description={ui.subtitle}
        meta={
          <>
            {status ? <StatusBadge tone="success">Audit API ready</StatusBadge> : null}
            {activeRoutes.length ? (
              activeRoutes.slice(0, 4).map((route) => (
                <StatusBadge key={route.model_key} tone="info">
                  {route.provider_key} / {route.model_name}
                </StatusBadge>
              ))
            ) : (
              <StatusBadge tone="danger">{ui.noRoutes}</StatusBadge>
            )}
            {status?.model_center.route_count ? (
              <StatusBadge>
                {ui.modelRoutes}: {status.model_center.active_route_count}/{status.model_center.route_count}
              </StatusBadge>
            ) : null}
          </>
        }
      />

      <FilterToolbar>
        <label className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
          <input
            type="checkbox"
            checked={includeNew}
            onChange={(event) => setIncludeNew(event.target.checked)}
            className="h-4 w-4 rounded border-tremor-border text-tremor-brand"
          />
          {ui.includeNew}
        </label>

        <label className="flex items-center gap-2 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          <span>{ui.maxCandidates}</span>
          <input
            type="number"
            min={1}
            max={100}
            value={maxCandidates}
            onChange={(event) => setMaxCandidates(Number(event.target.value) || 40)}
            className="h-10 w-24 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm outline-none focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background"
          />
        </label>

        <button
          type="button"
          className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:opacity-60 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
          onClick={() => run(false)}
          disabled={runAudit.isPending}
        >
          <FileSearch className="h-4 w-4" />
          {ui.runLocal}
        </button>
        <button
          type="button"
          className="inline-flex h-10 items-center gap-2 rounded-tremor-default bg-tremor-brand px-3 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 disabled:opacity-60 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
          onClick={() => run(true)}
          disabled={runAudit.isPending}
        >
          <BrainCircuit className="h-4 w-4" />
          {runAudit.isPending ? ui.loading : ui.runAI}
        </button>
        {!activeRoutes.length && status?.model_center.route_count ? (
          <span className="text-xs text-amber-700 dark:text-amber-300">
            {ui.degradedRouteHint}
          </span>
        ) : null}
      </FilterToolbar>

      {statusError ? (
        <AlertPanel
          title={ui.error}
          tone="warning"
          message={statusApiError?.message || ui.runErrorUnknown}
          details={statusApiError?.details}
        />
      ) : null}

      {runAudit.error ? (
        <AlertPanel
          title={ui.error}
          tone="danger"
          message={runAuditError?.message || ui.runErrorUnknown}
          details={runAuditError?.details}
        />
      ) : null}

      <AuditLogPanel title={ui.logs} logs={auditLogs} />

      {!result ? (
        <EmptyState
          icon={<ShieldCheck className="h-12 w-12" />}
          title={ui.noData}
          className="rounded-tremor-default border border-dashed border-tremor-border bg-tremor-background py-16 dark:border-dark-tremor-border dark:bg-dark-tremor-background"
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricTile
              label={ui.highDuplicates}
              value={result.summary.high_confidence_standard_duplicates}
              tone="danger"
              icon={<AlertTriangle className="h-4 w-4" />}
            />
            <MetricTile
              label={ui.mappingCandidates}
              value={result.summary.mapping_term_review_candidates}
              tone="warning"
              icon={<FileSearch className="h-4 w-4" />}
            />
            <MetricTile
              label={ui.newCandidates}
              value={result.summary.new_disease_candidates}
              tone="info"
              icon={<PlusCircle className="h-4 w-4" />}
            />
            <MetricTile
              label={ui.similarCandidates}
              value={result.summary.similar_name_review_candidates}
              tone="neutral"
              icon={<ShieldCheck className="h-4 w-4" />}
            />
          </div>

          {result.ai_review ? (
            <section className="space-y-5 rounded-tremor-default border border-emerald-200 bg-emerald-50/50 p-4 dark:border-emerald-900/40 dark:bg-emerald-950/20">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <BrainCircuit className="h-5 w-5 text-emerald-700 dark:text-emerald-300" />
                    <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {ui.aiReview}
                    </h2>
                  </div>
                  <p className="mt-1 text-sm text-tremor-content dark:text-dark-tremor-content">
                    {ui.modelUsed}: {result.ai_review.model_route?.provider_key || "-"} / {result.ai_review.model_route?.model_name || "-"}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <StatusBadge tone="success"><GitMerge className="mr-1 h-3 w-3" />{ui.merge}: {result.ai_review.summary?.merge ?? 0}</StatusBadge>
                  <StatusBadge tone="info"><PlusCircle className="mr-1 h-3 w-3" />{ui.add}: {result.ai_review.summary?.add_standard_disease ?? 0}</StatusBadge>
                  <StatusBadge>{ui.keep}: {result.ai_review.summary?.keep_separate ?? 0}</StatusBadge>
                  <StatusBadge tone="warning">{ui.review}: {result.ai_review.summary?.needs_human_review ?? 0}</StatusBadge>
                </div>
              </div>
              <RecommendationList items={result.ai_review.recommendations} lang={lang} />
            </section>
          ) : null}

          <div className="grid gap-4 xl:grid-cols-2">
            <FindingList title={ui.highDuplicates} items={result.high_confidence_standard_duplicates} empty={ui.noFindings} />
            <FindingList title={ui.newCandidates} items={result.new_disease_candidates} empty={ui.noFindings} />
            <FindingList title={ui.mappingCandidates} items={result.mapping_term_review_candidates} empty={ui.noFindings} />
            <FindingList title={ui.similarCandidates} items={result.similar_name_review_candidates} empty={ui.noFindings} />
          </div>

          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {ui.generatedAt}: {result.generated_at}
          </p>
        </>
      )}
    </div>
  );
}
