"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Clock3, Layers, ListTodo } from "lucide-react";
import type { components } from "@/generated/api";
import { apiFetch } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";
import { taskScopeHref } from "@/features/operations/task-scope";
import { StatusBadge } from "@/shared/ui";

// The summary endpoint currently declares a dict envelope; retain its generated
// contract while narrowing the documented response used by this view.
type FailedStage = Pick<components["schemas"]["StageOut"], "stage" | "attempt" | "error"> & {
  task_uuid: string;
  task_name?: string | null;
  task_type?: string | null;
  task_status?: string | null;
  finished_at?: string | null;
};
type Summary = components["schemas"]["DataResponse_dict_"]["data"] & {
  failed_stages: FailedStage[];
  model_queue: { queued: number; leased: number };
  pending_publish: Record<string, number>;
  overdue_schedules: Pick<components["schemas"]["ScheduleOut"], "id" | "name">[];
};
export function CollectionSummary() {
  const { lang } = useAppStore();
  const query = useQuery({ queryKey: ["/collection/summary"], queryFn: () => apiFetch<Summary>("/collection/summary"), refetchInterval: 15000 });
  const data = query.data;
  const stageName = (stage: string) => stage === "collect" || stage === "organize" || stage === "store" ? t(lang, `cc_${stage}`) : stage;
  const stageLabel = (stage: string) => lang === "en" ? stage : stageName(stage);
  const when = (value?: string | null) => {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString(lang === "zh" ? "zh-CN" : "en-GB");
  };
  const stageHref = (stage: FailedStage) => stage.task_type ? taskScopeHref({ task_uuid: stage.task_uuid, task_type: stage.task_type }) : `/collection/runs/${encodeURIComponent(stage.task_uuid)}`;
  return <section className="space-y-4" aria-label={t(lang, "cc_summary")}>
    {query.isLoading && <div className="cc-panel animate-pulse" role="status">{t(lang, "cc_loading")}</div>}
    {query.error && <div className="cc-panel border-rose-300" role="alert"><p>{t(lang, "cc_load_failed")}</p><p className="cc-muted">{query.error.message}</p><button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{t(lang, "cc_retry_load")}</button></div>}
    {data && <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: t(lang, "cc_failed_stages"), value: data.failed_stages.length, href: "#failed-stages", icon: AlertTriangle, tone: data.failed_stages.length ? "text-rose-700" : "text-emerald-700", detail: t(lang, "cc_actions") },
          { label: t(lang, "cc_queued"), value: data.model_queue.queued, href: "/models/queue", icon: ListTodo, tone: "text-blue-700", detail: `${data.model_queue.leased} ${t(lang, "cc_leased")}` },
          { label: t(lang, "cc_unpublished"), value: Object.values(data.pending_publish).reduce((a,b) => a+b, 0), href: "/publish", icon: Layers, tone: "text-amber-800", detail: t(lang, "publish") },
          { label: t(lang, "cc_overdue"), value: data.overdue_schedules.length, href: "/collection/schedules", icon: Clock3, tone: "text-amber-800", detail: t(lang, "cc_schedules") },
        ].map(({label,value,href,icon:Icon,tone,detail}) => <Link key={label} href={href} className="cc-panel block hover:border-[var(--cc-brand)]"><div className="flex items-center justify-between gap-3"><span className="text-sm font-medium">{label}</span><Icon className={`h-4 w-4 ${tone}`} /></div><p className={`text-3xl font-semibold tabular-nums ${tone}`}>{value}</p><p className="cc-muted">{detail}</p></Link>)}
      </div>
      <div className="grid items-start gap-4 xl:grid-cols-[2fr_1fr]">
        <section id="failed-stages" className="cc-panel scroll-mt-24"><h2 className="text-sm font-semibold">{t(lang, "cc_failed_stages")}</h2>
          <p className="cc-muted mt-1 text-xs">{t(lang, "cc_failed_hint")}</p>
          {!data.failed_stages.length && <p className="cc-muted mt-3">{t(lang, "cc_no_failed")}</p>}
          <div className="divide-y divide-tremor-border">{data.failed_stages.map((stage,index) => <Link className="flex min-w-0 items-start gap-3 py-3 hover:text-[var(--cc-brand)]" href={stageHref(stage)} key={`${stage.task_uuid}-${stage.stage}-${index}`}><StatusBadge status="failed">{t(lang,"cc_failed")} {stageLabel(stage.stage)}</StatusBadge><span className="min-w-0 flex-1"><span className="block break-words text-sm font-medium">{stage.task_name || stage.task_uuid}</span><span className="cc-muted block text-xs">{stage.task_name ? <span className="mr-2 break-all font-mono">{stage.task_uuid}</span> : null}{t(lang,"cc_attempt")} {stage.attempt}{when(stage.finished_at) ? ` · ${when(stage.finished_at)}` : ""}</span>{stage.error && <span className="mt-1 line-clamp-3 block break-words text-sm text-rose-700">{stage.error}</span>}</span><span aria-hidden="true">→</span></Link>)}</div>
        </section>
        <section className="cc-panel"><h2 className="text-sm font-semibold">{t(lang,"cc_overdue")}</h2>{!data.overdue_schedules.length && <p className="cc-muted">{t(lang,"cc_no_overdue")}</p>}{data.overdue_schedules.map(s => <Link className="block break-words text-sm text-amber-800 underline" href="/collection/schedules" key={s.id}>{s.name}</Link>)}</section>
      </div>
    </>}
  </section>;
}
