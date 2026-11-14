
import { useMemo } from "react";
import { GitBranch } from "lucide-react";

import { type AIRuntimeRoute } from "@/features/ai/api";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type ModelCenterController } from "../hooks/use-model-center";
import { AlertBox, SkeletonRows } from "./form-controls";
import { formatDuration, queryErrorText, routingStatusTone, routingStatusLabel, routingStatusReason } from "../presentation";

export function RuntimeRoutePanel({ controller }: { controller: ModelCenterController }) {
  const { lang, isZh, providers, loadingRuntimeRoutes, runtimeRoutesError, activeView, copy, summary, filteredRuntimeRoutes } = controller;
  const routeColumns = useMemo<DataTableColumn<AIRuntimeRoute>[]>(
    () => [
      {
        key: "rank",
        header: "#",
        render: (route) => <span className="font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">P{route.priority ?? "-"}</span>,
      },
      {
        key: "route",
        header: copy.routes,
        render: (route) => (
          <div className="min-w-[250px] max-w-[430px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{route.model_name}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{route.model_key}</p>
          </div>
        ),
      },
      {
        key: "provider",
        header: copy.providers,
        render: (route) => <span className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">{route.provider_key}</span>,
      },
      {
        key: "ready",
        header: copy.ready,
        render: (route) => (
          <div className="flex flex-wrap gap-1.5">
            <StatusBadge tone={route.has_api_key ? "success" : "danger"}>{route.has_api_key ? "key ready" : copy.noCredential}</StatusBadge>
            <StatusBadge tone={routingStatusTone(route.routing_status)}>
              {routingStatusLabel(route.routing_status, isZh)}
            </StatusBadge>
          </div>
        ),
      },
      {
        key: "cooldown",
        header: "Cooldown",
        render: (route) => (
          <span className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">
            {route.rate_limit_active
              ? formatDuration(route.rate_limit_remaining_seconds, lang)
              : route.runtime_failure_active
                ? formatDuration(route.runtime_failure_remaining_seconds, lang)
                : route.runtime_degraded
                  ? (isZh ? "自动恢复探测" : "automatic recovery probe")
                : "-"}
          </span>
        ),
      },
      {
        key: "runtime-health",
        header: copy.runtimeHealth,
        render: (route) => (
          <div className="min-w-[150px] text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            <p className="max-w-[260px] truncate" title={routingStatusReason(route, isZh)}>{routingStatusReason(route, isZh)}</p>
            <p>{route.runtime_latency_ewma_ms == null ? "-" : `${Math.round(route.runtime_latency_ewma_ms)} ms EWMA`}</p>
            <p className="mt-1">{isZh ? "超时" : "timeouts"}: {route.runtime_timeout_count}</p>
            <p className="mt-1">
              {isZh ? "连续失败" : "failure streak"}: {route.runtime_failure_streak}
              {route.runtime_failure_streak_raw !== route.runtime_failure_streak ? `/${route.runtime_failure_streak_raw}` : ""}
              {` · ${isZh ? "累计" : "total"} ${route.runtime_failure_count}`}
            </p>
            <p className="mt-1">
              {isZh ? "模型占用名额" : "occupied model slots"}: {route.runtime_model_inflight}/{route.runtime_model_capacity}
              {" · "}{isZh ? "服务商合计" : "provider total"}: {route.runtime_provider_inflight}/{route.runtime_provider_capacity}
            </p>
            {route.runtime_last_error ? <p className="mt-1 max-w-[220px] truncate" title={route.runtime_last_error}>{route.runtime_last_error}</p> : null}
          </div>
        ),
      },
    ],
    [copy, isZh, lang],
  );

  return (<>
        {activeView === "runtime" ? (
          <div id="model-center-runtime-panel" role="tabpanel" className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.routes}</h2>
              <StatusBadge tone={summary.routeCoverage >= 80 ? "success" : summary.routeCoverage >= 40 ? "warning" : "danger"}>
                {summary.routeCoverage}%
              </StatusBadge>
            </div>
            {loadingRuntimeRoutes ? (
              <SkeletonRows />
            ) : runtimeRoutesError ? (
              <AlertBox tone="danger">{queryErrorText(runtimeRoutesError, lang)}</AlertBox>
            ) : (
              <>
                <div className="space-y-2 md:hidden">
                  {filteredRuntimeRoutes.length ? filteredRuntimeRoutes.map((route) => (
                    <article key={route.model_key} className="space-y-3 rounded-tremor-default border border-tremor-border bg-tremor-background p-3 shadow-[0_1px_2px_rgba(23,33,31,0.04)] dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{route.model_name}</p>
                          <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{route.provider_key}</p>
                        </div>
                        <span className="shrink-0 text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">P{route.priority ?? "-"}</span>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge tone={routingStatusTone(route.routing_status)}>{routingStatusLabel(route.routing_status, isZh)}</StatusBadge>
                        {route.runtime_failure_active ? <StatusBadge tone="warning">{formatDuration(route.runtime_failure_remaining_seconds, lang)}</StatusBadge> : null}
                        {route.rate_limit_active ? <StatusBadge tone="warning">{formatDuration(route.rate_limit_remaining_seconds, lang)}</StatusBadge> : null}
                      </div>
                      <div className="grid grid-cols-2 gap-2 border-t border-tremor-border pt-3 text-xs text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">
                        <span>{route.runtime_latency_ewma_ms == null ? "-" : `${Math.round(route.runtime_latency_ewma_ms)} ms`}</span>
                        <span className="text-right">{route.runtime_model_inflight}/{route.runtime_model_capacity} {isZh ? "模型占用名额" : "occupied model slots"}</span>
                      </div>
                    </article>
                  )) : <EmptyState icon={<GitBranch className="h-10 w-10" />} title={copy.noRoutes} />}
                </div>
                <div className="hidden md:block">
                  <DataTable
                    columns={routeColumns}
                    rows={filteredRuntimeRoutes}
                    getRowKey={(route) => route.model_key}
                    emptyState={<EmptyState icon={<GitBranch className="h-10 w-10" />} title={copy.noRoutes} />}
                  />
                </div>
              </>
            )}
          </div>
        ) : null}

  </>);
}
