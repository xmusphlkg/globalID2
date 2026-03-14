"use client";

import { useMemo, useState } from "react";
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
          <Grid numItems={1} numItemsLg={3} className="mb-3 gap-3">
            <Card className="p-3">
              <Text>Timestamp</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {formatDateTime(item.timestamp)}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Run Status</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.run_status ?? "-"}
              </Text>
            </Card>
            <Card className="p-3">
              <Text>Duration</Text>
              <Text className="mt-1 text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.duration != null ? `${item.duration.toFixed(2)}s` : "-"}
              </Text>
            </Card>
          </Grid>

          <Grid numItems={1} numItemsLg={2} className="gap-3">
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

export default function AIInteractionsPage() {
  const { countryId, countryName } = useAppStore();

  const [agentFilter, setAgentFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [reportUuidFilter, setReportUuidFilter] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const filters = {
    countryId,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    reportUuid: reportUuidFilter || undefined,
    limit: 200,
  };

  const { data: interactions, isLoading, isError, error } = useAIInteractions(filters);
  const { data: summary } = useAIInteractionSummary({
    countryId,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    reportUuid: reportUuidFilter || undefined,
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

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="violet" className="w-fit">AI Programs</Badge>
        <Title className="text-2xl">AI Interactions</Title>
        <Text>Inspect prompts, responses, token usage, durations and quality scoring.</Text>
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
              placeholder="Filter by report uuid"
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
