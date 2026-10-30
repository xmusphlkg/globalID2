"use client";

import { useQuery } from "@tanstack/react-query";
import { Database, Radio, ServerCog } from "lucide-react";
import { controlPlaneClient } from "@/generated/client";
import { EmptyState, MetricStrip, StatusBadge, WorkspacePage } from "@/shared/ui";
import { t, type LangKey } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";

export default function RuntimeView({ scope = "shared" }: { scope?: "shared" | "collection" }) {
  const { lang } = useAppStore();
  const text = (key: LangKey) => t(lang, key);
  const query = useQuery({
    queryKey: ["control-plane", "runtime"],
    queryFn: async () => {
      const { data, error } = await controlPlaneClient.GET("/api/v1/runtime/services");
      if (error) throw new Error("detail" in error ? String(error.detail) : text("cc_scope_runtime_error"));
      return data.data;
    },
    refetchInterval: 10_000,
  });
  const services = query.data?.services ?? [];
  const kinds = new Set(services.map((service) => service.service));
  const healthy = (kind: string) => query.isLoading ? "—" : text(kinds.has(kind) ? "cc_scope_healthy" : "cc_scope_unavailable");
  return <WorkspacePage eyebrow={text(scope === "collection" ? "collection" : "overview")} title={text("cc_scope_runtime")} description={text("cc_scope_runtime_desc")} actions={<button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{text("cc_refresh")}</button>}>
    <MetricStrip items={[
      { label: text("cc_scope_instances"), value: query.isLoading ? "—" : services.length, detail: text(query.data?.heartbeat_available ? "cc_scope_heartbeat_online" : "cc_scope_heartbeat_offline"), icon: Radio, tone: services.length ? "success" : "danger" },
      { label: text("cc_scope_api"), value: healthy("api"), detail: text("cc_scope_delivery"), icon: ServerCog, tone: kinds.has("api") ? "success" : "danger" },
      { label: text("cc_scope_scheduler"), value: healthy("scheduler"), detail: text("cc_scope_dispatch"), icon: Database, tone: kinds.has("scheduler") ? "success" : "danger" },
      { label: text("cc_scope_workers"), value: query.isLoading ? "—" : services.filter((service) => service.service === "worker").length, detail: text("cc_scope_consumers"), icon: ServerCog, tone: kinds.has("worker") ? "success" : "danger" },
    ]} />
    <section className="cc-panel overflow-hidden"><div><h2 className="text-base font-semibold">{text("cc_scope_services")}</h2><p className="cc-muted mt-1">{text("cc_scope_expiry")}</p></div>
      {query.isError && <p role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{query.error instanceof Error ? query.error.message : text("cc_scope_runtime_error")}</p>}
      {services.length ? <div className="overflow-x-auto"><table className="cc-table min-w-[720px]"><thead><tr>{["cc_scope_service", "cc_scope_instance", "cc_scope_host", "cc_scope_metadata", "cc_scope_seen", "cc_scope_status"].map((key) => <th scope="col" key={key}>{text(key as LangKey)}</th>)}</tr></thead><tbody>{services.map((service) => <tr key={service.instance_id}><td className="font-semibold">{["api", "scheduler", "worker"].includes(service.service) ? text(`cc_scope_${service.service}` as LangKey) : service.service}</td><td className="max-w-xs break-all font-mono text-xs">{service.instance_id}</td><td>{service.host} · {service.pid}</td><td className="max-w-sm break-words font-mono text-xs">{Object.keys(service.metadata ?? {}).length ? JSON.stringify(service.metadata) : "—"}</td><td className="text-xs">{new Date(service.last_seen_at).toLocaleString(lang === "zh" ? "zh-CN" : "en-US")}</td><td><StatusBadge tone="success">{text("cc_scope_healthy")}</StatusBadge></td></tr>)}</tbody></table></div> : query.isLoading ? <p role="status" className="cc-muted py-8 text-center">{text("cc_loading")}</p> : !query.isError && <EmptyState title={text("cc_scope_no_instances")} description={text("cc_scope_no_instances_desc")} className="min-h-48" />}
    </section>
  </WorkspacePage>;
}
