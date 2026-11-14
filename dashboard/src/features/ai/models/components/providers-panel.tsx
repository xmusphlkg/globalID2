
import { useMemo } from "react";
import { Pencil, Power, Search, ShieldCheck, Trash2 } from "lucide-react";

import { type AIProviderItem } from "@/features/ai/api";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type ModelCenterController } from "../hooks/use-model-center";
import { IconActionButton, LastCheckCell, SkeletonRows } from "./form-controls";
import { formatDateTime, statusTone } from "../presentation";

export function ProviderPanel({ controller }: { controller: ModelCenterController }) {
  const { isZh, providers, loadingProviders, models, updateProvider, deleteProvider, testProvider, discoverProviderModels, activeView, deletingProviderId, copy, modelCountByProvider, filteredProviders, openEditProvider, onDeleteProvider, onDiscoverProviderModels } = controller;
  const providerColumns = useMemo<DataTableColumn<AIProviderItem>[]>(
    () => [
      {
        key: "status",
        header: isZh ? "状态" : "Status",
        render: (provider) => (
          <div className="flex max-w-[148px] flex-wrap gap-1">
            <StatusBadge tone={provider.is_active ? "success" : "neutral"}>{provider.is_active ? "active" : "inactive"}</StatusBadge>
            <StatusBadge tone={statusTone(provider.last_check_status)}>{provider.last_check_status || "unknown"}</StatusBadge>
            {provider.runtime_failure_active ? <StatusBadge tone="warning">{copy.cooldown}</StatusBadge> : null}
          </div>
        ),
      },
      {
        key: "provider",
        header: copy.providers,
        render: (provider) => (
          <div className="min-w-[230px] max-w-[380px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{provider.display_name}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{provider.provider_key}</p>
          </div>
        ),
      },
      {
        key: "endpoint",
        header: "Endpoint",
        render: (provider) => (
          <div className="min-w-[220px] max-w-[420px]">
            <p className="truncate text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{provider.api_style}</p>
            <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{provider.base_url || "-"}</p>
          </div>
        ),
      },
      {
        key: "credential",
        header: copy.credential,
        className: "w-[118px]",
        headerClassName: "w-[118px]",
        render: (provider) => (
          <div className="w-[104px]">
            <StatusBadge className="w-full" tone={provider.has_api_key ? "success" : "danger"}>
              {provider.api_key_hint || copy.noCredential}
            </StatusBadge>
            <p className="mt-1 whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {modelCountByProvider.get(provider.id) ?? 0} {copy.models}
            </p>
          </div>
        ),
      },
      {
        key: "catalogue",
        header: copy.modelCatalogue,
        render: (provider) => {
          const discovery = provider.extra_config?.model_discovery;
          const metadata = discovery && typeof discovery === "object" ? discovery as Record<string, unknown> : null;
          const count = typeof metadata?.count === "number" ? metadata.count : null;
          const checkedAt = typeof metadata?.checked_at === "string" ? metadata.checked_at : null;
          const status = typeof metadata?.status === "string" ? metadata.status : "";
          return (
            <div className="min-w-[150px]">
              <StatusBadge tone={statusTone(status)}>{status || copy.catalogueNotRead}</StatusBadge>
              <p className="mt-1 whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {count == null ? "-" : `${count} ${copy.models}`}
              </p>
              {checkedAt ? <p className="mt-1 whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatDateTime(checkedAt)}</p> : null}
            </div>
          );
        },
      },
      {
        key: "last-check",
        header: copy.lastCheck,
        render: (provider) => (
          <LastCheckCell checkedAt={provider.last_checked_at} message={provider.last_check_message} />
        ),
      },
      {
        key: "priority",
        header: copy.priority,
        render: (provider) => <span className="text-sm text-tremor-content dark:text-dark-tremor-content">P{provider.priority}</span>,
      },
      {
        key: "actions",
        header: "",
        className: "w-[216px] text-right",
        headerClassName: "w-[216px]",
        render: (provider) => (
          <div className="flex justify-end gap-1">
            <IconActionButton
              disabled={discoverProviderModels.isPending}
              onClick={() => onDiscoverProviderModels(provider)}
              icon={<Search className="h-4 w-4" />}
              label={copy.discoverModels}
            />
            <IconActionButton
              disabled={testProvider.isPending}
              onClick={() => testProvider.mutate(provider.provider_key)}
              icon={<ShieldCheck className="h-4 w-4" />}
              label={copy.test}
            />
            <IconActionButton
              onClick={() => openEditProvider(provider)}
              icon={<Pencil className="h-4 w-4" />}
              label={copy.edit}
            />
            <IconActionButton
              onClick={() => updateProvider.mutate({ providerKey: provider.provider_key, payload: { is_active: !provider.is_active } })}
              icon={<Power className="h-4 w-4" />}
              label={provider.is_active ? copy.disable : copy.enable}
            />
            <IconActionButton
              tone="danger"
              disabled={deleteProvider.isPending && deletingProviderId === provider.provider_key}
              onClick={() => onDeleteProvider(provider)}
              icon={<Trash2 className="h-4 w-4" />}
              label={copy.delete}
            />
          </div>
        ),
      },
    ],
    [copy, deleteProvider.isPending, deletingProviderId, discoverProviderModels.isPending, isZh, modelCountByProvider, testProvider.isPending, updateProvider],
  );

  return (<>
        {activeView === "providers" ? (
          <div id="model-center-providers-panel" role="tabpanel" className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.providers}</h2>
              <StatusBadge tone="neutral">{filteredProviders.length}</StatusBadge>
            </div>
            {loadingProviders ? (
              <SkeletonRows />
            ) : (
              <>
                <div className="space-y-2 md:hidden">
                  {filteredProviders.length ? filteredProviders.map((provider) => (
                    <article key={provider.id} className="space-y-3 rounded-tremor-default border border-tremor-border bg-tremor-background p-3 shadow-[0_1px_2px_rgba(23,33,31,0.04)] dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{provider.display_name}</p>
                          <p className="mt-1 truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{provider.provider_key}</p>
                        </div>
                        <span className="shrink-0 text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">P{provider.priority}</span>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge tone={provider.is_active ? "success" : "neutral"}>{provider.is_active ? "active" : "inactive"}</StatusBadge>
                        <StatusBadge tone={statusTone(provider.last_check_status)}>{provider.last_check_status || "unknown"}</StatusBadge>
                        <StatusBadge tone={provider.has_api_key ? "success" : "danger"}>{provider.api_key_hint || copy.noCredential}</StatusBadge>
                        {provider.runtime_failure_active ? <StatusBadge tone="warning">{copy.cooldown}</StatusBadge> : null}
                      </div>
                      <p className="truncate font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{provider.base_url || "-"}</p>
                      <div className="flex justify-end gap-1 border-t border-tremor-border pt-3 dark:border-dark-tremor-border">
                        <IconActionButton disabled={discoverProviderModels.isPending} onClick={() => onDiscoverProviderModels(provider)} icon={<Search className="h-4 w-4" />} label={copy.discoverModels} />
                        <IconActionButton disabled={testProvider.isPending} onClick={() => testProvider.mutate(provider.provider_key)} icon={<ShieldCheck className="h-4 w-4" />} label={copy.test} />
                        <IconActionButton onClick={() => openEditProvider(provider)} icon={<Pencil className="h-4 w-4" />} label={copy.edit} />
                        <IconActionButton onClick={() => updateProvider.mutate({ providerKey: provider.provider_key, payload: { is_active: !provider.is_active } })} icon={<Power className="h-4 w-4" />} label={provider.is_active ? copy.disable : copy.enable} />
                        <IconActionButton tone="danger" disabled={deleteProvider.isPending && deletingProviderId === provider.provider_key} onClick={() => onDeleteProvider(provider)} icon={<Trash2 className="h-4 w-4" />} label={copy.delete} />
                      </div>
                    </article>
                  )) : <EmptyState icon={<ShieldCheck className="h-10 w-10" />} title={copy.noProviders} />}
                </div>
                <div className="hidden md:block">
                  <DataTable
                    columns={providerColumns}
                    rows={filteredProviders}
                    getRowKey={(provider) => provider.id}
                    emptyState={<EmptyState icon={<ShieldCheck className="h-10 w-10" />} title={copy.noProviders} />}
                  />
                </div>
              </>
            )}
          </div>
        ) : null}

  </>);
}
