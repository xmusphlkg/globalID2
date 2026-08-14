"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Play, RefreshCw, Settings2 } from "lucide-react";

import type { components } from "@/generated/api";
import { controlPlaneClient } from "@/generated/client";
import { DataTable, EmptyState, FilterBar, MetricStrip, StatusBadge, WorkspacePage, type DataTableColumn } from "@/shared/ui";

type Schedule = components["schemas"]["ScheduleOut"];

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatTime(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export default function SchedulesView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const kind = searchParams.get("kind") || "all";
  const query = useQuery({
    queryKey: ["control-plane", "schedules", kind],
    queryFn: async () => {
      const { data, error } = await controlPlaneClient.GET("/api/v1/schedules", {
        params: { query: { kind: kind === "all" ? undefined : kind } },
      });
      if (error) throw new Error("detail" in error ? String(error.detail) : "Unable to load schedules.");
      return data.data;
    },
    refetchInterval: 15_000,
  });
  const trigger = useMutation({
    mutationFn: async (scheduleId: string) => {
      const { data, error } = await controlPlaneClient.POST("/api/v1/schedules/{schedule_id}/runs", {
        params: { path: { schedule_id: scheduleId } },
      });
      if (error) throw new Error("detail" in error ? String(error.detail) : "Unable to trigger schedule.");
      return data.data;
    },
    onSuccess: (task) => {
      queryClient.invalidateQueries({ queryKey: ["control-plane", "schedules"] });
      router.push(task.task_uuid ? `/operations/tasks?task=${task.task_uuid}` : "/operations/tasks");
    },
  });
  const rows = query.data ?? [];
  const enabled = rows.filter((item) => item.enabled).length;
  const failed = rows.filter((item) => item.last_status === "failed").length;
  const columns: DataTableColumn<Schedule>[] = [
    { key: "schedule", header: "Schedule", render: (row) => <div><p className="font-semibold text-[#1D1D1F]">{row.name}</p><p className="mt-0.5 font-mono text-[11px] text-[#6B7280]">{row.id}</p></div> },
    { key: "kind", header: "Kind", render: (row) => <span className="capitalize text-[#4B5563]">{row.kind}</span> },
    { key: "scope", header: "Scope", render: (row) => <span className="text-[#4B5563]">{row.country_code || "Global"}</span> },
    { key: "cadence", header: "Cadence", render: (row) => <span className="text-[#4B5563]">{row.interval_minutes ? `Every ${row.interval_minutes} min` : row.daily_time ? `${row.daily_time} ${row.timezone || ""}` : "Event driven"}</span> },
    { key: "next", header: "Next run", render: (row) => <span className="whitespace-nowrap text-xs text-[#6B7280]">{formatTime(row.next_run_at)}</span> },
    { key: "status", header: "Last status", render: (row) => <StatusBadge status={row.enabled ? row.last_status : "disabled"}>{row.enabled ? humanize(row.last_status) : "Disabled"}</StatusBadge> },
    { key: "action", header: "", className: "text-right", render: (row) => <button type="button" disabled={!row.enabled || trigger.isPending} onClick={() => trigger.mutate(row.id)} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[#D9D9D6] bg-white px-2.5 text-xs font-semibold text-[#374151] hover:border-[#FB923C] hover:text-[#C2410C] disabled:opacity-40"><Play className="h-3.5 w-3.5" />Run now</button> },
  ];

  const setKind = (value: string) => {
    const next = new URLSearchParams(searchParams.toString());
    if (value === "all") next.delete("kind"); else next.set("kind", value);
    router.replace(`/operations/schedules${next.size ? `?${next}` : ""}`);
  };

  return (
    <WorkspacePage eyebrow="Ingestion & Tasks" title="Schedules" description="Monitor and trigger ingestion, literature, and release automation from a single operational view." actions={<div className="flex gap-2"><Link href="/operations/schedules/ingestion" className="inline-flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-3 text-sm font-semibold text-[#374151] hover:bg-[#F7F7F5]"><Settings2 className="h-4 w-4" />Manage ingestion</Link><button type="button" onClick={() => query.refetch()} className="inline-flex h-9 items-center gap-2 rounded-md bg-[#C2410C] px-3 text-sm font-semibold text-white hover:bg-[#9A3412]"><RefreshCw className={`h-4 w-4 ${query.isFetching ? "animate-spin" : ""}`} />Refresh</button></div>}>
      <MetricStrip items={[
        { label: "Configured", value: rows.length, detail: "All schedule adapters", icon: CalendarClock },
        { label: "Enabled", value: enabled, detail: "Eligible for dispatch", icon: Play, tone: enabled ? "success" : "neutral" },
        { label: "Failed", value: failed, detail: "Latest run state", icon: RefreshCw, tone: failed ? "danger" : "success" },
      ]} />
      <FilterBar><span className="mr-1 text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Kind</span>{["all", "ingestion", "literature", "release"].map((value) => <button key={value} type="button" onClick={() => setKind(value)} className={`h-8 rounded-md px-3 text-sm font-medium capitalize ${kind === value ? "bg-[#FFF1E8] text-[#C2410C]" : "text-[#4B5563] hover:bg-[#F7F7F5]"}`}>{value}</button>)}</FilterBar>
      {trigger.isError ? <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{trigger.error.message}</div> : null}
      {query.isError ? <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{query.error.message}</div> : null}
      <DataTable columns={columns} rows={rows} getRowKey={(row) => row.id} emptyState={<EmptyState title={query.isLoading ? "Loading schedules…" : "No schedules found"} description="Adjust the kind filter or configure an ingestion or release job." className="min-h-48" />} />
    </WorkspacePage>
  );
}
