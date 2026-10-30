"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components, operations } from "@/generated/api";
import { ApiError, apiFetch } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";
import { MetricStrip, StatusBadge, WorkspacePage } from "@/shared/ui";
import { modelsPublishMessages } from "./models-publish-messages";

type MessageKey = keyof typeof modelsPublishMessages.en;
export function useModelPublishText() {
  const lang = useAppStore((s) => s.lang);
  return { lang, text: (key: MessageKey) => t(lang, key) };
}
export function modelPublishLabel(value: string, lang: "en" | "zh") {
  const key = `cc_mp_${value}`;
  return key in modelsPublishMessages.en ? t(lang, key as MessageKey) : value;
}
export function modelPublishDate(value: string | null | undefined, lang: "en" | "zh") {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(lang === "zh" ? "zh-CN" : "en-US");
}
export function ModelPublishProblem({ error, conflict }: { error: unknown; conflict?: string }) {
  return error ? <div role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800"><p>{error instanceof ApiError && error.status === 409 && conflict ? conflict : error instanceof Error ? error.message : String(error)}</p>{error instanceof ApiError && error.requestId && <p className="mt-1 font-mono text-xs">{error.requestId}</p>}</div> : null;
}
export function ModelPublishFeedback({ query, label }: { query: { isLoading: boolean; isFetching: boolean; error: unknown; refetch: () => unknown }; label: string }) {
  const { text } = useModelPublishText();
  return <>{query.isLoading && <div role="status" className="space-y-3 py-3"><p className="cc-muted">{text("cc_mp_loading")}</p><div className="h-16 animate-pulse rounded-md bg-stone-100" /></div>}{query.error != null && <div className="space-y-3"><ModelPublishProblem error={query.error} /><button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{text("cc_mp_reload")} {label}</button></div>}</>;
}
function useModelData<T>(path: string, enabled = true) {
  return useQuery({ queryKey: [path], queryFn: () => apiFetch<T>(path), enabled, refetchInterval: 15000, retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1 });
}
// These endpoints currently expose open dictionaries. Field constraints below mirror
// the public projection in routers/models.py, without importing provider secrets.
type RouteEnvelope = components["schemas"]["DataResponse_list_dict__"]["data"][number];
type Route = RouteEnvelope & Pick<components["schemas"]["RuntimeRouteOut"], "model_key" | "available_for_routing" | "runtime_model_capacity" | "rate_limit_cooldown_until" | "runtime_failure_cooldown_until" | "routing_status_reason"> & { in_flight: number; failure_cooldown_until?: string | null };
type QueueStatus = NonNullable<operations["queue_api_v1_models_queue_get"]["parameters"]["query"]>["status"];
type Priority = components["schemas"]["PriorityChange"]["priority"];
type WorkItem = components["schemas"]["DataResponse_dict_"]["data"] & { id: number; parent_task_uuid: string; line: string; priority: Priority; status: Exclude<QueueStatus, undefined>; attempt: number; available_at: string | null; route_key: string | null; lease_expires_at: string | null; error: string | null; payload?: unknown; result?: unknown };
const statuses = ["queued", "leased", "succeeded", "failed", "cancelled"] as const;
const priorities = ["urgent", "high", "normal", "low"] as const;

export function ModelsView() {
  const { lang, text } = useModelPublishText();
  const [status, setStatus] = useState<Exclude<QueueStatus, undefined>>("queued");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const actionLock = useRef(false);
  const selectedButton = useRef<HTMLButtonElement | null>(null);
  const routes = useModelData<Route[]>("/models/routes");
  const queue = useModelData<WorkItem[]>(`/models/queue?status=${status}`);
  const detail = useModelData<WorkItem>(`/models/queue/${selectedId}`, selectedId !== null);
  const client = useQueryClient();
  const routable = routes.data?.filter((route) => route.available_for_routing) ?? [];
  const capacity = routable.reduce((sum, route) => sum + (route.runtime_model_capacity ?? 0), 0);
  const inFlight = routes.data?.reduce((sum, route) => sum + route.in_flight, 0) ?? 0;
  const free = routable.reduce((sum, route) => sum + Math.max(0, (route.runtime_model_capacity ?? 0) - route.in_flight), 0);
  async function changePriority(item: WorkItem, priority: Priority) {
    if (actionLock.current) return;
    actionLock.current = true;
    setUpdatingId(item.id); setError(null); setMessage("");
    try {
      const body: components["schemas"]["PriorityChange"] = { priority };
      await apiFetch<components["schemas"]["DataResponse_dict_"]["data"]>(`/models/queue/${item.id}/priority`, { method: "POST", body: JSON.stringify(body) });
      setMessage(text("cc_mp_updated"));
    } catch (e) { setError(e); }
    finally {
      await Promise.all([queue.refetch(), client.invalidateQueries({ queryKey: [`/models/queue/${item.id}`] })]);
      actionLock.current = false; setUpdatingId(null);
    }
  }
  const lease = (item: WorkItem) => item.status !== "leased" || !item.lease_expires_at ? text("cc_mp_no_lease") : new Date(item.lease_expires_at).getTime() <= Date.now() ? text("cc_mp_expired") : modelPublishDate(item.lease_expires_at, lang);
  const closeDetail = () => { setSelectedId(null); selectedButton.current?.focus(); };
  return <WorkspacePage eyebrow={text("cc_mp_center")} title={text("cc_mp_models")} description={text("cc_mp_models_desc")} actions={<><button className="cc-button" disabled={routes.isFetching || queue.isFetching} onClick={() => { void routes.refetch(); void queue.refetch(); }}>{text("cc_mp_refresh")}</button><Link className="cc-button" href="/settings/models">{text("cc_mp_configure")}</Link></>}>
    <MetricStrip items={[{ label: text("cc_mp_routable"), value: routes.data ? `${routable.length} / ${routes.data.length}` : "—", tone: routable.length ? "success" : "warning" }, { label: text("cc_mp_capacity"), value: routes.data ? capacity : "—" }, { label: text("cc_mp_inflight"), value: routes.data ? inFlight : "—" }, { label: text("cc_mp_free"), value: routes.data ? free : "—", tone: free ? "success" : "warning" }]} />
    <section className="cc-panel space-y-4" aria-labelledby="model-routes-title"><h2 id="model-routes-title" className="text-base font-semibold">{text("cc_mp_routes")}</h2><ModelPublishFeedback query={routes} label={text("cc_mp_routes").toLowerCase()} />
      {routes.data?.length === 0 && <p className="cc-muted py-4">{text("cc_mp_no_routes")}</p>}
      {!!routes.data?.length && !routable.length && <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">{text("cc_mp_paused")}</p>}
      <div className="grid gap-3 xl:grid-cols-2">{routes.data?.slice().sort((a, b) => Number(a.available_for_routing) - Number(b.available_for_routing)).map((route) => <article key={route.model_key} className="min-w-0 rounded-md border border-[var(--cc-border)] p-4"><div className="flex flex-wrap items-start justify-between gap-2"><h3 className="break-all font-mono text-sm font-semibold">{route.model_key}</h3><StatusBadge tone={route.available_for_routing ? "success" : "warning"}>{text(route.available_for_routing ? "cc_mp_routable" : "cc_mp_unavailable")}</StatusBadge></div><dl className="mt-3 grid grid-cols-2 gap-3 text-sm"><div><dt className="cc-muted">{text("cc_mp_capacity")}</dt><dd className="font-semibold tabular-nums">{route.runtime_model_capacity ?? 0}</dd></div><div><dt className="cc-muted">{text("cc_mp_inflight")}</dt><dd className="font-semibold tabular-nums">{route.in_flight}</dd></div><div><dt className="cc-muted">{text("cc_mp_rate")}</dt><dd>{modelPublishDate(route.rate_limit_cooldown_until, lang)}</dd></div><div><dt className="cc-muted">{text("cc_mp_failure")}</dt><dd>{modelPublishDate(route.runtime_failure_cooldown_until ?? route.failure_cooldown_until, lang)}</dd></div></dl>{route.routing_status_reason && <p className="cc-muted mt-3 break-words">{route.routing_status_reason}</p>}</article>)}</div>
    </section>
    <section className="cc-panel space-y-4" aria-labelledby="model-queue-title"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 id="model-queue-title" className="text-base font-semibold">{text("cc_mp_queue")}</h2><p className="cc-muted mt-1">{text("cc_mp_edit_hint")}</p></div><label className="flex items-center gap-2 text-sm">{text("cc_mp_status")}<select className="cc-input" value={status} onChange={(e) => { setStatus(e.target.value as Exclude<QueueStatus, undefined>); setSelectedId(null); setError(null); setMessage(""); }}>{statuses.map((value) => <option key={value} value={value}>{modelPublishLabel(value, lang)}</option>)}</select></label></div>
      <ModelPublishProblem error={error} conflict={text("cc_mp_conflict")} />{message && <p role="status" className="text-sm text-emerald-800">{message}</p>}<ModelPublishFeedback query={queue} label={text("cc_mp_queue").toLowerCase()} />
      {queue.data?.length === 0 && <p className="cc-muted py-5">{text("cc_mp_no_items")}</p>}
      {!!queue.data?.length && <div className="overflow-x-auto"><table className="cc-table w-full text-left text-sm"><thead><tr>{["cc_mp_item", "cc_mp_status", "cc_mp_priority", "cc_mp_attempt", "cc_mp_lease"].map((key) => <th scope="col" key={key}>{text(key as MessageKey)}</th>)}</tr></thead><tbody>{queue.data.map((item) => <tr key={item.id}><td className="min-w-44"><button className="text-left font-semibold text-[var(--cc-link)] hover:underline" aria-expanded={selectedId === item.id} aria-controls="model-item-detail" onClick={(e) => { selectedButton.current = e.currentTarget; setSelectedId(item.id); }}>#{item.id} {modelPublishLabel(item.line, lang)}</button><Link className="mt-1 block max-w-64 break-all text-xs text-[var(--cc-text-muted)] underline" href={`/collection/runs/${encodeURIComponent(item.parent_task_uuid)}`}>{text("cc_mp_parent")}: {item.parent_task_uuid}</Link>{item.error && <p className="mt-1 max-w-sm break-words text-xs text-rose-800">{item.error}</p>}</td><td><StatusBadge status={item.status}>{modelPublishLabel(item.status, lang)}</StatusBadge></td><td><select className="cc-input" aria-label={`${text("cc_mp_priority_for")} ${item.id}`} value={item.priority} disabled={item.status !== "queued" || updatingId !== null} onChange={(e) => void changePriority(item, e.target.value as Priority)}>{priorities.map((priority) => <option key={priority} value={priority}>{modelPublishLabel(priority, lang)}</option>)}</select>{updatingId === item.id && <span role="status" className="cc-muted">{text("cc_mp_updating")}</span>}</td><td className="tabular-nums">{item.attempt}</td><td className="min-w-40 text-xs"><p>{lease(item)}</p><p className="cc-muted mt-1">{text("cc_mp_available")}: {modelPublishDate(item.available_at, lang)}</p></td></tr>)}</tbody></table></div>}
      {(queue.data?.length ?? 0) >= 100 && <p className="cc-muted">{text("cc_mp_limit")}</p>}
    </section>
    {selectedId !== null && <section id="model-item-detail" className="cc-panel space-y-4" aria-labelledby="model-detail-title"><div className="flex flex-wrap justify-between gap-3"><h2 id="model-detail-title" className="font-semibold">{text("cc_mp_item")} #{selectedId} {text("cc_mp_details")}</h2><button className="cc-button" onClick={closeDetail}>{text("cc_mp_close")}</button></div><ModelPublishFeedback query={detail} label={text("cc_mp_details")} />{detail.data && <><dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">{[[text("cc_mp_status"), modelPublishLabel(detail.data.status ?? "pending", lang)], [text("cc_mp_priority"), modelPublishLabel(detail.data.priority ?? "normal", lang)], [text("cc_mp_attempt"), detail.data.attempt], [text("cc_mp_lease"), lease(detail.data)], [text("cc_mp_route"), detail.data.route_key ?? "—"]].map(([label, value]) => <div key={label}><dt className="cc-muted">{label}</dt><dd className="mt-1 break-all font-medium">{value}</dd></div>)}</dl>{detail.data.error && <ModelPublishProblem error={detail.data.error} />}<div className="grid gap-4 lg:grid-cols-2">{(["payload", "result"] as const).map((key) => <div key={key} className="min-w-0"><h3 className="mb-2 text-sm font-semibold">{text(key === "payload" ? "cc_mp_payload" : "cc_mp_result")}</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md bg-stone-50 p-3 text-xs">{JSON.stringify(detail.data?.[key] ?? null, null, 2)}</pre></div>)}</div></>}</section>}
  </WorkspacePage>;
}
