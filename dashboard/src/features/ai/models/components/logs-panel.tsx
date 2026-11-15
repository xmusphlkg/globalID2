

import { History } from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type ModelCenterController } from "../hooks/use-model-center";
import { ActionButton, AlertBox, SkeletonRows, inputClass } from "./form-controls";
import { formatDateTime, queryErrorText } from "../presentation";

export function CallLogPanel({ controller }: { controller: ModelCenterController }) {
  const { lang, providers, activeView, callLogProvider, setCallLogProvider, callLogStatus, setCallLogStatus, callLogPageNumber, setCallLogPageNumber, callLogPage, loadingCallLogs, callLogsError, callLogs, callLogTotalPages, copy } = controller;

  return (<>
        {activeView === "logs" ? (
          <div id="model-center-logs-panel" role="tabpanel" className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{copy.callLogs}</h2>
              <StatusBadge tone="neutral">{callLogPage?.total ?? 0}</StatusBadge>
            </div>
            <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{copy.callLogPrivacy}</p>
            <div className="flex flex-wrap gap-2">
              <select
                aria-label={copy.callProvider}
                className={inputClass}
                value={callLogProvider}
                onChange={(event) => {
                  setCallLogProvider(event.target.value);
                  setCallLogPageNumber(1);
                }}
              >
                <option value="">{copy.callAllProviders}</option>
                {(providers ?? []).map((provider) => (
                  <option key={provider.provider_key} value={provider.provider_key}>
                    {provider.display_name} ({provider.provider_key})
                  </option>
                ))}
              </select>
              <select
                aria-label={copy.callStatus}
                className={inputClass}
                value={callLogStatus}
                onChange={(event) => {
                  setCallLogStatus(event.target.value);
                  setCallLogPageNumber(1);
                }}
              >
                <option value="">{copy.callAllResults}</option>
                <option value="success">{copy.callSuccess}</option>
                <option value="failed">{copy.callFailed}</option>
              </select>
            </div>
            {loadingCallLogs ? (
              <SkeletonRows />
            ) : callLogsError ? (
              <AlertBox tone="danger">{queryErrorText(callLogsError, lang)}</AlertBox>
            ) : callLogs?.length ? (
              <div className="overflow-x-auto rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
                <table className="min-w-full divide-y divide-tremor-border text-left text-sm dark:divide-dark-tremor-border">
                  <thead className="bg-tremor-background-subtle text-xs uppercase text-tremor-content-subtle dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                    <tr>
                      {[copy.callTime, copy.callStatus, copy.callProvider, copy.callModel, copy.callAgent, copy.callLatency, copy.callError].map((label) => (
                        <th key={label} className="px-3 py-2 font-medium">{label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
                    {callLogs.map((entry) => (
                      <tr key={entry.id} className="align-top text-tremor-content-strong dark:text-dark-tremor-content-strong">
                        <td className="whitespace-nowrap px-3 py-2 text-xs">{formatDateTime(entry.created_at)}</td>
                        <td className="px-3 py-2"><StatusBadge tone={entry.status === "success" ? "success" : "danger"}>{entry.status}</StatusBadge></td>
                        <td className="px-3 py-2 font-mono text-xs">{entry.provider_key}</td>
                        <td className="px-3 py-2"><span className="font-medium">{entry.model_name}</span><span className="ml-1 block font-mono text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{entry.model_key}</span></td>
                        <td className="px-3 py-2">{entry.agent_name}</td>
                        <td className="whitespace-nowrap px-3 py-2 tabular-nums">{Math.round(entry.latency_ms)} ms</td>
                        <td className="max-w-sm px-3 py-2 text-xs text-rose-700 dark:text-rose-300">{entry.error_message ? `${entry.error_type}: ${entry.error_message}` : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon={<History className="h-10 w-10" />} title={copy.noCallLogs} />
            )}
            {callLogPage ? (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {copy.callPageOf
                    .replace("{page}", String(callLogPageNumber))
                    .replace("{pages}", String(callLogTotalPages))
                    .replace("{total}", String(callLogPage.total))}
                </p>
                <div className="flex gap-2">
                  <ActionButton disabled={callLogPageNumber <= 1} onClick={() => setCallLogPageNumber((page) => Math.max(1, page - 1))}>
                    {copy.callPrev}
                  </ActionButton>
                  <ActionButton disabled={callLogPageNumber >= callLogTotalPages} onClick={() => setCallLogPageNumber((page) => Math.min(callLogTotalPages, page + 1))}>
                    {copy.callNext}
                  </ActionButton>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
  </>);
}
