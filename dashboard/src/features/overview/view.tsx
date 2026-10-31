"use client";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, ServerCog } from "lucide-react";
import { CollectionSummary } from "@/features/control-plane/summary";
import { useControlPlaneOverview } from "@/features/overview/api";
import { taskScopeHref } from "@/features/operations/task-scope";
import { StatusBadge, WorkspacePage } from "@/shared/ui";
import { t, type LangKey } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";

export default function ControlCenterOverviewView() {
  const query = useControlPlaneOverview();
  const client = useQueryClient();
  const { lang } = useAppStore();
  const data = query.data;
  function status(value: string) {
    const keys: Record<string, LangKey> = { queued:"cc_queued", leased:"cc_leased", running:"cc_running", completed:"cc_completed", failed:"cc_failed", retrying:"cc_retrying", cancelled:"cc_cancelled", pending:"cc_pending", skipped:"cc_skipped", succeeded:"cc_succeeded", healthy:"cc_healthy" };
    return keys[value] ? t(lang, keys[value]) : value;
  }
  return <WorkspacePage eyebrow={t(lang,"cc_center")} title={t(lang,"overview")} description={t(lang,"cc_overview_desc")} actions={<button className="cc-button" disabled={query.isFetching} onClick={() => { void query.refetch(); void client.invalidateQueries({ queryKey: ["/collection/summary"] }); }}><RefreshCw className="h-4 w-4" />{t(lang,"cc_refresh")}</button>}>
    <CollectionSummary />
    {query.isLoading && <div role="status" className="cc-panel animate-pulse">{t(lang,"cc_loading")}</div>}
    {query.isError && <div className="cc-panel border-rose-300" role="alert"><p>{t(lang,"cc_load_failed")}</p><p className="cc-muted">{query.error.message}</p><button className="cc-button" onClick={() => query.refetch()}>{t(lang,"cc_retry_load")}</button></div>}
    {data && <>
      <div className="grid items-start gap-4 xl:grid-cols-[2fr_1fr]">
        <section className="cc-panel"><h2 className="text-sm font-semibold">{t(lang,"cc_actions")}</h2>{!data.action_items.length && <p className="cc-muted">{t(lang,"cc_no_actions")}</p>}<div className="divide-y divide-tremor-border">{data.action_items.map(item => <Link href={item.href} key={item.id} className="block py-3"><p className="break-words text-sm font-medium">{item.title}</p><p className="cc-muted break-words">{item.detail}</p></Link>)}</div></section>
        <section className="cc-panel"><h2 className="text-sm font-semibold">{t(lang,"cc_services")}</h2>{data.runtime.services.map(service => <div key={service.instance_id} className="flex items-center gap-2"><ServerCog className="h-4 w-4 shrink-0 text-[var(--cc-brand)]" /><span className="min-w-0 flex-1"><span className="text-sm">{["api","worker","scheduler"].includes(service.service) ? t(lang, `cc_${service.service}` as LangKey) : service.service}</span><span className="cc-muted block truncate">{service.host} · PID {service.pid}</span></span><StatusBadge status={service.status} tone={service.status === "healthy" ? "success" : "warning"}>{status(service.status)}</StatusBadge></div>)}{!data.runtime.services.length && <p className="cc-muted">{t(lang,"cc_no_services")}</p>}<Link className="cc-button w-full" href="/collection/runtime">{t(lang,"cc_runtime_details")}</Link></section>
      </div>
      <section className="app-panel overflow-hidden"><div className="data-toolbar"><h2 className="text-sm font-semibold">{t(lang,"cc_recent")}</h2><Link className="text-sm font-medium text-[var(--cc-brand)]" href="/collection/runs">{t(lang,"cc_view_all")} →</Link></div><div className="overflow-x-auto"><table className="cc-table"><thead><tr><th>{t(lang,"cc_task")}</th><th>{t(lang,"cc_status")}</th><th className="hidden sm:table-cell">{t(lang,"cc_progress")}</th><th className="hidden lg:table-cell">{t(lang,"cc_created")}</th></tr></thead><tbody>{data.recent_tasks.map(task => <tr key={task.task_uuid}><td><Link className="block max-w-64 truncate font-medium text-[var(--cc-link)]" href={taskScopeHref({task_uuid:task.task_uuid,task_type:task.type})}>{task.name}</Link><span className="cc-muted block max-w-48 truncate font-mono">{task.task_uuid}</span></td><td><StatusBadge status={task.status}>{status(task.status)}</StatusBadge></td><td className="hidden sm:table-cell">{task.progress}%</td><td className="hidden lg:table-cell">{new Date(task.created_at).toLocaleString(lang === "zh" ? "zh-CN" : "en-US")}</td></tr>)}</tbody></table>{!data.recent_tasks.length && <p className="cc-muted p-5">{t(lang,"cc_no_runs")}</p>}</div></section>
    </>}
  </WorkspacePage>;
}
