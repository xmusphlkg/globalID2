"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/generated/api";
import { ApiError, apiFetch } from "@/lib/api";
import { MetricStrip, StatusBadge, WorkspacePage } from "@/shared/ui";
import { ModelPublishFeedback, ModelPublishProblem, modelPublishDate, modelPublishLabel, useModelPublishText } from "./models";

type Pending = components["schemas"]["PendingOut"];
type Checks = components["schemas"]["DataReleaseChecksOut"];
type Run = components["schemas"]["RunOut"];
// POST /publish/runs currently publishes a generated dictionary envelope.
type ReleaseResult = components["schemas"]["DataResponse_dict_"]["data"] & { task_uuid?: string; status?: string; reason?: string };
const lines = ["surveillance", "literature", "situation", "knowledge"] as const;

export function PublishView() {
  const { lang, text } = useModelPublishText();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["/publish/pending"], queryFn: () => apiFetch<Pending>("/publish/pending"), refetchInterval: 15000, retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1 });
  const [checks, setChecks] = useState<Checks | null>(null);
  const [result, setResult] = useState<ReleaseResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState<"preflight" | "publish" | null>(null);
  const actionLock = useRef(false);
  const run = useQuery({ queryKey: ["/collection/runs", result?.task_uuid], queryFn: () => apiFetch<Run>(`/collection/runs/${encodeURIComponent(result!.task_uuid!)}`), enabled: !!result?.task_uuid, refetchInterval: (query) => ["completed", "failed", "cancelled"].includes(query.state.data?.status ?? "") ? false : 5000, retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1 });
  const runStatus = run.data?.status;
  useEffect(() => { if (runStatus === "completed") void client.invalidateQueries({ queryKey: ["/publish/pending"] }); }, [runStatus, client]);
  const counts = lines.map((line) => { const scope = query.data?.[line]; return Array.isArray(scope) ? scope.length : scope ? 1 : 0; });
  const pendingCount = counts.reduce((sum, count) => sum + count, 0);
  const activeRun = !!result?.task_uuid && (!runStatus || ["queued", "pending", "running", "retrying"].includes(runStatus));
  async function preflight() {
    if (actionLock.current) return;
    actionLock.current = true; setBusy("preflight"); setError(null); setChecks(null);
    try { setChecks(await apiFetch<Checks>("/releases/site-release/checks")); }
    catch (e) { setError(e); }
    finally { actionLock.current = false; setBusy(null); }
  }
  async function publish() {
    if (actionLock.current) return;
    actionLock.current = true; setBusy("publish"); setError(null);
    try {
      const body: components["schemas"]["PublishRunCreate"] = { job_id: "site-release", full };
      setResult(await apiFetch<ReleaseResult>("/publish/runs", { method: "POST", body: JSON.stringify(body) }));
      await client.invalidateQueries({ queryKey: ["/publish/pending"] });
    } catch (e) { setError(e); }
    finally { actionLock.current = false; setBusy(null); }
  }
  const reason = result?.reason === "already_running" ? text("cc_mp_already_running") : result?.reason === "already_queued" ? text("cc_mp_already_queued") : result?.reason ?? text("cc_mp_active");
  const checkRows = checks ? [
    { label: text("cc_mp_git"), ready: checks.git?.read_access_ok && checks.git?.write_access_ok },
    { label: text("cc_mp_hosting"), ready: checks.cloudflare?.project_access_ok && checks.cloudflare?.token_present && checks.cloudflare?.account_id_present },
    { label: text("cc_mp_commands"), ready: checks.commands?.python_exists && checks.commands?.wrangler_available },
    { label: text("cc_mp_boundary"), ready: checks.repository_boundary?.enforced },
  ] : [];
  return <WorkspacePage eyebrow={text("cc_mp_center")} title={text("cc_mp_publish")} description={text("cc_mp_publish_desc")} actions={<><button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{text("cc_mp_refresh")}</button><Link className="cc-button" href="/publish/reports">{text("cc_mp_reports")}</Link></>}>
    <MetricStrip items={[{ label: text("cc_mp_pending_count"), value: query.data ? pendingCount : "—", tone: pendingCount ? "warning" : "success" }, { label: text("cc_mp_lines_count"), value: query.data ? `${counts.filter(Boolean).length} / 4` : "—" }, { label: text("cc_mp_release_status"), value: result ? modelPublishLabel(runStatus ?? result.status ?? "queued", lang) : text("cc_mp_no_release"), tone: runStatus === "failed" ? "danger" : runStatus === "completed" ? "success" : "neutral" }, { label: text("cc_mp_mode"), value: text(full ? "cc_mp_full" : "cc_mp_incremental") }]} />
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.9fr)]">
      <section className="cc-panel space-y-4" aria-labelledby="pending-title"><div><h2 id="pending-title" className="text-base font-semibold">{text("cc_mp_unpublished")}</h2><p className="cc-muted mt-1">{text("cc_mp_readonly_hint")}</p></div><ModelPublishFeedback query={query} label={text("cc_mp_unpublished").toLowerCase()} />
        {query.data && <><div className="overflow-x-auto"><table className="cc-table w-full text-left text-sm"><thead><tr><th scope="col">{text("cc_mp_line")}</th><th scope="col">{text("cc_mp_scope")}</th></tr></thead><tbody>{lines.slice().sort((a, b) => Number(Boolean(Array.isArray(query.data?.[b]) ? (query.data?.[b] as string[]).length : query.data?.[b])) - Number(Boolean(Array.isArray(query.data?.[a]) ? (query.data?.[a] as string[]).length : query.data?.[a]))).map((line) => { const scopes = query.data[line]; return <tr key={line}><th scope="row" className="font-medium">{modelPublishLabel(line, lang)}</th><td><div className="flex flex-wrap gap-1.5">{Array.isArray(scopes) ? scopes.length ? scopes.map((scope) => <StatusBadge tone="warning" key={scope}><span className="break-all">{scope}</span></StatusBadge>) : <span className="cc-muted">{text("cc_mp_none")}</span> : scopes ? <StatusBadge tone="warning">{text("cc_mp_whole_line")}</StatusBadge> : <span className="cc-muted">{text("cc_mp_none")}</span>}</div></td></tr>; })}</tbody></table></div>{pendingCount === 0 && <p className="cc-muted py-3">{text("cc_mp_no_changes")}</p>}</>}
      </section>
      <section className="cc-panel space-y-4" aria-labelledby="release-title"><h2 id="release-title" className="text-base font-semibold">{text("cc_mp_release")}</h2><ModelPublishProblem error={error} conflict={text("cc_mp_release_conflict")} />
        <div className="rounded-md border border-[var(--cc-border)] p-4"><h3 className="text-sm font-semibold">{text("cc_mp_incremental")}</h3><p className="cc-muted mt-1">{text("cc_mp_incremental_hint")}</p><label className="mt-4 flex items-center gap-2 text-sm font-medium"><input type="checkbox" checked={full} disabled={busy !== null || activeRun} onChange={(e) => setFull(e.target.checked)} className="h-4 w-4 accent-[var(--cc-brand)]" />{text("cc_mp_full")}</label><p className={`mt-2 text-xs leading-5 ${full ? "rounded bg-amber-50 p-2 text-amber-900" : "cc-muted"}`}>{text("cc_mp_full_hint")}</p></div>
        <div className="flex flex-wrap gap-2"><button className="cc-button" disabled={busy !== null} onClick={() => void preflight()}>{text(busy === "preflight" ? "cc_mp_checking" : "cc_mp_preflight")}</button><button className="cc-button cc-button-primary" disabled={busy !== null || activeRun || checks?.overall_ready === false} onClick={() => void publish()}>{text(busy === "publish" ? "cc_mp_submitting" : "cc_mp_submit")}</button></div>
        {checks && <div className="space-y-3 border-t border-[var(--cc-border)] pt-4"><h3 className="font-semibold">{text("cc_mp_checks")}</h3><StatusBadge tone={checks.overall_ready ? "success" : "danger"}>{text(checks.overall_ready ? "cc_mp_ready" : "cc_mp_blocked")}</StatusBadge><p className="cc-muted">{text("cc_mp_checked")}: {modelPublishDate(checks.checked_at, lang)}</p><dl className="divide-y divide-[var(--cc-border)]">{checkRows.map((check) => <div key={check.label} className="flex flex-wrap justify-between gap-2 py-2 text-sm"><dt>{check.label}</dt><dd><StatusBadge tone={check.ready ? "success" : "warning"}>{text(check.ready ? "cc_mp_pass" : "cc_mp_check_failed")}</StatusBadge></dd></div>)}</dl>{!!checks.blockers?.length && <ul className="list-inside list-disc space-y-1 rounded-md bg-rose-50 p-3 text-sm text-rose-800">{checks.blockers.map((blocker, i) => <li className="break-words" key={i}>{blocker}</li>)}</ul>}</div>}
      </section>
    </div>
    {result && <section className="cc-panel space-y-3" aria-labelledby="release-status-title"><h2 id="release-status-title" className="font-semibold">{text("cc_mp_release_status")}</h2><p role="status" className="text-sm">{result.status === "skipped" ? `${text("cc_mp_not_queued")}: ${reason}.` : text("cc_mp_accepted")}</p>{result.task_uuid && <><div className="flex flex-wrap items-center gap-3"><StatusBadge status={runStatus ?? result.status}>{modelPublishLabel(runStatus ?? result.status ?? "queued", lang)}</StatusBadge><Link className="break-all font-mono text-sm text-[var(--cc-link)] underline" href={`/publish/tasks?detail=${encodeURIComponent(result.task_uuid)}`}>{text("cc_mp_open_run")}</Link><span className="break-all font-mono text-xs text-[var(--cc-text-muted)]">{result.task_uuid}</span></div><ModelPublishFeedback query={run} label={text("cc_mp_release_status").toLowerCase()} />{runStatus === "failed" && <p className="rounded-md bg-rose-50 p-3 text-sm text-rose-800">{text("cc_mp_release_failed")}</p>}{runStatus === "completed" && <p className="text-sm text-emerald-800">{text("cc_mp_release_complete")}</p>}</>}</section>}
  </WorkspacePage>;
}
