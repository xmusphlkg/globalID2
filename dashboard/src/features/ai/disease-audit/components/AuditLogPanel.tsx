"use client";

import { StatusBadge } from "@/components/ui/StatusBadge";
import type { DiseaseAuditLogEntry } from "@/features/ai/api";
import { formatMetadata, getVisibleAuditLogs, logTone } from "../helpers";

interface AuditLogPanelProps {
  title: string;
  logs: DiseaseAuditLogEntry[];
}

export function AuditLogPanel({ title, logs }: AuditLogPanelProps) {
  if (!logs.length) return null;
  const visibleLogs = getVisibleAuditLogs(logs, 80);

  return (
    <section className="app-panel p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {title}
        </h2>
        <StatusBadge>{logs.length}</StatusBadge>
      </div>
      <div className="mt-4 max-h-[360px] space-y-2 overflow-y-auto pr-1">
        {visibleLogs.map((entry, index) => {
          const metadata = formatMetadata(entry.metadata);
          return (
            <div
              key={`${entry.run_id}-${entry.timestamp}-${entry.event}-${index}`}
              className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle"
            >
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge tone={logTone(entry.level)}>{entry.level}</StatusBadge>
                <StatusBadge>{entry.event}</StatusBadge>
                <span className="font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {new Date(entry.timestamp).toLocaleString()}
                </span>
                <span className="font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {entry.run_id.slice(0, 8)}
                </span>
              </div>
              <p className="mt-2 text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {entry.message}
              </p>
              {metadata ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    metadata
                  </summary>
                  <pre className="mt-2 max-h-44 overflow-auto rounded-tremor-default bg-tremor-background px-3 py-2 text-xs text-tremor-content dark:bg-dark-tremor-background dark:text-dark-tremor-content">
                    {metadata}
                  </pre>
                </details>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
