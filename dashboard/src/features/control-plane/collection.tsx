"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, Database, FileText, RefreshCw } from "lucide-react";
import type { components } from "@/generated/api";
import { ApiError, apiFetch } from "@/lib/api";
import { t, type LangKey } from "@/lib/i18n";
import { StatusBadge, WorkspacePage } from "@/shared/ui";
import { useAppStore } from "@/stores/app-store";

type Line = components["schemas"]["LineOut"];
type Run = components["schemas"]["RunOut"];
type Schedule = components["schemas"]["ScheduleOut"];
// The generator marks schema defaults as required; the HTTP request may omit them.
type WithDefaults<T, K extends keyof T> = Pick<T, K> & Partial<Omit<T, K>>;
type CreateRun = WithDefaults<components["schemas"]["SurveillanceRunCreate"], "line" | "country_code"> | components["schemas"]["LiteratureRunCreate"] | WithDefaults<components["schemas"]["SituationRunCreate"], "line"> | WithDefaults<components["schemas"]["KnowledgeRunCreate"], "line" | "disease_ids">;
type Language = "en" | "zh";
const labelKeys: Record<string, LangKey> = {
  surveillance: "cc_collection_surveillance", literature: "cc_collection_literature", situation: "cc_collection_situation", knowledge: "cc_collection_knowledge",
  collect: "cc_collection_collect", organize: "cc_collection_organize", store: "cc_collection_store",
  queued: "cc_collection_queued", pending: "cc_collection_pending_status", running: "cc_collection_running", retrying: "cc_collection_retrying_status", completed: "cc_collection_completed", failed: "cc_collection_failed", cancelled: "cc_collection_cancelled", skipped: "cc_collection_skipped", enabled: "cc_collection_enabled", disabled: "cc_collection_disabled", ingestion: "cc_collection_ingestion", release: "cc_collection_release",
};
function label(lang: Language, value?: string | null) { return value ? labelKeys[value] ? t(lang, labelKeys[value]) : value : "—"; }
function date(lang: Language, value?: string | null) { return value ? new Date(value).toLocaleString(lang === "zh" ? "zh-CN" : "en-GB") : "—"; }
function runHref(uuid: string) { return `/collection/runs/${encodeURIComponent(uuid)}`; }
function useData<T>(path: string) {
  return useQuery({ queryKey: [path], queryFn: () => apiFetch<T>(path), refetchInterval: 15000, retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1 });
}
function Problem({ error, conflict }: { error: unknown; conflict?: LangKey }) {
  const lang = useAppStore((state) => state.lang);
  return error ? <div role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
    {conflict && error instanceof ApiError && error.status === 409 && <p className="mb-1 font-semibold">{t(lang, conflict)}</p>}
    <p className="break-words">{error instanceof Error ? error.message : String(error)}</p>
  </div> : null;
}
function Feedback({ query, loading, reload }: { query: { isLoading: boolean; isFetching: boolean; error: unknown; refetch: () => unknown }; loading: LangKey; reload: LangKey }) {
  const lang = useAppStore((state) => state.lang);
  return <>
    {query.isLoading && <div role="status" className="cc-panel animate-pulse"><p className="cc-muted">{t(lang, loading)}</p><div className="mt-3 h-2 w-1/3 rounded bg-stone-200" /></div>}
    {query.error != null && <div className="space-y-3"><p className="cc-muted">{t(lang, "cc_collection_read_error")}</p><Problem error={query.error} /><button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{t(lang, reload)}</button></div>}
  </>;
}
function Metadata({ title, children }: { title: string; children: React.ReactNode }) { return <div className="min-w-0"><dt className="cc-muted text-xs">{title}</dt><dd className="mt-1 break-words text-sm font-medium">{children}</dd></div>; }
function JsonMetrics({ value }: { value: Record<string, unknown> }) {
  return <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-stone-50 p-3 font-mono text-xs text-stone-700">{JSON.stringify(value, null, 2)}</pre>;
}

export function CollectionView({ initialCountry = "US" }: { initialCountry?: string }) {
  const lang = useAppStore((state) => state.lang);
  const query = useData<Line[]>("/collection/lines");
  const schedules = useData<Schedule[]>("/schedules");
  const client = useQueryClient();
  const [country, setCountry] = useState(initialCountry);
  useEffect(() => setCountry(initialCountry), [initialCountry]);
  const submitLock = useRef(false);
  const [disease, setDisease] = useState("");
  const [kind, setKind] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<{ ids: string[]; skipped?: number } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const diseaseIds = [...new Set(disease.split(/[,，]/).map((value) => value.trim()).filter(Boolean))];
  async function start(line: string) {
    if (submitLock.current) return;
    let body: CreateRun;
    switch (line) {
      case "surveillance": body = { line, country_code: country.trim(), fill_missing: false }; break;
      case "literature": body = { line }; break;
      case "situation": body = { line }; break;
      case "knowledge": body = { line, disease_ids: diseaseIds }; break;
      default: return;
    }
    submitLock.current = true;
    setBusy(line); setError(null); setResult(null);
    try {
      // The OpenAPI response is an unstructured dictionary; narrow only the
      // documented task references, leaving all other response fields opaque.
      const response = await apiFetch<Record<string, unknown>>("/collection/runs", { method: "POST", body: JSON.stringify(body) });
      const ids = typeof response.task_uuid === "string" ? [response.task_uuid] : Array.isArray(response.created_tasks) ? response.created_tasks.flatMap((task: unknown) => task && typeof task === "object" && "task_uuid" in task && typeof task.task_uuid === "string" ? [task.task_uuid] : []) : [];
      setResult({ ids, skipped: Array.isArray(response.skipped) ? response.skipped.length : undefined });
      await Promise.all([query.refetch(), client.invalidateQueries({ queryKey: ["/collection/summary"] })]);
    } catch (failure) { setError(failure); } finally { submitLock.current = false; setBusy(null); }
  }
  const orderedLines = [...(query.data ?? [])].sort((a, b) => Number(b.latest_stage_status === "failed") - Number(a.latest_stage_status === "failed"));
  const visibleSchedules = (schedules.data ?? []).filter((schedule) => !kind || schedule.kind === kind);
  return <WorkspacePage eyebrow={t(lang, "cc_collection_center")} title={t(lang, "cc_collection_title")} description={t(lang, "cc_collection_description")} actions={<Link className="cc-button" href="/collection/runs">{t(lang, "cc_collection_all")}<ArrowRight aria-hidden="true" className="h-4 w-4" /></Link>}>
    <Feedback query={query} loading="cc_collection_loading_lines" reload="cc_collection_reload_lines" />
    <Problem error={error} conflict="cc_collection_conflict" />
    {result && <div role="status" className="cc-panel border-emerald-200 bg-emerald-50 text-sm"><p className="font-medium">{t(lang, "cc_collection_accepted")}</p>{result.skipped !== undefined && <p>{t(lang, "cc_collection_batch")}: {result.ids.length} / {result.skipped}</p>}<div className="mt-2 flex flex-wrap gap-3">{result.ids.map((uuid, index) => <Link key={uuid} className="underline" href={runHref(uuid)}>{t(lang, "cc_collection_open_queued")}{result.ids.length > 1 ? ` ${index + 1}` : ""}</Link>)}</div></div>}
    {query.data?.length === 0 && <div className="cc-panel cc-muted">{t(lang, "cc_collection_empty_lines")}</div>}
    <div className="grid gap-4 xl:grid-cols-2">
      {orderedLines.map((line) => <section className={`cc-panel flex min-w-0 flex-col gap-4 ${line.latest_stage_status === "failed" ? "border-rose-200" : ""}`} key={line.line}>
        <div className="flex items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-base font-semibold"><Database aria-hidden="true" className="h-4 w-4 text-orange-700" />{label(lang, line.line)}</h2><StatusBadge status={line.latest_stage_status}>{label(lang, line.latest_stage_status)}</StatusBadge></div>
        <dl className="grid grid-cols-2 gap-4"><Metadata title={t(lang, "cc_collection_next")}>{date(lang, line.next_run_at)}</Metadata><Metadata title={t(lang, "cc_collection_latest")}>{label(lang, line.latest_stage)}</Metadata><Metadata title={t(lang, "cc_collection_pending")}><Link href="/publish" className="text-orange-800 underline underline-offset-4">{line.pending_publish_count}</Link></Metadata><Metadata title={t(lang, "cc_collection_all")}>{line.latest_task_uuid ? <Link className="text-orange-800 underline underline-offset-4" href={runHref(line.latest_task_uuid)}>{t(lang, "cc_collection_open_latest")}</Link> : <span className="cc-muted">{t(lang, "cc_collection_no_runs")}</span>}</Metadata></dl>
        <form className="mt-auto space-y-3 border-t border-stone-200 pt-4" onSubmit={(event) => { event.preventDefault(); void start(line.line); }}>
          {line.line === "surveillance" && <label className="block space-y-1.5 text-xs font-medium">{t(lang, "cc_collection_country")}<input aria-label={t(lang, "cc_collection_country")} className="cc-input block w-full" value={country} minLength={2} maxLength={16} required onChange={(event) => setCountry(event.target.value.toUpperCase())} autoCapitalize="characters" /></label>}
          {line.line === "knowledge" && <label className="block space-y-1.5 text-xs font-medium">{t(lang, "cc_collection_diseases")}<input aria-label={t(lang, "cc_collection_diseases")} className="cc-input block w-full" value={disease} required onChange={(event) => setDisease(event.target.value)} placeholder="D001, D002" /><span className="cc-muted block font-normal">{t(lang, "cc_collection_diseases_hint")}</span></label>}
          <button type="submit" className="cc-button cc-button-primary" disabled={busy !== null || (line.line === "knowledge" && !diseaseIds.length) || (line.line === "surveillance" && country.trim().length < 2)}>{busy === line.line ? t(lang, "cc_collection_submitting") : t(lang, "cc_collection_start")}</button>
        </form>
      </section>)}
    </div>
    <section id="schedules" className="cc-panel scroll-mt-24 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="flex items-center gap-2 font-semibold"><CalendarClock className="h-4 w-4" aria-hidden="true" />{t(lang, "cc_collection_schedules")}</h2><label className="flex items-center gap-2 text-xs">{t(lang, "cc_collection_filter")}<select className="cc-input" value={kind} onChange={(event) => setKind(event.target.value)}><option value="">{t(lang, "cc_collection_filter_all")}</option>{["ingestion", "literature", "situation", "release"].map((value) => <option value={value} key={value}>{label(lang, value)}</option>)}</select></label></div>
      <p className="cc-muted text-xs">{t(lang, "cc_collection_schedule_hint")}</p>
      <Feedback query={schedules} loading="cc_collection_loading_schedules" reload="cc_collection_reload_schedules" />
      {schedules.data?.length === 0 ? <p className="cc-muted">{t(lang, "cc_collection_empty_schedules")}</p> : schedules.data && visibleSchedules.length === 0 ? <p className="cc-muted">{t(lang, "cc_collection_filter_empty")}</p> : visibleSchedules.length > 0 && <div className="overflow-x-auto"><table className="cc-table w-full text-left text-sm"><thead><tr>{["cc_collection_name", "cc_collection_kind", "cc_collection_status", "cc_collection_next"].map((key) => <th scope="col" key={key}>{t(lang, key as LangKey)}</th>)}</tr></thead><tbody>{visibleSchedules.map((schedule) => <tr key={schedule.id}><td><span className="font-medium">{schedule.name}</span>{schedule.last_error && <p className="mt-1 max-w-sm break-words text-xs text-rose-700">{schedule.last_error}</p>}</td><td>{label(lang, schedule.kind)}</td><td><StatusBadge status={schedule.enabled === false ? "disabled" : schedule.last_status}>{schedule.enabled === false ? t(lang, "cc_collection_disabled") : label(lang, schedule.last_status)}</StatusBadge></td><td className="whitespace-nowrap">{date(lang, schedule.next_run_at)}</td></tr>)}</tbody></table></div>}
    </section>
  </WorkspacePage>;
}

export function RunView({ taskUuid }: { taskUuid: string }) {
  const lang = useAppStore((state) => state.lang);
  const client = useQueryClient();
  const path = `/collection/runs/${encodeURIComponent(taskUuid)}`;
  const query = useData<Run>(path);
  const [error, setError] = useState<unknown>(null);
  const [retrying, setRetrying] = useState(false);
  const retryLock = useRef(false);
  const [retryAccepted, setRetryAccepted] = useState(false);
  const run = query.data;
  const attempts = run ? [...new Set([...run.stages.map((stage) => stage.attempt), ...run.artifacts.map((artifact) => artifact.attempt), ...(run.attempt > 0 ? [run.attempt] : [])])].sort((a, b) => b - a) : [];
  async function retry() {
    if (retryLock.current) return;
    retryLock.current = true;
    setRetrying(true); setError(null); setRetryAccepted(false);
    try {
      const response = await apiFetch<components["schemas"]["TaskReferenceOut"]>(`/tasks/${encodeURIComponent(taskUuid)}/retry`, { method: "POST" });
      setRetryAccepted(true);
      client.setQueryData<Run>([path], (previous) => previous ? { ...previous, status: response.status ?? "queued" } : previous);
      await Promise.all([query.refetch(), client.invalidateQueries({ queryKey: ["/collection/lines"] }), client.invalidateQueries({ queryKey: ["/collection/summary"] })]);
    } catch (failure) { setError(failure); if (failure instanceof ApiError && failure.status === 409) await query.refetch(); } finally { retryLock.current = false; setRetrying(false); }
  }
  return <WorkspacePage eyebrow={t(lang, "cc_collection_title")} title={t(lang, "cc_collection_run")} description={<span className="break-all font-mono text-xs">{taskUuid}</span>} actions={<><Link className="cc-button" href="/collection">{t(lang, "cc_collection_title")}</Link><Link className="cc-button" href="/collection/runs">{t(lang, "cc_collection_all")}</Link></>}>
    <Feedback query={query} loading="cc_collection_loading_run" reload="cc_collection_reload_run" />
    <Problem error={error} conflict="cc_collection_retry_conflict" />
    {retryAccepted && run?.status === "queued" && <p role="status" className="cc-panel border-blue-200 bg-blue-50 text-sm text-blue-800">{t(lang, "cc_collection_retry_accepted")}</p>}
    {run && <>
      <section className="cc-panel space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-3"><StatusBadge status={run.status}>{label(lang, run.status)}</StatusBadge>{run.line && <span className="text-sm font-medium">{label(lang, run.line)}</span>}{run.country_code && <span className="text-sm">{run.country_code}</span>}{run.attempt > 0 && <span className="cc-muted text-sm">{t(lang, "cc_collection_attempt")} {run.attempt}</span>}</div>{(run.status === "failed" || run.status === "cancelled" || (run.status === "completed" && Boolean(run.line))) && <button className="cc-button cc-button-primary" disabled={retrying} onClick={() => void retry()}><RefreshCw className={`h-4 w-4 ${retrying ? "animate-spin" : ""}`} aria-hidden="true" />{retrying ? t(lang, "cc_collection_retrying") : t(lang, run.status === "completed" ? "cc_collection_reexecute" : "cc_collection_retry")}</button>}</div>
        <p className="cc-muted text-xs">{t(lang, "cc_collection_retry_hint")}</p>
      </section>
      {attempts.map((attempt) => <section className="cc-panel space-y-4" key={attempt}>
        <h2 className="font-semibold">{t(lang, "cc_collection_attempt")} {attempt}</h2>
        <div className="grid gap-3 lg:grid-cols-3">{["collect", "organize", "store"].map((name, index) => {
          const stage = run.stages.find((item) => item.attempt === attempt && item.stage === name);
          return <article key={name} className={`min-w-0 space-y-3 rounded-md border p-4 ${stage?.status === "failed" ? "border-rose-200 bg-rose-50/40" : "border-stone-200"}`}>
            <div className="flex items-center justify-between gap-2"><h3 className="flex items-center gap-2 font-semibold"><span aria-hidden="true" className="flex h-6 w-6 items-center justify-center rounded-full bg-stone-100 text-xs text-stone-600">{index + 1}</span>{label(lang, name)}</h3><StatusBadge status={stage?.status}>{stage ? label(lang, stage.status) : t(lang, "cc_collection_not_started")}</StatusBadge></div>
            {stage && <><dl className="space-y-2"><Metadata title={t(lang, "cc_collection_started")}>{date(lang, stage.started_at)}</Metadata><Metadata title={t(lang, "cc_collection_finished")}>{date(lang, stage.finished_at)}</Metadata><Metadata title={t(lang, "cc_collection_duration")}>{stage.finished_at ? `${Math.max(0, (new Date(stage.finished_at).getTime() - new Date(stage.started_at).getTime()) / 1000).toFixed(1)} s` : "—"}</Metadata></dl>{stage.error && <p className="break-words rounded bg-rose-50 p-2 text-sm text-rose-800">{stage.error}</p>}<details open={stage.status === "failed"}><summary className="cursor-pointer text-xs font-medium">{t(lang, "cc_collection_metrics")}</summary><div className="mt-2"><JsonMetrics value={stage.metrics} /></div></details></>}
          </article>;
        })}</div>
      </section>)}
      {!run.stages.length && <p className="cc-panel cc-muted">{t(lang, "cc_collection_no_stages")}</p>}
      <section className="cc-panel space-y-4"><div><h2 className="flex items-center gap-2 font-semibold"><FileText aria-hidden="true" className="h-4 w-4" />{t(lang, "cc_collection_artifacts")}</h2><p className="cc-muted mt-1 text-xs">{t(lang, "cc_collection_artifact_hint")}</p></div>{run.artifacts.map((artifact, index) => <article key={`${artifact.attempt}:${artifact.relative_path}:${index}`} className="space-y-2 border-t border-stone-200 pt-4 text-sm"><p className="break-all font-mono font-medium">{artifact.relative_path}</p><p className="cc-muted">{t(lang, "cc_collection_attempt")} {artifact.attempt} · {artifact.kind} · {artifact.byte_size.toLocaleString(lang === "zh" ? "zh-CN" : "en-GB")} {t(lang, "cc_collection_bytes")}</p><p className="break-all font-mono text-xs">SHA256: {artifact.sha256}</p><p className="break-all text-xs">{t(lang, "cc_collection_source")}: {artifact.source_url ?? "—"}</p></article>)}{!run.artifacts.length && <p className="cc-muted">{t(lang, "cc_collection_no_artifacts")}</p>}</section>
      <section className="cc-panel space-y-3"><h2 className="font-semibold">{t(lang, "cc_collection_publication")}</h2>{run.publication.map((publication, index) => {
        const line = typeof publication.line === "string" ? publication.line : "";
        const scope = typeof publication.scope_key === "string" ? publication.scope_key : "";
        const publishedAt = typeof publication.published_at === "string" ? publication.published_at : null;
        const publishedTask = typeof publication.published_task_uuid === "string" ? publication.published_task_uuid : null;
        return <div key={index} className="flex flex-wrap items-center gap-2 border-t border-stone-200 pt-3 text-sm"><span>{label(lang, line)} / {scope}</span><StatusBadge status={publishedAt ? "published" : "queued"}>{t(lang, publishedAt ? "cc_collection_published" : "cc_collection_unpublished")}</StatusBadge>{publishedAt && <span className="cc-muted">{date(lang, publishedAt)}</span>}{publishedTask && <Link className="break-all text-orange-800 underline" href={runHref(publishedTask)}>{publishedTask}</Link>}</div>;
      })}{!run.publication.length && <p className="cc-muted">{t(lang, "cc_collection_no_publication")}</p>}<Link className="inline-flex items-center gap-2 text-sm font-medium text-orange-800 underline underline-offset-4" href="/publish">{t(lang, "cc_collection_review_publish")}<ArrowRight aria-hidden="true" className="h-4 w-4" /></Link></section>
    </>}
  </WorkspacePage>;
}
