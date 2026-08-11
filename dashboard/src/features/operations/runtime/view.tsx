"use client";

import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Database, Radio, ServerCog } from "lucide-react";

import { controlPlaneClient } from "@/generated/client";
import { EmptyState, MetricStrip, StatusBadge, WorkspacePage } from "@/shared/ui";

export default function RuntimeView() {
  const query = useQuery({
    queryKey: ["control-plane", "runtime"],
    queryFn: async () => {
      const { data, error } = await controlPlaneClient.GET("/api/v1/runtime/services");
      if (error) throw new Error("detail" in error ? String(error.detail) : "Unable to load runtime health.");
      return data.data;
    },
    refetchInterval: 10_000,
  });
  const services = query.data?.services ?? [];
  const kinds = new Set(services.map((service) => service.service));

  return (
    <WorkspacePage eyebrow="Ingestion & Tasks" title="Runtime" description="Live API, scheduler, and worker health from cross-process TTL heartbeats.">
      <MetricStrip items={[
        { label: "Live instances", value: services.length, detail: query.data?.heartbeat_available ? "Redis heartbeat online" : "Heartbeat storage unavailable", icon: Radio, tone: services.length ? "success" : "danger" },
        { label: "API", value: kinds.has("api") ? "Healthy" : "Unavailable", detail: "Request delivery", icon: ServerCog, tone: kinds.has("api") ? "success" : "danger" },
        { label: "Scheduler", value: kinds.has("scheduler") ? "Healthy" : "Unavailable", detail: "Automated dispatch", icon: Database, tone: kinds.has("scheduler") ? "success" : "danger" },
        { label: "Workers", value: services.filter((service) => service.service === "worker").length, detail: "Queue consumers", icon: ServerCog, tone: kinds.has("worker") ? "success" : "danger" },
      ]} />
      <section className="app-panel overflow-hidden">
        <div className="data-toolbar"><div><h2 className="text-sm font-semibold text-[#1D1D1F]">Service instances</h2><p className="mt-0.5 text-xs text-[#6B7280]">Entries expire automatically when a process stops reporting.</p></div></div>
        {query.isError ? <div className="m-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{query.error instanceof Error ? query.error.message : "Unable to load runtime health."}</div> : null}
        {services.length ? (
          <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead className="border-b border-[#E5E5E2] bg-[#FAFAF9] text-[11px] uppercase tracking-wide text-[#6B7280]"><tr><th className="px-4 py-2.5">Service</th><th className="px-4 py-2.5">Instance</th><th className="px-4 py-2.5">Host</th><th className="px-4 py-2.5">Metadata</th><th className="px-4 py-2.5">Last seen</th><th className="px-4 py-2.5">Status</th></tr></thead><tbody className="divide-y divide-[#E5E5E2]">{services.map((service) => <tr key={service.instance_id} className="hover:bg-[#F7F7F5]"><td className="px-4 py-3 font-semibold capitalize text-[#1D1D1F]">{service.service}</td><td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-[#6B7280]">{service.instance_id}</td><td className="px-4 py-3 text-[#4B5563]">{service.host} · {service.pid}</td><td className="px-4 py-3 font-mono text-xs text-[#6B7280]">{Object.keys(service.metadata ?? {}).length ? JSON.stringify(service.metadata) : "—"}</td><td className="px-4 py-3 text-xs text-[#6B7280]">{formatDistanceToNow(new Date(service.last_seen_at), { addSuffix: true })}</td><td className="px-4 py-3"><StatusBadge tone="success">Healthy</StatusBadge></td></tr>)}</tbody></table></div>
        ) : query.isLoading ? <div className="p-8 text-center text-sm text-[#6B7280]">Loading runtime health...</div> : <EmptyState title="No live instances" description="Start the API, scheduler, and worker to populate runtime heartbeats." className="min-h-64" />}
      </section>
    </WorkspacePage>
  );
}
