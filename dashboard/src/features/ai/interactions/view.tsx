"use client";

import { Suspense, useMemo } from "react";
import Link from "next/link";
import { useAppStore } from "@/stores/app-store";
import { Badge, Card, Grid, Text, Title } from "@/components/ui/tremor";
import { MessageSquare, MessageSquareText, Search, ListTodo, Settings2 } from "lucide-react";
import { Chart } from "@/components/charts/Chart";
import { TaskDetailPanel } from "@/components/tasks/TaskDetailPanel";
import { TaskWorkflowTopology } from "@/components/tasks/TaskWorkflowTopology";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge as UiStatusBadge } from "@/components/ui/StatusBadge";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";
import { uiText } from "./messages";
import { queryErrorText, shortUuid, metadataString, stageLabel } from "./presentation";
import { InteractionRow } from "./components/interaction-row";
import { GroupedRunChat } from "./components/grouped-run-chat";
import { groupInteractionThreads } from "./threads";
import { useInteractionContext } from "./hooks/use-interaction-context";

function AIInteractionsPageContent() {
  const { countryCode, countryName, lang, labels, agentFilter, setAgentFilter, modelFilter, setModelFilter, taskUuidFilter, setTaskUuidFilter, reportUuidFilter, setReportUuidFilter, diseaseFilter, setDiseaseFilter, expandedId, setExpandedId, activeTaskUuid, activeReportUuid, hasTaskScope, taskDetail, taskDetailLoading, recentKnowledgeTaskPage, interactions, isLoading, isError, error, summary } = useInteractionContext();

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

  const runThreads = useMemo(() => groupInteractionThreads(interactions), [interactions]);

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
