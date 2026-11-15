

import { t } from "@/lib/i18n";
import { type StageInfo } from "@/features/operations/sources/api";

import { formatDate } from "@/lib/utils";

import { Badge, ProgressBar } from "@/components/ui/tremor";
import type { Color } from "@/components/ui/tremor";

import { GitBranch, Download, Cog, CheckCircle2, Circle, AlertCircle, Loader2 } from "lucide-react";

// ── Colour mapping ──────────────────────────────────────────────────────────
const statusBadge: Record<string, Color> = {
  pending: "slate",
  queued: "blue",
  running: "yellow",
  completed: "emerald",
  skipped: "gray",
  failed: "rose",
  cancelled: "slate",
  retrying: "yellow",
};

function StageIcon({ stage, status }: { stage: string; status: string | null }) {
  const cls = "h-5 w-5 shrink-0";
  if (status === "running") return <Loader2 className={`${cls} animate-spin text-amber-500`} />;
  if (status === "completed") return <CheckCircle2 className={`${cls} text-emerald-500`} />;
  if (status === "skipped") return <Circle className={`${cls} text-slate-400`} />;
  if (status === "failed") return <AlertCircle className={`${cls} text-rose-500`} />;
  // stage default icon
  if (stage === "fetch_list") return <Download className={`${cls} text-blue-400`} />;
  if (stage === "incremental_check") return <GitBranch className={`${cls} text-violet-400`} />;
  if (stage === "process_store") return <Cog className={`${cls} text-teal-400`} />;
  if (stage === "finalize") return <Circle className={`${cls} text-orange-400`} />;
  return <Circle className={`${cls} text-tremor-content-subtle`} />;
}

const STAGE_LABEL_KEYS: Record<string, string> = {
  fetch_list: "flow_stage_fetch_list",
  incremental_check: "flow_stage_incremental_check",
  process_store: "flow_stage_process_store",
  finalize: "flow_stage_finalize",
};

// ── Stage pill ───────────────────────────────────────────────────────────────
export function StagePill({
  stage,
  lang,
}: {
  stage: StageInfo;
  lang: "en" | "zh";
}) {
  const labelKey = STAGE_LABEL_KEYS[stage.stage] ?? stage.stage;
  const label = t(lang, labelKey as Parameters<typeof t>[1]);
  const isDone = stage.status === "completed";
  const isRun = stage.status === "running";
  const isFail = stage.status === "failed";

  return (
    <div
      className={`flex flex-col items-center justify-center gap-1.5 rounded-tremor-default border p-2.5 text-center transition w-[100px] shrink-0 min-h-[96px]
        ${isDone ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30" : ""}
        ${isRun ? "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30" : ""}
        ${isFail ? "border-rose-200 bg-rose-50 dark:border-rose-800 dark:bg-rose-950/30" : ""}
        ${!stage.status ? "border-tremor-border bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle opacity-60" : ""}
      `}
    >
      <StageIcon stage={stage.stage} status={stage.status} />
      <span className="text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis leading-tight">
        {label}
      </span>
      {stage.status ? (
        <Badge color={statusBadge[stage.status] ?? "slate"} className="px-1.5 py-0 text-[10px]">
          {stage.status}
        </Badge>
      ) : (
        <span className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          —
        </span>
      )}
      {stage.status === "running" && (
        <ProgressBar value={stage.progress} color="amber" className="h-1.5 w-full mt-auto" />
      )}
      {stage.last_run && stage.status !== "running" && (
        <span className="text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle truncate max-w-full">
          {formatDate(stage.last_run).split(" ")[0]}
        </span>
      )}
    </div>
  );
}
