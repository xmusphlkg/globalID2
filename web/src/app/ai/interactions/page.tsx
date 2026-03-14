"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useAppStore } from "@/stores/app-store";
import { Badge, Card, Grid, Metric, Text, Title } from "@tremor/react";
import { MessageSquare, Search, ChevronDown } from "lucide-react";
import { Chart } from "@/components/charts/Chart";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { ApiError } from "@/lib/api";
import {
  useAIInteractions,
  useAIInteractionSummary,
  type AIInteractionItem,
} from "@/lib/hooks/useAIInteractions";

function queryErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    return `Request failed (${error.status}). ${error.message || "Please check backend API route and server status."}`;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Failed to load AI interaction data.";
}

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
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
}: {
  item: AIInteractionItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <Card className="overflow-hidden p-0">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle"
      >
        <Badge color="slate">{item.agent ?? "unknown"}</Badge>
        <span className="flex-1 truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {item.report_title}
        </span>
        <Badge color="indigo">{shortUuid(item.report_uuid)}</Badge>
        <Badge color="blue">{item.model ?? "-"}</Badge>
        <span className="w-24 text-right text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {item.total_tokens} tokens
        </span>
        <span className="w-16 text-right text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          Q {qualityText(item.quality_overall)}
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
              <Text>Timestamp</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {formatDateTime(item.timestamp)}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Report UUID</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.report_uuid}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Run Status</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_status ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Run UUID</Text>
              <Text className="mt-1 break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_uuid ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Duration</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.duration != null ? `${item.duration.toFixed(2)}s` : "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Section</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.section_type ?? "-"} {item.section_title ? `(${item.section_title})` : ""}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Run Model</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_provider ?? "-"} / {item.run_model ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Prompt Temp</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.temperature != null ? item.temperature.toFixed(2) : "-"}
              </Text>
            </Card>
          </Grid>

          <Grid numItems={1} numItemsLg={2} className="gap-3">
            <Card className="p-3">
              <Text className="mb-2">System Prompt</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.system_prompt || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">Prompt</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.prompt || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">Response</Text>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {item.response || "-"}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">Tokens</Text>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                {jsonString(item.tokens)}
              </pre>
            </Card>
            <Card className="p-3">
              <Text className="mb-2">Quality Scores</Text>
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

function GroupedRunChat({ thread }: { thread: RunThread }) {
  return (
    <Card className="space-y-4 p-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge color="indigo">Report {shortUuid(thread.reportUuid)}</Badge>
          <Badge color="slate">Run {shortUuid(thread.runUuid)}</Badge>
          <Badge color="blue">{thread.sectionType ?? "unknown section"}</Badge>
          <Badge color="emerald">{thread.runStatus ?? "-"}</Badge>
        </div>
        <Text className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {thread.reportTitle}
        </Text>
        <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {thread.sectionTitle ?? "-"} | Disease: {thread.diseaseName ?? "-"} | Model: {thread.runProvider ?? "-"}/{thread.runModel ?? "-"} | Q {qualityText(thread.qualityOverall)} | Tokens {thread.totalTokens}
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
              <div className={`w-full max-w-4xl rounded-xl border p-3 ${alignRight ? "border-blue-200 bg-blue-50/70 dark:border-blue-900/70 dark:bg-blue-950/20" : "border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-slate-900/20"}`}>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge color="slate">{item.agent ?? "unknown"}</Badge>
                  <Badge color="blue">{item.role ?? "-"}</Badge>
                  <Badge color="indigo">{item.model ?? "-"}</Badge>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {formatDateTime(item.timestamp)}
                  </Text>
                </div>

                <details className="mb-2 rounded-md border border-tremor-border/70 px-2 py-1 dark:border-dark-tremor-border/70">
                  <summary className="cursor-pointer text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    system prompt
                  </summary>
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-tremor-content dark:text-dark-tremor-content">
                    {item.system_prompt || "-"}
                  </pre>
                </details>

                <Text className="mb-1 text-xs font-medium">Prompt</Text>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-white/80 p-2 text-xs text-tremor-content dark:bg-black/20 dark:text-dark-tremor-content">
                  {item.prompt || "-"}
                </pre>

                <Text className="mb-1 mt-2 text-xs font-medium">Response</Text>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-white/80 p-2 text-xs text-tremor-content dark:bg-black/20 dark:text-dark-tremor-content">
                  {item.response || "-"}
                </pre>

                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    tokens: {item.total_tokens}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    duration: {item.duration != null ? `${item.duration.toFixed(2)}s` : "-"}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    temp: {item.temperature != null ? item.temperature.toFixed(2) : "-"}
                  </Text>
                  <Text className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    quality: {qualityText(item.quality_overall)}
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
  const { countryId, countryName } = useAppStore();
  const searchParamsString = searchParams.toString();
  const initialUuid = (searchParams.get("uuid") ?? searchParams.get("report_uuid") ?? "").trim();

  const [agentFilter, setAgentFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [reportUuidFilter, setReportUuidFilter] = useState(initialUuid);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(searchParamsString);
    const nextUuid = (params.get("uuid") ?? params.get("report_uuid") ?? "").trim();
    if (nextUuid !== reportUuidFilter) {
      setReportUuidFilter(nextUuid);
    }
  }, [searchParamsString]);

  useEffect(() => {
    const params = new URLSearchParams(searchParamsString);
    const currentUuid = (params.get("uuid") ?? "").trim();
    const nextUuid = reportUuidFilter.trim();

    if (nextUuid === currentUuid && !params.get("report_uuid")) {
      return;
    }

    if (nextUuid) {
      params.set("uuid", nextUuid);
    } else {
      params.delete("uuid");
    }
    params.delete("report_uuid");

    const nextQuery = params.toString();
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
  }, [pathname, reportUuidFilter, router, searchParamsString]);

  const filters = {
    countryId,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    reportUuid: reportUuidFilter.trim() || undefined,
    limit: 200,
  };

  const { data: interactions, isLoading, isError, error } = useAIInteractions(filters);
  const { data: summary } = useAIInteractionSummary({
    countryId,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    reportUuid: reportUuidFilter.trim() || undefined,
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

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="violet" className="w-fit">AI Programs</Badge>
        <Title className="text-2xl">AI Interactions</Title>
        <Text>Inspect prompts, responses, token usage, durations and quality scoring. Support direct query with ?uuid=...</Text>
      </div>

      <Grid numItems={1} numItemsSm={2} numItemsLg={5} className="gap-4">
        <Card>
          <Text>Interactions</Text>
          <Metric>{summary?.total_interactions ?? 0}</Metric>
        </Card>
        <Card>
          <Text>Total Tokens</Text>
          <Metric>{summary?.total_tokens ?? 0}</Metric>
        </Card>
        <Card>
          <Text>Avg Tokens</Text>
          <Metric>{Math.round(summary?.avg_tokens ?? 0)}</Metric>
        </Card>
        <Card>
          <Text>Avg Duration</Text>
          <Metric>{(summary?.avg_duration ?? 0).toFixed(2)}s</Metric>
        </Card>
        <Card>
          <Text>Avg Quality</Text>
          <Metric>{summary?.avg_quality != null ? summary.avg_quality.toFixed(2) : "-"}</Metric>
        </Card>
      </Grid>

      {(byAgentData.length > 0 || byModelData.length > 0) && (
        <Grid numItems={1} numItemsLg={2} className="gap-4">
          <Card>
            <Title className="mb-2">By Agent</Title>
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
            <Title className="mb-2">By Model</Title>
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

      <Card>
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <input
              type="text"
              value={reportUuidFilter}
              onChange={(e) => setReportUuidFilter(e.target.value)}
              placeholder="Filter by report uuid (or use ?uuid=...)"
              className="w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            />
          </div>

          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          >
            <option value="">All agents</option>
            {agentOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>

          <select
            value={modelFilter}
            onChange={(e) => setModelFilter(e.target.value)}
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          >
            <option value="">All models</option>
            {modelOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>
      </Card>

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
            <Text className="mt-3 text-red-600">Unable to load interaction data</Text>
            <Text className="mt-2 max-w-3xl text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {queryErrorText(error)}
            </Text>
          </div>
        </Card>
      ) : !interactions || interactions.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <MessageSquare className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Text className="mt-3">No interaction data found</Text>
            <Text className="mt-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              Current country filter: {countryName || "All"}
            </Text>
          </div>
        </Card>
      ) : reportUuidFilter.trim() ? (
        <div className="space-y-4">
          <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            Chat workflow mode enabled for report UUID: {reportUuidFilter.trim()}
          </Text>
          {runThreads.map((thread) => (
            <GroupedRunChat key={thread.runId} thread={thread} />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {interactions.map((item) => (
            <InteractionRow
              key={item.id}
              item={item}
              expanded={expandedId === item.id}
              onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function AIInteractionsPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-7xl px-4 py-6 text-sm text-tremor-content-subtle md:px-6">Loading interactions...</div>}>
      <AIInteractionsPageContent />
    </Suspense>
  );
}
