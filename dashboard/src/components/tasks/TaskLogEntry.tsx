import { useState } from "react";
import { Badge, type Color } from "@/components/ui/tremor";
import type { WorkbookEntry } from "@/lib/hooks/useTasks";
import { getSourceDisplayLabel } from "@/lib/source-labels";
import { formatDateTime } from "@/lib/utils";
import { logFieldRows } from "./task-log-display";
import { metadataString } from "./task-metadata";

const entryBadge: Record<string, Color> = {
  info: "blue",
  success: "emerald",
  warning: "amber",
  error: "rose",
};

export const logCopy = {
  en: {
    attention: "Logs that need attention",
    timeline: "Task log",
    earlier: "Show earlier logs",
    hideEarlier: "Hide earlier logs",
    full: "Show full log",
    less: "Show less",
    empty: "No workflow entries recorded yet.",
    repeats: "repeated",
    stages: "Stages",
    taskError: "Task error",
    folded: "Verbose logs folded",
  },
  zh: {
    attention: "需要处理的日志",
    timeline: "任务日志",
    earlier: "显示更早的日志",
    hideEarlier: "收起更早的日志",
    full: "展开完整日志",
    less: "收起",
    empty: "还没有任务日志。",
    repeats: "重复",
    stages: "阶段",
    taskError: "任务错误",
    folded: "已折叠的重复输出",
  },
} as const;

export function LogBody({ content, expandLabel, collapseLabel }: { content: string; expandLabel: string; collapseLabel: string }) {
  const [open, setOpen] = useState(false);
  const lines = content.split("\n");
  const long = content.length > 1800 || lines.length > 30;
  const shown = !long || open ? content : lines.length > 30 ? lines.slice(0, 30).join("\n") : content.slice(0, 1800);
  return (
    <div className="min-w-0">
      <pre className="whitespace-pre-wrap break-all font-mono text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">{shown}</pre>
      {long ? (
        <button type="button" className="mt-1 text-xs font-medium text-orange-800 underline dark:text-orange-300" onClick={() => setOpen((current) => !current)}>
          {open ? collapseLabel : expandLabel}
        </button>
      ) : null}
    </div>
  );
}

export function TaskLogEntry({
  entry,
  repeats,
  countryCode,
  repeatsLabel,
  expandLabel,
  collapseLabel,
}: {
  entry: WorkbookEntry;
  repeats: number;
  countryCode?: string | null;
  repeatsLabel: string;
  expandLabel: string;
  collapseLabel: string;
}) {
  const content = entry.content?.trim() ? entry.content : null;
  const fields = logFieldRows(content).map((row) =>
    row.label.trim().toLowerCase() === "source"
      ? { ...row, value: getSourceDisplayLabel(row.value, "en", countryCode) }
      : row,
  );
  const metadata = entry.metadata || {};
  const metadataDisease = metadataString(metadata.disease_name);
  const metadataSection = metadataString(metadata.section_type);
  const metadataProvider = metadataString(metadata.provider);
  const attention = entry.entry_type === "error" || entry.success === false;
  return (
    <article className={`mb-2 min-w-0 rounded-md border px-3 py-2 last:mb-0 ${attention ? "border-rose-200 bg-rose-50/70 dark:border-rose-900/50 dark:bg-rose-950/20" : "border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background"}`}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        <span className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{entry.title}</span>
        <Badge color={entryBadge[entry.entry_type] ?? "slate"}>{entry.entry_type}</Badge>
        {repeats > 1 ? <Badge color="slate">{repeatsLabel} {repeats}</Badge> : null}
        {metadataDisease ? <Badge color="violet">{metadataDisease}</Badge> : null}
        {metadataSection ? <Badge color="blue">{metadataSection}</Badge> : null}
        {metadataProvider ? <Badge color="cyan">{metadataProvider}</Badge> : null}
        {entry.model_used ? <Badge color="blue">{entry.model_used}</Badge> : null}
        {entry.tokens_used != null ? <Badge color="slate">{entry.tokens_used} tokens</Badge> : null}
        {entry.duration != null ? <Badge color="slate">{entry.duration.toFixed(1)}s</Badge> : null}
        <span className="text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatDateTime(entry.created_at)}</span>
      </div>
      {fields.length > 0 ? (
        <dl className="mt-2 grid gap-2 sm:grid-cols-2">
          {fields.map((row, index) => (
            <div key={`${entry.id}-${index}`} className="min-w-0">
              <dt className="text-[10px] uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{row.label}</dt>
              <dd className="break-words text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : content ? (
        <div className="mt-2"><LogBody content={content} expandLabel={expandLabel} collapseLabel={collapseLabel} /></div>
      ) : null}
      {(entry.prompt || entry.response) && (
        <details className="mt-2 rounded-md border border-tremor-border px-2 py-1 dark:border-dark-tremor-border">
          <summary className="cursor-pointer text-[11px] font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">View AI payload</summary>
          {entry.prompt ? <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-xs">{entry.prompt}</pre> : null}
          {entry.response ? <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-xs">{entry.response}</pre> : null}
        </details>
      )}
    </article>
  );
}
