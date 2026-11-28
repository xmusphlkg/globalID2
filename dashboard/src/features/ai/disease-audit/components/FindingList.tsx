"use client";

import { StatusBadge } from "@/components/ui/StatusBadge";
import type { DiseaseAuditFinding } from "@/features/ai/api";

interface FindingListProps {
  title: string;
  items?: DiseaseAuditFinding[];
  empty: string;
}

export function FindingList({ title, items, empty }: FindingListProps) {
  return (
    <section className="app-panel p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {title}
        </h2>
        <StatusBadge tone={items?.length ? "warning" : "success"}>{items?.length ?? 0}</StatusBadge>
      </div>
      {!items?.length ? (
        <p className="mt-4 text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {empty}
        </p>
      ) : (
        <div className="mt-4 max-h-[420px] space-y-3 overflow-y-auto pr-1">
          {items.map((item, index) => (
            <div
              key={`${item.category}-${index}`}
              className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle/60 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/50"
            >
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge>{item.category}</StatusBadge>
                {item.candidate_ids?.map((id) => (
                  <StatusBadge key={id} tone="info">
                    {id}
                  </StatusBadge>
                ))}
                {item.country_code ? <StatusBadge tone="success">{item.country_code}</StatusBadge> : null}
                {item.row_count ? <StatusBadge tone="warning">{item.row_count} rows</StatusBadge> : null}
              </div>
              <p className="mt-2 text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {item.finding}
              </p>
              {item.raw_terms?.length ? (
                <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  Examples: {item.raw_terms.slice(0, 4).join(", ")}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
