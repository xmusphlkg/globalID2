"use client";

import { useMemo, useState } from "react";
import { BrainCircuit, FileSearch } from "lucide-react";

import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  useDiseaseDuplicateAuditLogs,
  useDiseaseDuplicateAuditStatus,
  useRunDiseaseDuplicateAudit,
} from "@/features/ai/api";
import { useAppStore } from "@/stores/app-store";
import { AlertPanel } from "./components/AlertPanel";
import { AuditLogPanel } from "./components/AuditLogPanel";
import { AuditResultsSection } from "./components/AuditResultsSection";
import {
  diseaseAuditCopy,
  formatDiseaseRunError,
  getActiveAuditRoutes,
  resolveAuditLogs,
} from "./helpers";

export default function DiseaseAuditPage() {
  const { lang } = useAppStore();
  const ui = diseaseAuditCopy[lang];
  const [includeNew, setIncludeNew] = useState(true);
  const [maxCandidates, setMaxCandidates] = useState(40);
  const { data: status, error: statusError } = useDiseaseDuplicateAuditStatus(includeNew);
  const { data: persistedLogs } = useDiseaseDuplicateAuditLogs(120);
  const runAudit = useRunDiseaseDuplicateAudit();
  const result = runAudit.data;

  const activeRoutes = useMemo(
    () => getActiveAuditRoutes(status?.model_center.routes),
    [status],
  );
  const auditLogs = resolveAuditLogs(result?.logs, persistedLogs);
  const runAuditError = runAudit.error ? formatDiseaseRunError(runAudit.error, ui) : null;
  const statusApiError = statusError ? formatDiseaseRunError(statusError, ui) : null;

  const run = (includeAI: boolean) => {
    runAudit.mutate({
      include_ai: includeAI,
      include_new_disease_candidates: includeNew,
      max_ai_candidates: maxCandidates,
    });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={ui.eyebrow}
        title={ui.title}
        description={ui.subtitle}
        meta={
          <>
            {status ? <StatusBadge tone="success">Audit API ready</StatusBadge> : null}
            {activeRoutes.length ? (
              activeRoutes.slice(0, 4).map((route) => (
                <StatusBadge key={route.model_key} tone="info">
                  {route.provider_key} / {route.model_name}
                </StatusBadge>
              ))
            ) : (
              <StatusBadge tone="danger">{ui.noRoutes}</StatusBadge>
            )}
            {status?.model_center.route_count ? (
              <StatusBadge>
                {ui.modelRoutes}: {status.model_center.active_route_count}/
                {status.model_center.route_count}
              </StatusBadge>
            ) : null}
          </>
        }
      />

      <FilterToolbar>
        <label className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
          <input
            type="checkbox"
            checked={includeNew}
            onChange={(event) => setIncludeNew(event.target.checked)}
            className="h-4 w-4 rounded border-tremor-border text-tremor-brand"
          />
          {ui.includeNew}
        </label>

        <label className="flex items-center gap-2 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          <span>{ui.maxCandidates}</span>
          <input
            type="number"
            min={1}
            max={100}
            value={maxCandidates}
            onChange={(event) => setMaxCandidates(Number(event.target.value) || 40)}
            className="h-10 w-24 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm outline-none focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background"
          />
        </label>

        <button
          type="button"
          className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:opacity-60 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
          onClick={() => run(false)}
          disabled={runAudit.isPending}
        >
          <FileSearch className="h-4 w-4" />
          {ui.runLocal}
        </button>
        <button
          type="button"
          className="inline-flex h-10 items-center gap-2 rounded-tremor-default bg-tremor-brand px-3 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 disabled:opacity-60 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
          onClick={() => run(true)}
          disabled={runAudit.isPending}
        >
          <BrainCircuit className="h-4 w-4" />
          {runAudit.isPending ? ui.loading : ui.runAI}
        </button>
        {!activeRoutes.length && status?.model_center.route_count ? (
          <span className="text-xs text-amber-700 dark:text-amber-300">
            {ui.degradedRouteHint}
          </span>
        ) : null}
      </FilterToolbar>

      {statusError ? (
        <AlertPanel
          title={ui.error}
          tone="warning"
          message={statusApiError?.message || ui.runErrorUnknown}
          details={statusApiError?.details}
        />
      ) : null}

      {runAudit.error ? (
        <AlertPanel
          title={ui.error}
          tone="danger"
          message={runAuditError?.message || ui.runErrorUnknown}
          details={runAuditError?.details}
        />
      ) : null}

      <AuditLogPanel title={ui.logs} logs={auditLogs} />

      <AuditResultsSection result={result} lang={lang} ui={ui} />
    </div>
  );
}
