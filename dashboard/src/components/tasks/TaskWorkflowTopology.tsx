"use client";

import { useMemo } from "react";
import { Card, Text, Title } from "@tremor/react";
import { Chart, echarts } from "@/components/charts/Chart";
import { CHART_TOKENS } from "@/lib/chart-theme";
import type { TaskDetail, WorkbookEntry } from "@/lib/hooks/useTasks";

type TopologyLabels = {
  title: string;
  subtitle: string;
  empty: string;
};

function metadataString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function entryColor(entry: WorkbookEntry): string {
  if (entry.success === false || entry.entry_type === "error") return CHART_TOKENS.destructive;
  if (entry.entry_type === "success") return CHART_TOKENS.success;
  if (entry.entry_type === "warning") return CHART_TOKENS.warning;
  return CHART_TOKENS.info;
}

function stageTitle(stageKey: string, fallback: string): string {
  const normalized = stageKey.trim().toLowerCase();
  if (normalized === "knowledge_start") return "Start";
  if (normalized === "source_fetch_completed") return "Fetch Sources";
  if (normalized === "source_reuse") return "Reuse Sources";
  if (normalized === "brief_generation_en") return "Generate EN Brief";
  if (normalized === "brief_generation_zh") return "Generate ZH Brief";
  if (normalized === "brief_persisted") return "Persist Briefs";
  if (normalized === "knowledge_complete") return "Completed";
  return fallback;
}

function summarizeEntry(entry: WorkbookEntry): string {
  const provider = metadataString(entry.metadata?.provider);
  const model = entry.model_used ? `${provider ? `${provider}/` : ""}${entry.model_used}` : provider;
  const parts = [
    stageTitle(metadataString(entry.metadata?.workflow_stage) || entry.title, entry.title),
    model ? `Model: ${model}` : null,
    entry.tokens_used != null ? `Tokens: ${entry.tokens_used}` : null,
    entry.duration != null ? `Duration: ${entry.duration.toFixed(1)}s` : null,
  ].filter(Boolean);
  return parts.join("\n");
}

function buildKnowledgeTopology(entries: WorkbookEntry[]): { nodes: any[]; edges: any[] } | null {
  const stageOrder = [
    "knowledge_start",
    "source_fetch_completed",
    "source_reuse",
    "brief_generation_en",
    "brief_generation_zh",
    "brief_persisted",
    "knowledge_complete",
  ];

  const stageMap = new Map<string, WorkbookEntry>();
  for (const entry of entries) {
    const stageKey = metadataString(entry.metadata?.workflow_stage);
    if (!stageKey) continue;
    stageMap.set(stageKey, entry);
  }

  if (stageMap.size === 0) return null;

  const sourceKey = stageMap.has("source_fetch_completed") ? "source_fetch_completed" : "source_reuse";
  const positions: Record<string, { x: number; y: number }> = {
    knowledge_start: { x: 80, y: 160 },
    source_fetch_completed: { x: 280, y: 160 },
    source_reuse: { x: 280, y: 160 },
    brief_generation_en: { x: 540, y: 80 },
    brief_generation_zh: { x: 540, y: 240 },
    brief_persisted: { x: 800, y: 160 },
    knowledge_complete: { x: 1040, y: 160 },
  };

  const nodes = stageOrder
    .filter((stageKey) => stageMap.has(stageKey))
    .map((stageKey) => {
      const entry = stageMap.get(stageKey)!;
      const provider = metadataString(entry.metadata?.provider);
      const modelLabel = entry.model_used ? `${provider ? `${provider}/` : ""}${entry.model_used}` : provider;
      return {
        id: stageKey,
        name: stageTitle(stageKey, entry.title),
        value: summarizeEntry(entry),
        x: positions[stageKey]?.x ?? 120,
        y: positions[stageKey]?.y ?? 160,
        symbol: "roundRect",
        symbolSize: [170, 70],
        itemStyle: {
          color: entryColor(entry),
          borderColor: "#ffffff",
          borderWidth: 2,
          shadowBlur: 10,
          shadowColor: "rgba(15, 23, 42, 0.10)",
        },
        label: {
          show: true,
          color: "#ffffff",
          fontWeight: 600,
          fontSize: 11,
          lineHeight: 16,
          formatter: `${stageTitle(stageKey, entry.title)}${modelLabel ? `\n${modelLabel}` : ""}`,
        },
        tooltipPayload: {
          title: entry.title,
          stage: stageKey,
          createdAt: formatDateTime(entry.created_at),
          model: entry.model_used || "-",
          provider: provider || "-",
          tokens: entry.tokens_used ?? "-",
          duration: entry.duration != null ? `${entry.duration.toFixed(1)}s` : "-",
          content: entry.content || "-",
        },
      };
    });

  const edges: Array<{ source: string; target: string; lineStyle?: Record<string, unknown> }> = [];
  if (stageMap.has("knowledge_start") && stageMap.has(sourceKey)) {
    edges.push({ source: "knowledge_start", target: sourceKey });
  }
  if (stageMap.has(sourceKey) && stageMap.has("brief_generation_en")) {
    edges.push({ source: sourceKey, target: "brief_generation_en" });
  }
  if (stageMap.has(sourceKey) && stageMap.has("brief_generation_zh")) {
    edges.push({ source: sourceKey, target: "brief_generation_zh" });
  }
  if (stageMap.has("brief_generation_en") && stageMap.has("brief_persisted")) {
    edges.push({ source: "brief_generation_en", target: "brief_persisted" });
  }
  if (stageMap.has("brief_generation_zh") && stageMap.has("brief_persisted")) {
    edges.push({ source: "brief_generation_zh", target: "brief_persisted" });
  }
  if (stageMap.has("brief_persisted") && stageMap.has("knowledge_complete")) {
    edges.push({ source: "brief_persisted", target: "knowledge_complete" });
  }

  if (edges.length === 0) {
    const orderedStages = stageOrder.filter((stageKey) => stageMap.has(stageKey));
    for (let index = 0; index < orderedStages.length - 1; index += 1) {
      edges.push({ source: orderedStages[index], target: orderedStages[index + 1] });
    }
  }

  return { nodes, edges };
}

function buildGenericTopology(entries: WorkbookEntry[]): { nodes: any[]; edges: any[] } | null {
  const filtered = entries.filter((entry) => entry.model_used || metadataString(entry.metadata?.workflow_stage) || entry.entry_type !== "info").slice(0, 8);
  if (filtered.length === 0) return null;

  const nodes = filtered.map((entry, index) => {
    const provider = metadataString(entry.metadata?.provider);
    const modelLabel = entry.model_used ? `${provider ? `${provider}/` : ""}${entry.model_used}` : provider;
    return {
      id: `entry-${entry.id}`,
      name: entry.title,
      value: summarizeEntry(entry),
      x: 100 + index * 180,
      y: 160,
      symbol: "roundRect",
      symbolSize: [150, 64],
      itemStyle: {
        color: entryColor(entry),
        borderColor: "#ffffff",
        borderWidth: 2,
      },
      label: {
        show: true,
        color: "#ffffff",
        fontWeight: 600,
        fontSize: 11,
        lineHeight: 16,
        formatter: `${entry.title}${modelLabel ? `\n${modelLabel}` : ""}`,
      },
      tooltipPayload: {
        title: entry.title,
        stage: metadataString(entry.metadata?.workflow_stage) || "-",
        createdAt: formatDateTime(entry.created_at),
        model: entry.model_used || "-",
        provider: provider || "-",
        tokens: entry.tokens_used ?? "-",
        duration: entry.duration != null ? `${entry.duration.toFixed(1)}s` : "-",
        content: entry.content || "-",
      },
    };
  });

  const edges = nodes.slice(0, -1).map((node, index) => ({
    source: node.id,
    target: nodes[index + 1].id,
  }));

  return { nodes, edges };
}

export function TaskWorkflowTopology({
  taskDetail,
  labels,
}: {
  taskDetail?: TaskDetail;
  labels: TopologyLabels;
}) {
  const topology = useMemo(() => {
    if (!taskDetail) return null;
    const entries = [...taskDetail.workbook_entries].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
    if (taskDetail.task_type === "update_disease_knowledge") {
      return buildKnowledgeTopology(entries);
    }
    return buildGenericTopology(entries);
  }, [taskDetail]);

  if (!taskDetail || !topology || topology.nodes.length === 0) {
    return (
      <Card className="border-dashed border-tremor-border/80 bg-white/70 dark:border-dark-tremor-border/80 dark:bg-white/5">
        <Title className="text-base">{labels.title}</Title>
        <Text className="mt-2 text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {labels.empty}
        </Text>
      </Card>
    );
  }

  const option: echarts.EChartsCoreOption = {
    tooltip: {
      trigger: "item",
      formatter: (params: any) => {
        const payload = params?.data?.tooltipPayload;
        if (!payload) return params?.data?.name ?? "";
        return [
          `<strong>${payload.title}</strong>`,
          `Stage: ${payload.stage}`,
          `Model: ${payload.model}`,
          `Provider: ${payload.provider}`,
          `Tokens: ${payload.tokens}`,
          `Duration: ${payload.duration}`,
          `Time: ${payload.createdAt}`,
          `<div style="margin-top:6px;max-width:360px;white-space:normal;">${String(payload.content).replace(/\n/g, "<br/>")}</div>`,
        ].join("<br/>");
      },
    },
    animationDuration: 400,
    animationDurationUpdate: 200,
    xAxis: { show: false, min: 0, max: 1140 },
    yAxis: { show: false, min: 0, max: 320 },
    series: [
      {
        type: "graph",
        layout: "none",
        coordinateSystem: undefined,
        roam: true,
        data: topology.nodes,
        links: topology.edges,
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: 8,
        lineStyle: {
          color: CHART_TOKENS.gridLine,
          width: 2,
          curveness: 0.05,
        },
        emphasis: {
          focus: "adjacency",
        },
      },
    ],
  };

  return (
    <Card className="border-tremor-border/80 bg-white/85 dark:border-dark-tremor-border/80 dark:bg-white/5">
      <Title className="text-base">{labels.title}</Title>
      <Text className="mt-1 text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {labels.subtitle}
      </Text>
      <Chart option={option} height={340} className="mt-4" />
    </Card>
  );
}
