"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { t, type LangKey } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";
import { StatusBadge } from "@/shared/ui";
export function useSourceText() { const lang = useAppStore((state) => state.lang); return { lang, text: (key: LangKey) => t(lang, key) }; }
export function useSourceQuery<T>(path: string, enabled = true) { return useQuery({ queryKey: [path], queryFn: () => apiFetch<T>(path), enabled, refetchInterval: 30000, retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1 }); }
export function SourceFeedback({ query }: { query: { isLoading: boolean; error: unknown; isFetching: boolean; refetch: () => unknown } }) {
 const { text } = useSourceText();
 return <>{query.isLoading && <p role="status" className="cc-panel cc-muted animate-pulse">{text("source_loading")}</p>}{query.error != null && <div className="cc-panel space-y-3"><p role="alert" className="text-sm text-rose-800">{text("source_load_error")} {query.error instanceof Error ? query.error.message : String(query.error)}</p><button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{text("source_reload")}</button></div>}</>;
}
export function SourceStatus({ status }: { status: string }) { const { text } = useSourceText(); const key = `source_${status}` as LangKey; const translated = text(key); return <StatusBadge status={status} tone={status === "ok" ? "success" : undefined}>{translated === key ? status : translated}</StatusBadge>; }
export function sourceDate(value?: string | null, lang = "en") { return value ? new Date(value).toLocaleString(lang === "zh" ? "zh-CN" : "en-GB") : "—"; }
export function SourceLinks({ type, kind }: { type: string; kind?: string }) { const { text } = useSourceText(); return <><Link className="cc-button" href={`/collection/runs?task_type=${encodeURIComponent(type)}`}>{text("tasks")}</Link><Link className="cc-button" href={`/collection/schedules${kind ? `?kind=${kind}` : ""}`}>{text("cc_collection_schedules")}</Link></>; }
export function useCollectionAction() {
 const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(false); const [uuid, setUuid] = useState<string | null>(null); const lock = useRef(false); const client = useQueryClient();
 async function submit(body: object) { if (lock.current) return; lock.current = true; setBusy(true); setError(null); setUuid(null); try { const response = await apiFetch<Record<string, unknown>>("/collection/runs", { method: "POST", body: JSON.stringify(body) }); if (typeof response.task_uuid === "string") setUuid(response.task_uuid); await client.invalidateQueries({ queryKey: ["/collection/lines"] }); } catch (failure) { setError(failure); } finally { lock.current = false; setBusy(false); } }
 return { error, busy, uuid, submit };
}
export function CollectionActionResult({ action }: { action: ReturnType<typeof useCollectionAction> }) { const { text } = useSourceText(); return <>{action.error != null && <p role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{action.error instanceof Error ? action.error.message : String(action.error)}</p>}{action.uuid && <p role="status" className="cc-panel border-emerald-200 text-sm">{text("source_submitted")} <Link className="underline" href={`/collection/runs/${encodeURIComponent(action.uuid)}`}>{text("source_open_run")}</Link></p>}</>; }
export function SourceStages({ stages }: { stages?: Array<{ stage: string; status?: string | null }> | null }) {
 const { text } = useSourceText();
 const byName = new Map((stages ?? []).map((stage) => [stage.stage, stage.status]));
 return <ul className="flex flex-wrap gap-1.5" aria-label={text("source_stages")}>{(["collect", "organize", "store"] as const).map((name) => { const status = byName.get(name) || "not_checked"; const statusKey = `source_${status}` as LangKey; const statusLabel = text(statusKey); return <li key={name} className="rounded-full border border-stone-300 px-2 py-0.5 text-xs">{text(`source_stage_${name}` as LangKey)} · {statusLabel === statusKey ? status : statusLabel}</li>; })}</ul>;
}
