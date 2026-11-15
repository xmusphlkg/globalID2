

import { CheckCircle2, Cpu } from "lucide-react";

import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type ModelCenterController } from "../hooks/use-model-center";
import { ActionButton } from "./form-controls";
import { statusTone } from "../presentation";

export function DiscoveryPanel({ controller }: { controller: ModelCenterController }) {
  const { models, discoveryResult, setDiscoveryResult, discoverySelection, setDiscoverySelection, discoveryImportResults, isImportingDiscoveredModels, copy, configuredDiscoveredModels, selectableDiscoveredModels, onAddDiscoveredModels } = controller;

  return (<>
      <DetailDrawer
        open={discoveryResult !== null}
        title={copy.discoveredModels}
        subtitle={discoveryResult ? discoveryResult.provider.display_name : undefined}
        onClose={() => setDiscoveryResult(null)}
      >
        {discoveryResult ? (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge tone={statusTone(discoveryResult.result.status)}>{discoveryResult.result.status}</StatusBadge>
              <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{discoveryResult.result.models.length} {copy.models}</span>
            </div>
            <p className="text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{discoveryResult.result.message}</p>
            {discoveryResult.result.base_url ? <p className="break-all font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{discoveryResult.result.base_url}</p> : null}
            {discoveryResult.result.models.length ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3 border-y border-tremor-border py-3 dark:border-dark-tremor-border">
                  <label className="flex items-center gap-2 text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    <input
                      type="checkbox"
                      checked={selectableDiscoveredModels.length > 0 && discoverySelection.length === selectableDiscoveredModels.length}
                      disabled={isImportingDiscoveredModels || selectableDiscoveredModels.length === 0}
                      onChange={(event) => setDiscoverySelection(event.target.checked ? selectableDiscoveredModels : [])}
                    />
                    {copy.selectAll}
                  </label>
                  <ActionButton
                    tone="primary"
                    disabled={isImportingDiscoveredModels || discoverySelection.length === 0}
                    onClick={() => void onAddDiscoveredModels()}
                    icon={<CheckCircle2 className="h-4 w-4" />}
                  >
                    {isImportingDiscoveredModels ? copy.importing : `${copy.addAndTest} (${discoverySelection.length})`}
                  </ActionButton>
                </div>
                <ul className="divide-y divide-tremor-border border-b border-tremor-border dark:divide-dark-tremor-border dark:border-dark-tremor-border">
                  {discoveryResult.result.models.map((model) => {
                    const isConfigured = configuredDiscoveredModels.has(model);
                    const importResult = discoveryImportResults[model];
                    return (
                      <li key={model} className="flex min-h-11 items-center gap-3 px-3 py-2">
                        <input
                          type="checkbox"
                          checked={isConfigured || discoverySelection.includes(model)}
                          disabled={isConfigured || isImportingDiscoveredModels || Boolean(importResult)}
                          onChange={(event) => setDiscoverySelection((current) => (
                            event.target.checked ? [...current, model] : current.filter((item) => item !== model)
                          ))}
                          aria-label={model}
                        />
                        <span className="min-w-0 flex-1 truncate font-mono text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{model}</span>
                        {isConfigured ? <StatusBadge tone="neutral">{copy.alreadyConfigured}</StatusBadge> : null}
                        {importResult ? (
                          <span className="flex max-w-[260px] items-center gap-2">
                            <StatusBadge tone={statusTone(importResult.status)}>{importResult.status}</StatusBadge>
                            {importResult.message ? <span className="truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle" title={importResult.message}>{importResult.message}</span> : null}
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </>
            ) : <EmptyState icon={<Cpu className="h-8 w-8" />} title={copy.noDiscoveredModels} />}
          </div>
        ) : null}
      </DetailDrawer>
  </>);
}
