"use client";

import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { Activity, AlertTriangle, ArrowRight, CalendarClock, CircleCheck, RefreshCw, ServerCog } from "lucide-react";

import { ActionList, MetricStrip, Skeleton, StatusBadge, WorkspacePage } from "@/shared/ui";
import { useControlPlaneOverview } from "@/features/overview/api";

function titleCase(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function ControlCenterOverviewView() {
  const query = useControlPlaneOverview();
  const data = query.data;
  const activeTasks = (data?.tasks.running ?? 0) + (data?.tasks.retrying ?? 0) + (data?.tasks.queued ?? 0);

  return (
    <WorkspacePage
      eyebrow="Control Center"
      title="Overview"
      description="Runtime health, operational blockers, and the current path from ingestion to publishing."
      actions={
        <button type="button" onClick={() => query.refetch()} disabled={query.isFetching} className="inline-flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-3 text-sm font-medium text-[#374151] hover:bg-[#F7F7F5] disabled:opacity-60">
          <RefreshCw className={`h-4 w-4 ${query.isFetching ? "animate-spin" : ""}`} /> Refresh
        </button>
      }
    >
      {query.isLoading ? (
        <div className="space-y-5"><Skeleton className="h-28 w-full" /><div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]"><Skeleton className="h-96" /><Skeleton className="h-96" /></div></div>
      ) : query.isError || !data ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
          <p className="font-semibold">The control-plane summary is unavailable.</p>
          <p className="mt-1">{query.error instanceof Error ? query.error.message : "Check API and database readiness."}</p>
        </div>
      ) : (
        <>
          <MetricStrip items={[
            { label: "Active tasks", value: activeTasks, detail: `${data.tasks.queued ?? 0} queued`, icon: Activity, tone: activeTasks ? "warning" : "success" },
            { label: "Failed tasks", value: data.tasks.failed ?? 0, detail: "Last 14 days", icon: AlertTriangle, tone: data.tasks.failed ? "danger" : "success" },
            { label: "Enabled schedules", value: data.schedules.enabled ?? 0, detail: `${data.schedules.total ?? 0} configured`, icon: CalendarClock, tone: "neutral" },
            { label: "Action items", value: data.action_items.length, detail: data.action_items.length ? "Review required" : "No blockers", icon: CircleCheck, tone: data.action_items.length ? "warning" : "success" },
          ]} />

          <section className="app-panel overflow-hidden">
            <div className="data-toolbar">
              <div><h2 className="text-sm font-semibold text-[#1D1D1F]">Operational pipeline</h2><p className="mt-0.5 text-xs text-[#6B7280]">A compact status view across the control-plane workflow.</p></div>
              <span className="text-xs text-[#6B7280]">Updated {formatDistanceToNow(new Date(data.generated_at), { addSuffix: true })}</span>
            </div>
            <div className="grid divide-y divide-[#E5E5E2] sm:grid-cols-4 sm:divide-x sm:divide-y-0">
              {data.pipeline.map((stage, index) => (
                <div key={stage.id} className="relative px-4 py-4">
                  <div className="flex items-center gap-2"><span className="flex h-6 w-6 items-center justify-center rounded-full border border-[#D9D9D6] bg-[#F7F7F5] text-[11px] font-semibold text-[#6B7280]">{index + 1}</span><p className="text-sm font-semibold text-[#1D1D1F]">{stage.label}</p></div>
                  <div className="mt-3"><StatusBadge status={stage.status} tone={stage.status === "healthy" ? "success" : stage.status === "attention" ? "warning" : "neutral"}>{titleCase(stage.status)}</StatusBadge></div>
                </div>
              ))}
            </div>
          </section>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
            <section className="app-panel overflow-hidden">
              <div className="data-toolbar"><div><h2 className="text-sm font-semibold text-[#1D1D1F]">Action items</h2><p className="mt-0.5 text-xs text-[#6B7280]">Failures and unavailable services requiring attention.</p></div></div>
              <ActionList items={data.action_items} />
            </section>

            <aside className="app-panel overflow-hidden">
              <div className="data-toolbar"><div><h2 className="text-sm font-semibold text-[#1D1D1F]">Runtime services</h2><p className="mt-0.5 text-xs text-[#6B7280]">Redis TTL heartbeats, not local PID files.</p></div></div>
              <div className="divide-y divide-[#E5E5E2]">
                {data.runtime.services.map((service) => (
                  <div key={service.instance_id} className="flex items-center gap-3 px-4 py-3.5">
                    <span className="flex h-8 w-8 items-center justify-center rounded-md bg-[#F7F7F5] text-[#C2410C]"><ServerCog className="h-4 w-4" /></span>
                    <span className="min-w-0 flex-1"><span className="block text-sm font-semibold text-[#1D1D1F]">{titleCase(service.service)}</span><span className="block truncate text-xs text-[#6B7280]">{service.host} · PID {service.pid}</span></span>
                    <StatusBadge status={service.status} tone="success">Healthy</StatusBadge>
                  </div>
                ))}
                {!data.runtime.services.length ? <div className="px-4 py-8 text-center text-sm text-[#6B7280]">No live heartbeats found.</div> : null}
              </div>
              <Link href="/operations/runtime" className="flex items-center justify-between border-t border-[#E5E5E2] px-4 py-3 text-sm font-semibold text-[#C2410C] hover:bg-[#FFF7ED]">Open runtime details <ArrowRight className="h-4 w-4" /></Link>
            </aside>
          </div>

          <section className="app-panel overflow-hidden">
            <div className="data-toolbar"><div><h2 className="text-sm font-semibold text-[#1D1D1F]">Recent task runs</h2><p className="mt-0.5 text-xs text-[#6B7280]">Latest work accepted by the background queue.</p></div><Link href="/operations/tasks" className="inline-flex items-center gap-1 text-sm font-semibold text-[#C2410C]">View all <ArrowRight className="h-4 w-4" /></Link></div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-[#E5E5E2] bg-[#FAFAF9] text-[11px] uppercase tracking-wide text-[#6B7280]"><tr><th className="px-4 py-2.5 font-semibold">Task</th><th className="hidden px-4 py-2.5 font-semibold sm:table-cell">Type</th><th className="px-4 py-2.5 font-semibold">Status</th><th className="hidden px-4 py-2.5 font-semibold md:table-cell">Progress</th><th className="hidden px-4 py-2.5 font-semibold lg:table-cell">Created</th></tr></thead>
                <tbody className="divide-y divide-[#E5E5E2]">
                  {data.recent_tasks.map((task) => (
                    <tr key={task.task_uuid} className="hover:bg-[#F7F7F5]"><td className="min-w-0 max-w-sm px-4 py-3"><Link href={`/operations/tasks?task=${task.task_uuid}`} className="block max-w-[14rem] truncate font-medium text-[#1D1D1F] hover:text-[#C2410C] sm:max-w-xs">{task.name}</Link><span className="block max-w-[14rem] truncate font-mono text-[11px] text-[#6B7280] sm:max-w-xs">{task.task_uuid}</span></td><td className="hidden px-4 py-3 text-[#4B5563] sm:table-cell">{titleCase(task.type)}</td><td className="px-4 py-3"><StatusBadge status={task.status}>{titleCase(task.status)}</StatusBadge></td><td className="hidden px-4 py-3 md:table-cell"><div className="flex items-center gap-2"><div className="h-1.5 w-24 overflow-hidden rounded-full bg-[#E5E5E2]"><div className="h-full rounded-full bg-[#F48120]" style={{ width: `${task.progress}%` }} /></div><span className="text-xs text-[#6B7280]">{task.progress}%</span></div></td><td className="hidden px-4 py-3 text-xs text-[#6B7280] lg:table-cell">{formatDistanceToNow(new Date(task.created_at), { addSuffix: true })}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </WorkspacePage>
  );
}
