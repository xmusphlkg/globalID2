"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useAppStore } from "@/stores/app-store";
import { Badge, Card, Grid, Text, Title } from "@/components/ui/tremor";
import { MessageSquare, MessageSquareText, Search, ChevronDown, ListTodo, Settings2 } from "lucide-react";
import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { TaskWorkflowTopology } from "@/components/tasks/TaskWorkflowTopology";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge as UiStatusBadge } from "@/components/ui/StatusBadge";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";
import { usePaginatedTasks, useTaskDetail, useTaskEventStream } from "@/features/operations/tasks/api";
import {
  useAIInteractions,
  useAIInteractionSummary,
  type AIInteractionItem,
} from "@/features/ai/api";

function queryErrorText(error: unknown, lang: "en" | "zh"): string {
  const fallback = lang === "zh" ? "请检查后端 API 路由和服务状态。" : "Please check backend API route and server status.";

  if (error instanceof ApiError) {
    return `Request failed (${error.status}). ${error.message || fallback}`;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return lang === "zh" ? "加载 AI 交互数据失败。" : "Failed to load AI interaction data.";
}

function uiText(lang: "en" | "zh") {
  if (lang === "zh") {
    return {
      unknown: "未知",
      timestamp: "时间",
      taskUuid: "任务 UUID",
      taskStatus: "任务状态",
      reportUuid: "报告 UUID",
      runStatus: "运行状态",
      runUuid: "运行 UUID",
      duration: "耗时",
      section: "章节",
      runModel: "运行模型",
      promptTemp: "提问温度",
      systemPrompt: "系统提示词",
      prompt: "提示词",
      response: "回复",
      tokens: "Token",
      qualityScores: "质量评分详情",
      unknownSection: "未知章节",
      disease: "疾病",
      model: "模型",
      qualityShort: "质量",
      interactions: "交互数",
      totalTokens: "总 Token",
      avgTokens: "平均 Token",
      avgDuration: "平均耗时",
      avgQuality: "平均质量",
      byAgent: "按 Agent 统计",
      byModel: "按模型统计",
      taskFilterPlaceholder: "按任务 UUID 过滤（支持 ?task=...）",
      reportFilterPlaceholder: "按报告 UUID 过滤（兼容模式）",
      allAgents: "全部 Agent",
      allModels: "全部模型",
      allDiseases: "全部疾病",
      loadingErrorTitle: "无法加载交互数据",
      noData: "暂无交互数据",
      currentCountry: "当前国家筛选",
      chatModePrefix: "已启用聊天流程视图，任务 UUID",
      reportModePrefix: "兼容模式：按报告 UUID 查看",
      diseaseSuffix: "疾病",
      loading: "加载交互数据中...",
      subtitle: "优先按任务实时查看提示词、回复、Token 消耗、耗时和质量评分，兼容 ?task=... 或 ?uuid=...&disease=... 直达。",
      waitingForTaskBinding: "任务已创建，但暂时还没有可展示的 AI 交互；上方的流程日志会先展示任务生成过程，报告上下文就绪后页面会自动刷新。",
      workflowTopologyTitle: "任务流程拓扑",
      workflowTopologySubtitle: "把工作簿阶段日志转成流程图，展示知识库搭建每一步和对应模型。",
      workflowTopologyEmpty: "当前任务还没有足够的阶段数据来绘制流程拓扑。",
      workflowModels: "流程模型",
      workflowOnlyMode: "该知识库任务主要以工作流日志记录过程，可能不会生成传统对话消息；请以上方流程拓扑和阶段日志为准。",
      recentKnowledgeTasks: "最近知识库任务",
      recentKnowledgeTasksSubtitle: "这里会列出最近的知识库建设任务，点开即可查看流程拓扑和每一步使用的模型。",
      openWorkflow: "打开流程",
    } as const;
  }

  return {
    unknown: "unknown",
    timestamp: "Timestamp",
    taskUuid: "Task UUID",
    taskStatus: "Task Status",
    reportUuid: "Report UUID",
    runStatus: "Run Status",
    runUuid: "Run UUID",
    duration: "Duration",
    section: "Section",
    runModel: "Run Model",
    promptTemp: "Prompt Temp",
    systemPrompt: "System Prompt",
    prompt: "Prompt",
    response: "Response",
    tokens: "Tokens",
    qualityScores: "Quality Scores",
    unknownSection: "unknown section",
    disease: "Disease",
    model: "Model",
    qualityShort: "Q",
    interactions: "Interactions",
    totalTokens: "Total Tokens",
    avgTokens: "Avg Tokens",
    avgDuration: "Avg Duration",
    avgQuality: "Avg Quality",
    byAgent: "By Agent",
    byModel: "By Model",
    taskFilterPlaceholder: "Filter by task UUID (supports ?task=...)",
    reportFilterPlaceholder: "Filter by report UUID (compatibility mode)",
    allAgents: "All agents",
    allModels: "All models",
    allDiseases: "All diseases",
    loadingErrorTitle: "Unable to load interaction data",
    noData: "No interaction data found",
    currentCountry: "Current country filter",
    chatModePrefix: "Chat workflow mode enabled for task UUID",
    reportModePrefix: "Compatibility mode: viewing by report UUID",
    diseaseSuffix: "Disease",
    loading: "Loading interactions...",
    subtitle: "Inspect prompts, responses, token usage, durations and quality scoring in task-centric real time. Supports ?task=... and keeps ?uuid=...&disease=... for report mode.",
    waitingForTaskBinding: "The task exists, but there are no AI interactions to show yet. The workflow log above still shows the generation process, and the page will refresh once the report context is ready.",
    workflowTopologyTitle: "Workflow Topology",
    workflowTopologySubtitle: "Render workbook-stage logs as a topology so you can inspect each knowledge-build step and the model used.",
    workflowTopologyEmpty: "This task does not have enough stage data yet to render a workflow topology.",
    workflowModels: "Workflow Models",
    workflowOnlyMode: "This knowledge task is primarily recorded as workflow logs, so it may not emit traditional chat messages. Use the topology and staged log above as the source of truth.",
    recentKnowledgeTasks: "Recent Knowledge Tasks",
    recentKnowledgeTasksSubtitle: "Open a recent knowledge-building task to inspect its topology and the model used for each step.",
    openWorkflow: "Open Workflow",
  } as const;
}

function jsonString(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

function qualityText(value: number | null): string {
  if (value == null) return "-";
  return value.toFixed(2);
}

function shortUuid(value: string | null): string {
  if (!value) return "-";
  if (value.length <= 12) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function metadataString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function stageLabel(value: string | null): string | null {
  if (!value) return null;
  return value.replace(/_/g, " ");
}

function rowStatusColor(status: string | null): string {
  if (!status) return CHART_TOKENS.neutral;
  const key = status.toLowerCase();
  if (key === "completed" || key === "approved" || key === "published") return CHART_TOKENS.success;
  if (key === "failed" || key === "cancelled") return CHART_TOKENS.destructive;
  if (key === "running" || key === "generating" || key === "reviewing") return CHART_TOKENS.warning;
  return CHART_TOKENS.info;
}

function InteractionRow({
  item,
  expanded,
  onToggle,
  labels,
}: {
  item: AIInteractionItem;
  expanded: boolean;
  onToggle: () => void;
  labels: ReturnType<typeof uiText>;
}) {
  return (
    <Card className="overflow-hidden p-0">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle"
      >
        <Badge color="slate">{item.agent ?? labels.unknown}</Badge>
        <span className="flex-1 truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {item.report_title}
        </span>
        {item.task_uuid && <Badge color="amber">{shortUuid(item.task_uuid)}</Badge>}
        <Badge color="indigo">{shortUuid(item.report_uuid)}</Badge>
        <Badge color="blue">{item.model ?? "-"}</Badge>
        <span className="w-24 text-right text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {item.total_tokens} {labels.tokens}
        </span>
        <span className="w-16 text-right text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {labels.qualityShort} {qualityText(item.quality_overall)}
        </span>
        <span
          className="h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: rowStatusColor(item.run_status) }}
          aria-hidden="true"
        />
        <ChevronDown
          className={`h-4 w-4 text-tremor-content-subtle transition-transform dark:text-dark-tremor-content-subtle ${expanded ? "rotate-180" : ""}`}
        />
      </button>

      {expanded && (
        <div className="border-t border-tremor-border px-4 py-3 dark:border-dark-tremor-border">
          <Grid numItems={1} numItemsLg={4} className="mb-3 gap-3">
            <Card className="p-3">
              <Text>{labels.taskUuid}</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.task_uuid ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.taskStatus}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.task_status ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.timestamp}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {formatDateTime(item.timestamp)}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.reportUuid}</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.report_uuid}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.runStatus}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_status ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.runUuid}</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_uuid ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.duration}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.duration != null ? `${item.duration.toFixed(2)}s` : "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.section}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.section_type ?? "-"} {item.section_title ? `(${item.section_title})` : ""}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.runModel}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_provider ?? "-"} / {item.run_model ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>{labels.promptTemp}</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.temperature != null ? item.temperature.toFixed(2) : "-"}
              </Text>
            </Card>
          </Grid>

          <Grid numItems={1} numItemsLg={2} className="gap-3">
            <Card className="p-3">
              <Text className="mb-2">{labels.systemPrompt}</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.system_prompt || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.prompt}</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.prompt || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.response}</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.response || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.tokens}</Text>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {jsonString(item.tokens)}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">{labels.qualityScores}</Text>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {jsonString(item.quality_scores)}
              </pre>
            </Card>
          </Grid>
        </div>
      )}
    </Card>
  );
}

interface RunThread {
  taskUuid: string | null;
  taskName: string | null;
  taskStatus: string | null;
  runId: number;
  runUuid: string | null;
  reportUuid: string;
  reportTitle: string;
  reportStatus: string | null;
  runStatus: string | null;
  runModel: string | null;
  runProvider: string | null;
  runTemperature: number | null;
  sectionType: string | null;
  sectionTitle: string | null;
  diseaseName: string | null;
  qualityOverall: number | null;
  totalTokens: number;
  startedAt: string | null;
  endedAt: string | null;
  messages: AIInteractionItem[];
}

function GroupedRunChat({
  thread,
  labels,
}: {
  thread: RunThread;
  labels: ReturnType<typeof uiText>;
}) {
  return (
    <Card className="space-y-4 p-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {thread.taskUuid && <Badge color="amber">{labels.taskUuid} {shortUuid(thread.taskUuid)}</Badge>}
          <Badge color="indigo">{labels.reportUuid} {shortUuid(thread.reportUuid)}</Badge>
          <Badge color="slate">{labels.runUuid} {shortUuid(thread.runUuid)}</Badge>
          <Badge color="blue">{thread.sectionType ?? labels.unknownSection}</Badge>
          <Badge color="emerald">{thread.runStatus ?? "-"}</Badge>
        </div>
        <Text className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {thread.taskName || thread.reportTitle}
        </Text>
        <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {thread.sectionTitle ?? "-"} | {labels.disease}: {thread.diseaseName ?? "-"} | {labels.model}: {thread.runProvider ?? "-"}/{thread.runModel ?? "-"} | {labels.qualityShort} {qualityText(thread.qualityOverall)} | {labels.tokens} {thread.totalTokens} | {labels.taskStatus}: {thread.taskStatus ?? "-"}
        </Text>
        <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {formatDateTime(thread.startedAt)} {"->"} {formatDateTime(thread.endedAt)}
        </Text>
      </div>

      <div className="space-y-3">
        {thread.messages.map((item) => {
          const alignRight = (item.agent ?? "").toLowerCase() === "writer";
          return (
            <div key={item.id} className={`flex ${alignRight ? "justify-end" : "justify-start"}`}>
              <div className={`w-full max-w-4xl rounded-tremor-default border p-3 ${alignRight ? "border-blue-200 bg-blue-50/70 dark:border-blue-900/70 dark:bg-blue-950/20" : "border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-slate-900/20"}`}>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge color="slate">{item.agent ?? labels.unknown}</Badge>
                  <Badge color="blue">{item.role ?? "-"}</Badge>
                  <Badge color="indigo">{item.model ?? "-"}</Badge>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {formatDateTime(item.timestamp)}
                  </Text>
                </div>

                <details className="mb-2 rounded-md border border-tremor-border/70 px-2 py-1 dark:border-dark-tremor-border/70">
                  <summary className="cursor-pointer text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.systemPrompt}
                  </summary>
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                    {item.system_prompt || "-"}
                  </pre>
                </details>

                <Text className="mb-1 text-xs font-medium">{labels.prompt}</Text>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-white/80 p-2 text-xs text-tremor-content dark:bg-black/20 dark:text-dark-tremor-content">
                  {item.prompt || "-"}
                </pre>

                <Text className="mb-1 mt-2 text-xs font-medium">{labels.response}</Text>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-white/80 p-2 text-xs text-tremor-content dark:bg-black/20 dark:text-dark-tremor-content">
                  {item.response || "-"}
                </pre>

                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.tokens}: {item.total_tokens}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.duration}: {item.duration != null ? `${item.duration.toFixed(2)}s` : "-"}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    temp: {item.temperature != null ? item.temperature.toFixed(2) : "-"}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {labels.qualityShort}: {qualityText(item.quality_overall)}
                  </Text>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function AIInteractionsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { countryCode, countryName, lang } = useAppStore();
  const labels = useMemo(() => uiText(lang), [lang]);
  const searchParamsString = searchParams.toString();
  const initialTaskUuid = (searchParams.get("task") ?? searchParams.get("task_uuid") ?? "").trim();
  const initialUuid = (searchParams.get("uuid") ?? searchParams.get("report_uuid") ?? "").trim();
  const initialDisease = (searchParams.get("disease") ?? "").trim();

  const [agentFilter, setAgentFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [taskUuidFilter, setTaskUuidFilter] = useState(initialTaskUuid);
  const [reportUuidFilter, setReportUuidFilter] = useState(initialUuid);
  const [diseaseFilter, setDiseaseFilter] = useState(initialDisease);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const activeTaskUuid = taskUuidFilter.trim();
  const activeReportUuid = reportUuidFilter.trim();
  const hasTaskScope = Boolean(activeTaskUuid || activeReportUuid);

  useEffect(() => {
    const params = new URLSearchParams(searchParamsString);
    const nextTaskUuid = (params.get("task") ?? params.get("task_uuid") ?? "").trim();
    const nextUuid = (params.get("uuid") ?? params.get("report_uuid") ?? "").trim();
    const nextDisease = (params.get("disease") ?? "").trim();
    if (nextTaskUuid !== taskUuidFilter) {
      setTaskUuidFilter(nextTaskUuid);
    }
    if (nextUuid !== reportUuidFilter) {
      setReportUuidFilter(nextUuid);
    }
    if (nextDisease !== diseaseFilter) {
      setDiseaseFilter(nextDisease);
    }
  }, [diseaseFilter, reportUuidFilter, searchParamsString, taskUuidFilter]);

  useEffect(() => {
    const params = new URLSearchParams(searchParamsString);
    const currentTaskUuid = (params.get("task") ?? "").trim();
    const currentUuid = (params.get("uuid") ?? "").trim();
    const currentDisease = (params.get("disease") ?? "").trim();
    const nextTaskUuid = taskUuidFilter.trim();
    const nextUuid = reportUuidFilter.trim();
    const nextDisease = diseaseFilter.trim();

    if (nextTaskUuid === currentTaskUuid && nextUuid === currentUuid && nextDisease === currentDisease && !params.get("report_uuid") && !params.get("task_uuid")) {
      return;
    }

    if (nextTaskUuid) {
      params.set("task", nextTaskUuid);
    } else {
      params.delete("task");
    }
    if (nextUuid) {
      params.set("uuid", nextUuid);
    } else {
      params.delete("uuid");
    }
    if (nextDisease) {
      params.set("disease", nextDisease);
    } else {
      params.delete("disease");
    }
    params.delete("report_uuid");
    params.delete("task_uuid");

    const nextQuery = params.toString();
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
  }, [diseaseFilter, pathname, reportUuidFilter, router, searchParamsString, taskUuidFilter]);

  const interactionLimit = hasTaskScope ? 1000 : 200;
  const filters = {
    countryCode: countryCode || undefined,
    taskUuid: activeTaskUuid || undefined,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    disease: diseaseFilter.trim() || undefined,
    reportUuid: activeReportUuid || undefined,
    limit: interactionLimit,
  };

  useTaskEventStream({
    extraQueryKeys: [["ai-interactions"], ["ai-interactions-summary"], ["reports"], ["report-runs"]],
  });

  const liveRefreshMs = hasTaskScope ? 3000 : 5000;

  const { data: taskDetail, isFetching: taskDetailLoading } = useTaskDetail(activeTaskUuid || null);
  const { data: recentKnowledgeTaskPage } = usePaginatedTasks(
    undefined,
    "update_disease_knowledge",
    countryCode || undefined,
    diseaseFilter.trim() || undefined,
    8,
    0,
  );

  const { data: interactions, isLoading, isError, error } = useAIInteractions(filters, {
    refetchIntervalMs: liveRefreshMs,
  });
  const { data: summary } = useAIInteractionSummary({
    countryCode: countryCode || undefined,
    taskUuid: activeTaskUuid || undefined,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    disease: diseaseFilter.trim() || undefined,
    reportUuid: activeReportUuid || undefined,
  }, {
    refetchIntervalMs: liveRefreshMs,
  });

  const agentOptions = useMemo(() => {
    const values = new Set<string>();
    (interactions ?? []).forEach((item) => {
      if (item.agent) values.add(item.agent);
    });
    return Array.from(values).sort();
  }, [interactions]);

  const modelOptions = useMemo(() => {
    const values = new Set<string>();
    (interactions ?? []).forEach((item) => {
      if (item.model) values.add(item.model);
    });
    return Array.from(values).sort();
  }, [interactions]);

  const diseaseOptions = useMemo(() => {
    const values = new Set<string>();
    (interactions ?? []).forEach((item) => {
      if (item.disease_name) values.add(item.disease_name);
    });
    return Array.from(values).sort();
  }, [interactions]);

  const byAgentData = useMemo(() => {
    return Object.entries(summary?.by_agent ?? {}).map(([name, value]) => ({ name, value }));
  }, [summary]);

  const byModelData = useMemo(() => {
    return Object.entries(summary?.by_model ?? {}).map(([name, value]) => ({ name, value }));
  }, [summary]);

  const runThreads = useMemo<RunThread[]>(() => {
    const groups = new Map<number, AIInteractionItem[]>();

    (interactions ?? []).forEach((item) => {
      const arr = groups.get(item.run_id) ?? [];
      arr.push(item);
      groups.set(item.run_id, arr);
    });

    return Array.from(groups.entries()).map(([runId, messages]) => {
      const ordered = [...messages].sort((a, b) => {
        const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0;
        const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0;
        return ta - tb;
      });

      const first = ordered[0];
      const last = ordered[ordered.length - 1];
      const totalTokens = ordered.reduce((acc, row) => acc + row.total_tokens, 0);

      return {
        taskUuid: first?.task_uuid ?? null,
        taskName: first?.task_name ?? null,
        taskStatus: first?.task_status ?? null,
        runId,
        runUuid: first?.run_uuid ?? null,
        reportUuid: first?.report_uuid ?? "",
        reportTitle: first?.report_title ?? "",
        reportStatus: first?.report_status ?? null,
        runStatus: first?.run_status ?? null,
        runModel: first?.run_model ?? first?.model ?? null,
        runProvider: first?.run_provider ?? first?.provider ?? null,
        runTemperature: first?.run_temperature ?? null,
        sectionType: first?.section_type ?? null,
        sectionTitle: first?.section_title ?? null,
        diseaseName: first?.disease_name ?? null,
        qualityOverall: first?.quality_overall ?? null,
        totalTokens,
        startedAt: first?.timestamp ?? null,
        endedAt: last?.timestamp ?? null,
        messages: ordered,
      };
    }).sort((a, b) => {
      const ta = a.endedAt ? new Date(a.endedAt).getTime() : 0;
      const tb = b.endedAt ? new Date(b.endedAt).getTime() : 0;
      return tb - ta;
    });
  }, [interactions]);

  const interactionItems = interactions ?? [];
  const recentKnowledgeTasks = recentKnowledgeTaskPage?.items ?? [];
  const isKnowledgeTask = taskDetail?.task_type === "update_disease_knowledge";
  const workflowModels = useMemo(() => {
    if (!taskDetail) return [];
    const seen = new Set<string>();
    return taskDetail.workbook_entries
      .map((entry) => {
        const provider = metadataString(entry.metadata?.provider);
        const stage = stageLabel(metadataString(entry.metadata?.workflow_stage));
        const model = entry.model_used;
        if (!model && !provider) return null;
        const label = [stage, model ? `${provider ? `${provider}/` : ""}${model}` : provider].filter(Boolean).join(": ");
        if (!label || seen.has(label)) return null;
        seen.add(label);
        return label;
      })
      .filter((item): item is string => !!item);
  }, [taskDetail]);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_ai")}
        title={t(lang, "ai_interactions")}
        description={labels.subtitle}
        meta={
          <>
            <UiStatusBadge tone={activeTaskUuid ? "primary" : "neutral"}>
              {activeTaskUuid ? `${labels.taskUuid} ${shortUuid(activeTaskUuid)}` : labels.currentCountry}
            </UiStatusBadge>
            <UiStatusBadge tone="info">
              {summary?.total_interactions ?? 0} {labels.interactions}
            </UiStatusBadge>
          </>
        }
        actions={
          <>
          <Link
            href="/production/ai"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
          >
            <ListTodo className="h-4 w-4" />
            Open AI Tasks
          </Link>
          <Link
            href="/settings/models"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
          >
            <Settings2 className="h-4 w-4" />
            Open AI Models
          </Link>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricTile
          label={labels.interactions}
          value={summary?.total_interactions ?? 0}
          icon={<MessageSquareText className="h-4 w-4" />}
          tone="primary"
        />
        <MetricTile
          label={labels.totalTokens}
          value={summary?.total_tokens ?? 0}
          icon={<MessageSquare className="h-4 w-4" />}
          tone="info"
        />
        <MetricTile
          label={labels.avgTokens}
          value={Math.round(summary?.avg_tokens ?? 0)}
          tone="neutral"
        />
        <MetricTile
          label={labels.avgDuration}
          value={`${(summary?.avg_duration ?? 0).toFixed(2)}s`}
          tone="warning"
        />
        <MetricTile
          label={labels.avgQuality}
          value={summary?.avg_quality != null ? summary.avg_quality.toFixed(2) : "-"}
          tone="success"
        />
      </div>

      {!activeTaskUuid && recentKnowledgeTasks.length > 0 && (
        <Card className="border-emerald-200/70 bg-emerald-50/40 dark:border-emerald-900/60 dark:bg-emerald-950/10">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <Title className="text-base">{labels.recentKnowledgeTasks}</Title>
              <Text className="mt-1 text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {labels.recentKnowledgeTasksSubtitle}
              </Text>
            </div>
            <Link
              href="/production/ai?task_type=update_disease_knowledge"
              className="inline-flex items-center gap-1 rounded-tremor-default border border-emerald-300/70 bg-white px-3 py-1.5 text-xs font-medium text-emerald-700 transition hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300"
            >
              <ListTodo className="h-3.5 w-3.5" />
              Open AI Tasks
            </Link>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {recentKnowledgeTasks.map((task) => (
              <Link
                key={task.task_uuid}
                href={`/production/interactions?task=${encodeURIComponent(task.task_uuid)}`}
                className="rounded-tremor-default border border-tremor-border/80 bg-white/90 p-3 transition hover:border-emerald-300 dark:border-dark-tremor-border/80 dark:bg-white/5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge color="emerald">{task.status}</Badge>
                  <Badge color="slate">{task.progress}%</Badge>
                </div>
                <Text className="mt-2 line-clamp-2 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {task.task_name}
                </Text>
                <Text className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {shortUuid(task.task_uuid)}
                </Text>
                <Text className="mt-1 text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {formatDateTime(task.created_at)}
                </Text>
                <Text className="mt-3 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                  {labels.openWorkflow}
                </Text>
              </Link>
            ))}
          </div>
        </Card>
      )}

      {(byAgentData.length > 0 || byModelData.length > 0) && (
        <Grid numItems={1} numItemsLg={2} className="gap-4">
          <Card>
            <Title className="mb-2">{labels.byAgent}</Title>
            <Chart
              height={260}
              option={{
                tooltip: { trigger: "axis" },
                grid: { left: 120, right: 20, top: 10, bottom: 20 },
                xAxis: { type: "value" },
                yAxis: {
                  type: "category",
                  data: byAgentData.map((row) => row.name).reverse(),
                  axisLabel: { fontSize: 11 },
                },
                series: [
                  {
                    type: "bar",
                    data: byAgentData.map((row) => row.value).reverse(),
                    barMaxWidth: 20,
                    itemStyle: { color: CHART_TOKENS.info, borderRadius: [0, 4, 4, 0] },
                  },
                ],
              }}
            />
          </Card>

          <Card>
            <Title className="mb-2">{labels.byModel}</Title>
            <Chart
              height={260}
              option={{
                tooltip: { trigger: "axis" },
                grid: { left: 120, right: 20, top: 10, bottom: 20 },
                xAxis: { type: "value" },
                yAxis: {
                  type: "category",
                  data: byModelData.map((row) => row.name).reverse(),
                  axisLabel: { fontSize: 11 },
                },
                series: [
                  {
                    type: "bar",
                    data: byModelData.map((row) => row.value).reverse(),
                    barMaxWidth: 20,
                    itemStyle: { color: CHART_TOKENS.primary, borderRadius: [0, 4, 4, 0] },
                  },
                ],
              }}
            />
          </Card>
        </Grid>
      )}

      <FilterToolbar>
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <input
              type="text"
              value={taskUuidFilter}
              placeholder={labels.taskFilterPlaceholder}
              onChange={(e) => {
                const value = e.target.value;
                setTaskUuidFilter(value);
                if (value.trim()) {
                  setReportUuidFilter("");
                }
              }}
              className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            />
          </div>

          {!activeTaskUuid && (
            <div className="relative flex-1 min-w-[220px] max-w-md">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
              <input
                type="text"
                value={reportUuidFilter}
                onChange={(e) => setReportUuidFilter(e.target.value)}
                placeholder={labels.reportFilterPlaceholder}
                className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
              />
            </div>
          )}

          <select
            aria-label="Agent filter"
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          >
            <option value="">{labels.allAgents}</option>
            {agentOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>

          <select
            aria-label="Model filter"
            value={modelFilter}
            onChange={(e) => setModelFilter(e.target.value)}
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          >
            <option value="">{labels.allModels}</option>
            {modelOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>

          <select
            aria-label="Disease filter"
            value={diseaseFilter}
            onChange={(e) => setDiseaseFilter(e.target.value)}
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          >
            <option value="">{labels.allDiseases}</option>
            {diseaseOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
      </FilterToolbar>

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          ))}
        </div>
      ) : isError ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <MessageSquare className="h-12 w-12 text-red-500" />
            <Text className="mt-3 text-red-600">{labels.loadingErrorTitle}</Text>
            <Text className="mt-2 max-w-3xl text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {queryErrorText(error, lang)}
            </Text>
          </div>
        </Card>
      ) : activeTaskUuid ? (
        <div className="space-y-4">
          <div className="rounded-tremor-default border border-violet-200/60 bg-violet-50/35 p-4 dark:border-violet-900/50 dark:bg-violet-950/10">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <MessageSquareText className="h-4 w-4 text-violet-500" />
              <Title className="text-lg">{lang === "zh" ? "任务生成过程" : "Task generation process"}</Title>
              <Badge color="violet">{shortUuid(activeTaskUuid)}</Badge>
            </div>
            <Text className="mb-4 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh"
                ? "这里先展示任务级工作流、阶段进度和 AI payload。即使 chat 记录还没写入，也能先查看生成过程。"
                : "This section shows task-level workflow, stage progress, and AI payloads. Even before chat records are written, you can inspect the generation process here."}
            </Text>
            {workflowModels.length > 0 && (
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <Text className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {labels.workflowModels}
                </Text>
                {workflowModels.map((item) => (
                  <Badge key={item} color="indigo">{item}</Badge>
                ))}
              </div>
            )}
            <TaskWorkflowTopology
              taskDetail={taskDetail}
              labels={{
                title: labels.workflowTopologyTitle,
                subtitle: labels.workflowTopologySubtitle,
                empty: labels.workflowTopologyEmpty,
              }}
            />
            <TaskDetailPanel
              taskDetail={taskDetail}
              detailLoading={taskDetailLoading}
              emptyMessage={labels.waitingForTaskBinding}
            />
          </div>

          <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {labels.chatModePrefix}: {activeTaskUuid} {diseaseFilter.trim() ? `| ${labels.diseaseSuffix}: ${diseaseFilter.trim()}` : ""}
          </Text>
          {interactionItems.length === 0 ? (
            <Card>
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <MessageSquare className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                <Text className="mt-3">{isKnowledgeTask ? labels.workflowOnlyMode : labels.waitingForTaskBinding}</Text>
                <Text className="mt-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {labels.currentCountry}: {countryName || "All"}
                </Text>
              </div>
            </Card>
          ) : (
            runThreads.map((thread) => (
              <GroupedRunChat key={thread.runId} thread={thread} labels={labels} />
            ))
          )}
        </div>
      ) : activeReportUuid ? (
        <div className="space-y-4">
          <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {labels.reportModePrefix}: {activeReportUuid} {diseaseFilter.trim() ? `| ${labels.diseaseSuffix}: ${diseaseFilter.trim()}` : ""}
          </Text>
          {interactionItems.length === 0 ? (
            <Card>
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <MessageSquare className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                <Text className="mt-3">{labels.noData}</Text>
                <Text className="mt-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {labels.currentCountry}: {countryName || "All"}
                </Text>
              </div>
            </Card>
          ) : (
            runThreads.map((thread) => (
              <GroupedRunChat key={thread.runId} thread={thread} labels={labels} />
            ))
          )}
        </div>
      ) : interactionItems.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <MessageSquare className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Text className="mt-3">{labels.noData}</Text>
            <Text className="mt-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {labels.currentCountry}: {countryName || "All"}
            </Text>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {interactionItems.map((item) => (
            <InteractionRow
              key={item.id}
              item={item}
              expanded={expandedId === item.id}
              onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
              labels={labels}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function AIInteractionsPage() {
  const { lang } = useAppStore();
  const labels = uiText(lang);

  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-7xl px-4 py-6 text-sm text-tremor-content-subtle md:px-6">{labels.loading}</div>}>
      <AIInteractionsPageContent />
    </Suspense>
  );
}
