"use client";

import Link from "next/link";

import { AlertTriangle, ArrowRight, Cpu, GitBranch, History, KeyRound, ListTodo, MessageSquareText, Plus, Search, ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";

import { t } from "@/lib/i18n";

import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";

import { useModelCenter } from "./hooks/use-model-center";
import type { HealthFilter } from "./filters";
import { ActionButton, AlertBox, inputClass } from "./components/form-controls";
import { mutationErrorText } from "./presentation";
import { ProviderPanel } from "./components/providers-panel";
import { ModelPanel } from "./components/models-panel";
import { RuntimeRoutePanel } from "./components/runtime-panel";
import { CallLogPanel } from "./components/logs-panel";
import { ProviderForm } from "./components/provider-form";
import { ModelForm } from "./components/model-form";
import { DiscoveryPanel } from "./components/discovery-panel";

export default function AIModelsPage() {
  const controller = useModelCenter();
  const { lang, isZh, providers, models, rebuildProviders, checkAll, search, setSearch, healthFilter, setHealthFilter, activeView, setActiveView, callLogs, copy, summary, filteredProviders, filteredModels, filteredRuntimeRoutes, operationError, openCreateProvider, openCreateModel, onRebuildFromEnv } = controller;
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "settings")}
        title={t(lang, "ai_models")}
        description={copy.subtitle}
        meta={
          <>
            <StatusBadge tone={summary.configuredKeys === summary.totalProviders && summary.totalProviders > 0 ? "success" : "warning"}>
              {summary.configuredKeys}/{summary.totalProviders} {isZh ? "密钥" : "keys"}
            </StatusBadge>
            <StatusBadge tone={summary.routeCoverage >= 80 ? "success" : summary.routeCoverage >= 40 ? "warning" : "danger"}>
              {summary.routeCoverage}% {copy.routeCoverage}
            </StatusBadge>
          </>
        }
        actions={
          <>
            <Link
              href="/models"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              <ListTodo className="h-4 w-4" />
              {copy.openTasks}
            </Link>
            <Link
              href="/production/interactions"
              className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            >
              <MessageSquareText className="h-4 w-4" />
              {copy.openInteractions}
            </Link>
            <ActionButton disabled={checkAll.isPending} onClick={() => checkAll.mutate()} icon={<ShieldCheck className="h-4 w-4" />}>
              {copy.checkAllModels}
            </ActionButton>
            <ActionButton disabled={rebuildProviders.isPending} onClick={onRebuildFromEnv} icon={<ArrowRight className="h-4 w-4" />}>
              {copy.rebuildFromEnv}
            </ActionButton>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label={copy.providers} value={summary.totalProviders} icon={<ShieldCheck className="h-4 w-4" />} tone="neutral" hint={`${summary.activeProviders} ${copy.activeProviders}`} />
        <MetricTile label={copy.enabledModels} value={summary.enabledModels} icon={<Cpu className="h-4 w-4" />} tone="primary" hint={`${summary.defaultModels} ${copy.defaults}`} />
        <MetricTile label={copy.keys} value={summary.configuredKeys} icon={<KeyRound className="h-4 w-4" />} tone={summary.configuredKeys > 0 ? "success" : "danger"} hint={`${summary.totalProviders} ${copy.providers}`} />
        <MetricTile label={copy.routes} value={summary.totalRoutes} icon={<GitBranch className="h-4 w-4" />} tone="info" hint={`${summary.runtimeReady} ready`} />
      </div>

      {operationError ? (
        <AlertBox tone="danger">
          <div className="flex gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{mutationErrorText(operationError, lang)}</span>
          </div>
        </AlertBox>
      ) : null}

      <FilterToolbar>
        <div className="relative min-w-[240px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={copy.searchPlaceholder}
            className={cn(inputClass, "pl-9")}
          />
        </div>
        <select
          className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          value={healthFilter}
          onChange={(event) => setHealthFilter(event.target.value as HealthFilter)}
          aria-label={isZh ? "健康状态筛选" : "Health filter"}
        >
          <option value="all">{copy.all}</option>
          <option value="healthy">{copy.healthy}</option>
          <option value="attention">{copy.attention}</option>
          <option value="checking">{copy.checking}</option>
        </select>
        {activeView === "providers" ? (
          <ActionButton tone="primary" onClick={openCreateProvider} icon={<Plus className="h-4 w-4" />}>{copy.newProvider}</ActionButton>
        ) : null}
        {activeView === "models" ? (
          <ActionButton tone="primary" onClick={openCreateModel} icon={<Plus className="h-4 w-4" />}>{copy.newModel}</ActionButton>
        ) : null}
      </FilterToolbar>

      <section className="space-y-3" aria-label={isZh ? "模型中心资源" : "Model Center resources"}>
        <div
          role="tablist"
          aria-label={isZh ? "模型中心视图" : "Model Center views"}
          className="flex w-full overflow-x-auto rounded-tremor-default border border-tremor-border bg-tremor-background p-1 dark:border-dark-tremor-border dark:bg-dark-tremor-background"
        >
          {[
            { id: "providers" as const, label: copy.providers, count: filteredProviders.length, icon: <ShieldCheck className="h-4 w-4" /> },
            { id: "models" as const, label: copy.models, count: filteredModels.length, icon: <Cpu className="h-4 w-4" /> },
            { id: "runtime" as const, label: copy.runtime, count: filteredRuntimeRoutes.length, icon: <GitBranch className="h-4 w-4" /> },
            { id: "logs" as const, label: copy.callLogs, count: callLogs?.length ?? 0, icon: <History className="h-4 w-4" /> },
          ].map((view) => {
            const selected = activeView === view.id;
            return (
              <button
                key={view.id}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`model-center-${view.id}-panel`}
                onClick={() => setActiveView(view.id)}
                className={cn(
                  "inline-flex h-9 min-w-[132px] flex-1 items-center justify-center gap-2 rounded px-3 text-sm font-medium transition sm:flex-none",
                  selected
                    ? "bg-tremor-brand text-tremor-brand-inverted shadow-sm"
                    : "text-tremor-content hover:bg-tremor-background-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle dark:hover:text-dark-tremor-content-strong",
                )}
              >
                {view.icon}
                <span>{view.label}</span>
                <span className={cn("rounded px-1.5 py-0.5 text-xs tabular-nums", selected ? "bg-white/20" : "bg-tremor-background-muted text-tremor-content-subtle dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-subtle")}>{view.count}</span>
              </button>
            );
          })}
        </div>

        <ProviderPanel controller={controller} />

        <ModelPanel controller={controller} />

        <RuntimeRoutePanel controller={controller} />

        <CallLogPanel controller={controller} />

      </section>

      <ProviderForm controller={controller} />

      <ModelForm controller={controller} />

      <DiscoveryPanel controller={controller} />
    </div>
  );
}
