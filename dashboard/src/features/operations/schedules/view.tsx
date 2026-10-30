"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Play, RefreshCw, Settings2 } from "lucide-react";

import { t, type LangKey } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";
import type { components } from "@/generated/api";
import { controlPlaneClient } from "@/generated/client";
import { DataTable, EmptyState, FilterBar, MetricStrip, StatusBadge, WorkspacePage, type DataTableColumn } from "@/shared/ui";

import { scopedScheduleKinds, scheduleKindsByScope, scopedTaskHref, type ScheduleScope } from "@/features/operations/task-scope";

type Schedule = components["schemas"]["ScheduleOut"];

export default function SchedulesView({ scope = "collection" }: { scope?: ScheduleScope }) {
  const { lang } = useAppStore();
  const text = (key: LangKey) => t(lang, key);
  const kindLabel = (value: string) => text(`cc_schedule_${value}` as LangKey);
  const statusLabel = (value: string) => ["queued", "running", "completed", "failed", "pending", "retrying", "cancelled", "skipped"].includes(value) ? text(`cc_${value}` as LangKey) : value;
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const requestedKind = searchParams.get("kind");
  const allowedKinds = scopedScheduleKinds(scope, requestedKind);
  const kind = allowedKinds.length === 1 ? allowedKinds[0] : "all";
  const query = useQuery({
    queryKey: ["control-plane", "schedules", scope, kind],
    queryFn: async () => {
      const groups = await Promise.all(allowedKinds.map(async (allowedKind) => {
        const { data, error } = await controlPlaneClient.GET("/api/v1/schedules", {
          params: { query: { kind: allowedKind } },
        });
        if (error) throw new Error("detail" in error ? String(error.detail) : text("cc_schedule_empty_desc"));
        return data.data.filter((row) => row.kind === allowedKind);
      }));
      return groups.flat();
    },
    refetchInterval: 15_000,
  });
  const trigger = useMutation({
    mutationFn: async (scheduleId: string) => {
      if (!query.data?.some((row) => row.id === scheduleId && (scheduleKindsByScope[scope] as readonly string[]).includes(row.kind))) throw new Error(text("cc_scope_schedule_blocked"));
      const { data, error } = await controlPlaneClient.POST("/api/v1/schedules/{schedule_id}/runs", {
        params: { path: { schedule_id: scheduleId } },
      });
      if (error) throw new Error("detail" in error ? String(error.detail) : "Unable to trigger schedule.");
      return data.data;
    },
    onSuccess: (task) => {
      queryClient.invalidateQueries({ queryKey: ["control-plane", "schedules"] });
      router.push(task.task_uuid ? scopedTaskHref(task.task_uuid, scope) : scope === "publish" ? "/publish/tasks" : "/collection/runs");
    },
  });
  const rows = query.data ?? [];
  const enabled = rows.filter((item) => item.enabled).length;
  const failed = rows.filter((item) => item.last_status === "failed").length;
  const columns: DataTableColumn<Schedule>[] = [
    { key: "schedule", header: text("cc_schedules"), render: (row) => <div><p className="font-semibold text-[#1D1D1F]">{row.name}</p><p className="mt-0.5 font-mono text-[11px] text-[#6B7280]">{row.id}</p></div> },
    { key: "kind", header: text("cc_schedule_kind"), render: (row) => <span className="capitalize text-[#4B5563]">{kindLabel(row.kind)}</span> },
    { key: "scope", header: text("cc_schedule_scope"), render: (row) => <span className="text-[#4B5563]">{row.country_code || text("cc_global")}</span> },
    { key: "cadence", header: text("cc_schedule_cadence"), render: (row) => <span className="text-[#4B5563]">{row.interval_minutes ? `${text("cc_schedule_every")}: ${row.interval_minutes}` : row.daily_time ? `${row.daily_time} ${row.timezone || ""}` : text("cc_schedule_event")}</span> },
    { key: "next", header: text("cc_schedule_next"), render: (row) => <span className="whitespace-nowrap text-xs text-[#6B7280]">{row.next_run_at ? new Date(row.next_run_at).toLocaleString(lang === "zh" ? "zh-CN" : "en-US") : "—"}</span> },
    { key: "status", header: text("cc_schedule_last"), render: (row) => <StatusBadge status={row.enabled ? row.last_status : "disabled"}>{row.enabled ? statusLabel(row.last_status) : text("cc_schedule_disabled")}</StatusBadge> },
    { key: "action", header: "", className: "text-right", render: (row) => <button type="button" disabled={!row.enabled || trigger.isPending} onClick={() => trigger.mutate(row.id)} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[#D9D9D6] bg-white px-2.5 text-xs font-semibold text-[#374151] hover:border-[#FB923C] hover:text-[#C2410C] disabled:opacity-40"><Play className="h-3.5 w-3.5" />{text("cc_schedule_now")}</button> },
  ];

  const setKind = (value: string) => {
    const next = new URLSearchParams(searchParams.toString());
    if (value === "all") next.delete("kind"); else next.set("kind", value);
    router.replace(`${pathname}${next.size ? `?${next}` : ""}`);
  };

  return (
    <WorkspacePage eyebrow={text(scope)} title={text(`cc_scope_${scope}_schedules`)} description={text(`cc_scope_${scope}_schedules_desc`)} actions={<div className="flex gap-2">{scope === "collection" && <Link href="/operations/schedules/ingestion" className="inline-flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-3 text-sm font-semibold text-[#374151] hover:bg-[#F7F7F5]"><Settings2 className="h-4 w-4" />{text("cc_schedule_manage")}</Link>}<button type="button" onClick={() => query.refetch()} className="inline-flex h-9 items-center gap-2 rounded-md bg-[#C2410C] px-3 text-sm font-semibold text-white hover:bg-[#9A3412]"><RefreshCw className={`h-4 w-4 ${query.isFetching ? "animate-spin" : ""}`} />{text("cc_refresh")}</button></div>}>
      <MetricStrip items={[
        { label: text("cc_schedule_configured"), value: rows.length,  icon: CalendarClock },
        { label: text("cc_schedule_enabled"), value: enabled,  icon: Play, tone: enabled ? "success" : "neutral" },
        { label: text("cc_failed"), value: failed,  icon: RefreshCw, tone: failed ? "danger" : "success" },
      ]} />
      <FilterBar><span className="mr-1 text-xs font-semibold uppercase tracking-wide text-[#6B7280]">{text("cc_schedule_kind")}</span>{(scope === "publish" ? ["release"] : ["all", ...scheduleKindsByScope[scope]]).map((value) => <button key={value} type="button" aria-pressed={kind === value || (value === "all" && allowedKinds.length === scheduleKindsByScope[scope].length)} onClick={() => setKind(value)} className={`h-8 rounded-md px-3 text-sm font-medium capitalize ${kind === value ? "bg-[#FFF1E8] text-[#C2410C]" : "text-[#4B5563] hover:bg-[#F7F7F5]"}`}>{kindLabel(value)}</button>)}</FilterBar>
      {trigger.isError ? <div role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{trigger.error.message}</div> : null}
      {query.isError ? <div role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{query.error.message}</div> : null}
      <DataTable columns={columns} rows={rows} getRowKey={(row) => row.id} emptyState={<EmptyState title={query.isLoading ? text("cc_loading") : text("cc_schedule_empty")} description={text("cc_schedule_empty_desc")} className="min-h-48" />} />
    </WorkspacePage>
  );
}
