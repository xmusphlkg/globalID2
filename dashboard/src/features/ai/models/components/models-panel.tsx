
import { useMemo } from "react";
import { Cpu, Pencil, Power, ShieldCheck, Star, Trash2 } from "lucide-react";

import { type AIModelItem } from "@/features/ai/api";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type ModelCenterController } from "../hooks/use-model-center";
import { ActionButton, IconActionButton, LastCheckCell, SkeletonRows } from "./form-controls";
import { statusTone, routingStatusTone, routingStatusLabel } from "../presentation";

export function ModelPanel({ controller }: { controller: ModelCenterController }) {
  const { isZh, providers, models, loadingModels, updateModel, testModel, deleteModel, activeView, deletingModelId, copy, runtimeRouteByModelKey, filteredModels, openEditModel, onDeleteModel } = controller;
  const modelColumns = useMemo<DataTableColumn<AIModelItem>[]>(
    () => [
      {
        key: "status",
        header: isZh ? "状态" : "Status",
        className: "w-[144px]",
        headerClassName: "w-[144px]",
        render: (model) => {
          const route = runtimeRouteByModelKey.get(model.model_key);
          return (
            <div className="flex w-[144px] flex-wrap gap-1">
              <StatusBadge tone={model.is_enabled ? "success" : "neutral"}>{model.is_enabled ? "enabled" : "disabled"}</StatusBadge>
              {model.is_default ? <StatusBadge tone="primary">default</StatusBadge> : null}
              <StatusBadge tone={statusTone(model.last_check_status)}>
                {isZh ? `探针 ${model.last_check_status || "unknown"}` : `probe ${model.last_check_status || "unknown"}`}
              </StatusBadge>
              {route ? (
                <StatusBadge tone={routingStatusTone(route.routing_status)}>
                  {routingStatusLabel(route.routing_status, isZh)}
                </StatusBadge>
              ) : null}
            </div>
          );
        },
      },
      {
        key: "model",
        header: copy.models,
        render: (model) => (
          <div className="min-w-[190px] max-w-[320px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{model.display_name}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{model.model_key}</p>
          </div>
        ),
      },
      {
        key: "provider",
        header: copy.providers,
        render: (model) => (
          <div className="min-w-[120px]">
            <p className="truncate text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{model.provider_key}</p>
            <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{model.provider_name}</p>
          </div>
        ),
      },
      {
        key: "params",
        header: copy.params,
        className: "hidden 2xl:table-cell",
        headerClassName: "hidden 2xl:table-cell",
        render: (model) => (
          <div className="min-w-[140px] text-sm text-tremor-content dark:text-dark-tremor-content">
            <p>{model.model_type || "chat"}</p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              temp {model.temperature ?? "-"} / {model.availability_windows?.length
                ? `${model.availability_windows.map((window) => `${window.start}–${window.end}`).join(", ")} ${model.availability_timezone || ""}`
                : copy.alwaysAvailable}
            </p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {copy.modelConcurrency}: {model.runtime_max_concurrency ?? 1}
            </p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {copy.thinkingMode}: {copy.thinkingLabels[model.thinking_mode ?? "default"]}
            </p>
          </div>
        ),
      },
      {
        key: "concurrency",
        header: copy.modelConcurrency,
        render: (model) => (
          <ActionButton onClick={() => openEditModel(model)} className="h-8 whitespace-nowrap px-2" title={copy.setConcurrency}>
            {model.runtime_max_concurrency ?? 1} · {copy.setConcurrency}
          </ActionButton>
        ),
      },
      {
        key: "last-check",
        header: copy.lastCheck,
        render: (model) => (
          <LastCheckCell compact checkedAt={model.last_checked_at} message={model.last_check_message} />
        ),
      },
      {
        key: "priority",
        header: copy.priority,
        render: (model) => <span className="text-sm text-tremor-content dark:text-dark-tremor-content">P{model.priority}</span>,
      },
      {
        key: "actions",
        header: "",
        className: "sticky right-0 z-10 w-[200px] bg-tremor-background text-right shadow-[-8px_0_12px_-12px_rgba(23,33,31,0.32)] dark:bg-dark-tremor-background",
        headerClassName: "sticky right-0 z-20 w-[200px] bg-tremor-background-subtle/95 text-right dark:bg-dark-tremor-background-subtle/95",
        render: (model) => (
          <div className="flex justify-end gap-1">
            <IconActionButton
              disabled={testModel.isPending}
              onClick={() => testModel.mutate(model.model_key)}
              icon={<ShieldCheck className="h-4 w-4" />}
              label={copy.test}
            />
            <IconActionButton
              onClick={() => openEditModel(model)}
              icon={<Pencil className="h-4 w-4" />}
              label={copy.edit}
            />
            <IconActionButton
              onClick={() => updateModel.mutate({ modelKey: model.model_key, payload: { is_enabled: !model.is_enabled } })}
              icon={<Power className="h-4 w-4" />}
              label={model.is_enabled ? copy.disable : copy.enable}
            />
            {!model.is_default ? (
              <IconActionButton
                onClick={() => updateModel.mutate({ modelKey: model.model_key, payload: { is_default: true } })}
                icon={<Star className="h-4 w-4" />}
                label={copy.setDefault}
              />
            ) : null}
            <IconActionButton
              tone="danger"
              disabled={deleteModel.isPending && deletingModelId === model.model_key}
              onClick={() => onDeleteModel(model)}
              icon={<Trash2 className="h-4 w-4" />}
              label={copy.delete}
            />
          </div>
        ),
      },
    ],
    [copy, deleteModel.isPending, deletingModelId, isZh, runtimeRouteByModelKey, testModel.isPending, updateModel],
  );

  return (<>
        {activeView === "models" ? (
          <div id="model-center-models-panel" role="tabpanel" className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.models}</h2>
              <StatusBadge tone="neutral">{filteredModels.length}</StatusBadge>
            </div>
            {loadingModels ? (
              <SkeletonRows />
            ) : (
              <>
                <div className="space-y-2 md:hidden">
                  {filteredModels.length ? filteredModels.map((model) => {
                    const route = runtimeRouteByModelKey.get(model.model_key);
                    return (
                    <article key={model.id} className="space-y-3 rounded-tremor-default border border-tremor-border bg-tremor-background p-3 shadow-[0_1px_2px_rgba(23,33,31,0.04)] dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{model.display_name}</p>
                          <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{model.model_key}</p>
                        </div>
                        <span className="shrink-0 text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">P{model.priority}</span>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge tone={model.is_enabled ? "success" : "neutral"}>{model.is_enabled ? "enabled" : "disabled"}</StatusBadge>
                        {model.is_default ? <StatusBadge tone="primary">default</StatusBadge> : null}
                        <StatusBadge tone={statusTone(model.last_check_status)}>{isZh ? `探针 ${model.last_check_status || "unknown"}` : `probe ${model.last_check_status || "unknown"}`}</StatusBadge>
                        {route ? <StatusBadge tone={routingStatusTone(route.routing_status)}>{routingStatusLabel(route.routing_status, isZh)}</StatusBadge> : null}
                      </div>
                      <div className="flex items-center justify-between gap-3 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        <span className="truncate">{model.provider_key}</span>
                        <span className="shrink-0">{model.model_type || "chat"}</span>
                      </div>
                      <div className="flex justify-end gap-1 border-t border-tremor-border pt-3 dark:border-dark-tremor-border">
                        <IconActionButton disabled={testModel.isPending} onClick={() => testModel.mutate(model.model_key)} icon={<ShieldCheck className="h-4 w-4" />} label={copy.test} />
                        <IconActionButton onClick={() => openEditModel(model)} icon={<Pencil className="h-4 w-4" />} label={copy.edit} />
                        <IconActionButton onClick={() => updateModel.mutate({ modelKey: model.model_key, payload: { is_enabled: !model.is_enabled } })} icon={<Power className="h-4 w-4" />} label={model.is_enabled ? copy.disable : copy.enable} />
                        {!model.is_default ? <IconActionButton onClick={() => updateModel.mutate({ modelKey: model.model_key, payload: { is_default: true } })} icon={<Star className="h-4 w-4" />} label={copy.setDefault} /> : null}
                        <IconActionButton tone="danger" disabled={deleteModel.isPending && deletingModelId === model.model_key} onClick={() => onDeleteModel(model)} icon={<Trash2 className="h-4 w-4" />} label={copy.delete} />
                      </div>
                    </article>
                    );
                  }) : <EmptyState icon={<Cpu className="h-10 w-10" />} title={copy.noModels} />}
                </div>
                <div className="hidden md:block">
                  <DataTable
                    columns={modelColumns}
                    rows={filteredModels}
                    getRowKey={(model) => model.id}
                    emptyState={<EmptyState icon={<Cpu className="h-10 w-10" />} title={copy.noModels} />}
                  />
                </div>
              </>
            )}
          </div>
        ) : null}

  </>);
}
