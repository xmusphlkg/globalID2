"use client";

import Link from "next/link";
import { useState } from "react";
import { StatusBadge, WorkspacePage } from "@/shared/ui";
import { ApiError } from "@/lib/api";
import { displayedTaskProgress } from "@/lib/task-progress";
import { useAppStore } from "@/stores/app-store";
import { useRealtimeAction, useRealtimeOverview, useRealtimePending, useRealtimeRuns, type RealtimeAction } from "./hooks";
import {
  realtimeAge, realtimeDate, realtimeDuration, realtimeIssue, realtimeProblem,
  realtimeStatus, realtimeTaskHref, realtimeText, realtimeRetryStatus, realtimeContinuationStatus, realtimeBlockedReason, type RealtimeLanguage, type RealtimeText,
} from "./presentation";
import type {
  PendingEligibility, RealtimeActionResult, RealtimeChannel, RealtimeChannelOverview,
  RealtimeControlResult, RealtimePage, RealtimePending, RealtimeRun,
} from "./types";

type Feedback = { action: RealtimeAction; result: RealtimeActionResult | RealtimeControlResult };

function TaskLink({ uuid, children }: { uuid: string; children: React.ReactNode }) {
  return <Link className="break-all text-[var(--cc-link)] underline underline-offset-2" href={realtimeTaskHref(uuid)}>{children}</Link>;
}

function Problem({ error, text }: { error: unknown; text: RealtimeText }) {
  return error ? <p role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{realtimeProblem(error, text)}</p> : null;
}

function Pagination({ data, page, change, fetching, text }: {
  data: RealtimePage<unknown> | undefined; page: number; change: (page: number) => void; fetching: boolean; text: RealtimeText;
}) {
  if (!data || data.total_pages < 2) return null;
  return <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--cc-border)] pt-3 text-sm">
    <span className="cc-muted">{text("page")} {data.page} {text("of")} {data.total_pages} · {data.total} {text("items")}</span>
    <div className="flex gap-2">
      <button className="cc-button" disabled={fetching || page <= 1} onClick={() => change(page - 1)}>{text("previous")}</button>
      <button className="cc-button" disabled={fetching || page >= data.total_pages} onClick={() => change(page + 1)}>{text("next")}</button>
    </div>
  </div>;
}

function ChannelCard({ data, lang, text, busy, unavailable, control }: {
  data: RealtimeChannelOverview; lang: RealtimeLanguage; text: RealtimeText;
  busy: boolean; unavailable: boolean; control: (action: RealtimeAction) => void;
}) {
  const { channel, monitor, manifest, backlog } = data;
  const automaticPaused = data.paused || !!data.control_error_code;
  const automatic = !data.enabled ? "disabled" : data.control_error_code ? "control_unavailable" : data.paused ? "automatic_paused" : "automatic_active";
  const stale = monitor.stale || manifest.stale;
  const issues = monitor.issues ?? [];
  const critical = monitor.status === "critical" || issues.some(issue => issue.level === "critical");
  const lastFailure = data.latest_failure?.completed_at ? Date.parse(data.latest_failure.completed_at) : NaN;
  const lastSuccess = data.latest_success?.completed_at ? Date.parse(data.latest_success.completed_at) : NaN;
  const hasLaterSuccess = Number.isFinite(lastFailure) && Number.isFinite(lastSuccess) && lastSuccess > lastFailure;
  const monitorLabel = critical ? "critical" : stale ? "snapshot_stale" : monitor.status === "ok" ? "healthy" : monitor.status === "warning" ? "warning" : "unknown";
  const monitorTone = critical ? "danger" : stale || monitor.status === "warning" ? "warning" : monitor.status === "ok" ? "success" : "neutral";
  return <section className="cc-panel" aria-labelledby={`rt-${channel}-title`}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 id={`rt-${channel}-title`} className="text-base font-semibold">{text(channel)}</h2><p className="cc-muted mt-1 text-sm">{text(channel === "surveillance" ? "surveillance_description" : channel === "literature" ? "literature_description" : "situation_description")}</p></div>
      <StatusBadge tone={!data.enabled ? "neutral" : data.paused || data.control_error_code ? "warning" : "success"}>{text(automatic)}</StatusBadge>
    </div>
    <dl className="grid grid-cols-3 gap-3 rounded-md bg-[var(--cc-workspace)] p-3">
      {([ ["total_pending", backlog.total.count], ["publishable", backlog.publishable.count], ["blocked", backlog.blocked.count] ] as const).map(([key, value]) => <div key={key}><dt className="cc-muted text-xs">{text(key)}</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{value.toLocaleString(lang === "zh" ? "zh-CN" : "en-US")}</dd></div>)}
    </dl>
    <p className="cc-muted text-xs">{text("oldest")}: {realtimeAge(backlog.publishable.oldest_age_seconds, text)} · {text("scopes")}: {backlog.publishable.scope_count}</p>
    <div className="space-y-2 border-t border-[var(--cc-border)] pt-3">
      <h3 className="text-sm font-semibold">{text("version")}</h3>
      <p className="break-all font-mono text-xs">{manifest.release_id || text("no_version")}</p>
      <dl className="space-y-1 text-xs">
        <div className="flex flex-wrap justify-between gap-2"><dt className="cc-muted">{text("generated")}</dt><dd>{realtimeDate(manifest.generated_at, lang)}</dd></div>
        <div className="flex flex-wrap justify-between gap-2"><dt className="cc-muted">{text("observed")}</dt><dd>{realtimeDate(monitor.checked_at || manifest.observed_at, lang)}</dd></div>
      </dl>
      {manifest.sha256 && <details className="text-xs"><summary className="cc-muted cursor-pointer">{text("checksum")}</summary><p className="mt-2 break-all font-mono">{manifest.sha256}</p></details>}
      <div className="flex flex-wrap gap-2">
        <StatusBadge tone={monitor.http_status === 200 ? stale ? "warning" : "success" : monitor.http_status == null ? "neutral" : "danger"}>{text("http")}: {monitor.http_status ?? text("not_checked")}</StatusBadge>
        <StatusBadge tone={monitor.sample_verified == null ? "neutral" : !monitor.sample_verified ? "danger" : stale ? "warning" : "success"}>{monitor.sample_verified == null ? `${text("sample_checksum")}: ${text("not_checked")}` : text(monitor.sample_verified ? "verified" : "failed_verification")}</StatusBadge>
      </div>
    </div>
    <div className="space-y-2 border-t border-[var(--cc-border)] pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">{text("monitoring")}</h3><div className="flex flex-wrap gap-2"><StatusBadge tone={monitorTone}>{text(monitorLabel)}</StatusBadge>{critical && stale && <StatusBadge tone="warning">{text("snapshot_stale")}</StatusBadge>}</div></div>
      <p className="cc-muted text-xs">{text("monitor_age")}: {realtimeAge(monitor.age_seconds, text)}</p>
      {issues.length > 0 && <ul className="list-inside list-disc space-y-1 text-sm">{issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{realtimeIssue(issue.code, text)}</li>)}</ul>}
      <p className="cc-muted text-xs leading-5">{text("snapshot_hint")}</p>
    </div>
    <div className="space-y-3 border-t border-[var(--cc-border)] pt-3">
      <h3 className="text-sm font-semibold">{text("queue")}</h3>
      <dl className="flex flex-wrap gap-x-5 gap-y-2 text-xs">{(["pending", "queued", "running", "retrying"] as const).map(status => <div key={status}><dt className="cc-muted">{text(status)}</dt><dd className="mt-1 font-semibold tabular-nums">{data.queue[status]}</dd></div>)}<div><dt className="cc-muted">{text("failed_24h")}</dt><dd className="mt-1 font-semibold tabular-nums">{data.failed_last_24h}</dd></div></dl>
      <dl className="space-y-2 text-xs">{([ ["current_run", data.current_run], ["latest_success", data.latest_success], ["latest_failure", data.latest_failure] ] as const).map(([label, run]) => <div key={label} className="flex flex-wrap justify-between gap-2"><dt className="cc-muted">{text(label)}</dt><dd>{run ? <TaskLink uuid={run.task_uuid}>{realtimeStatus(run.status, text)} · {realtimeDate(run.completed_at || run.started_at || run.created_at, lang)}</TaskLink> : text("none")}</dd></div>)}</dl>
      {hasLaterSuccess && <p className="cc-muted text-xs">{text("failure_followed_by_success")}</p>}
    </div>
    <div className="space-y-2 border-t border-[var(--cc-border)] pt-3">
      <p className="cc-muted text-xs">{text("enabled_gate")}: {text(data.enabled ? "enabled" : "disabled_short")}</p>
      <button className="cc-button" aria-label={`${text(automaticPaused ? "resume" : "pause")} · ${text(channel)}`} disabled={busy || unavailable || !data.enabled} onClick={() => control({ kind: "control", channel, paused: !automaticPaused })}>{text(automaticPaused ? "resume" : "pause")}</button>
      {!data.enabled && <p className="cc-muted text-xs leading-5">{text("disabled_hint")}</p>}
    </div>
  </section>;
}

function ChangesTable({ items, text }: { items: RealtimePending[]; text: RealtimeText }) {
  if (!items.length) return <p className="cc-muted py-4 text-sm">{text("changes_empty")}</p>;
  return <div className="overflow-x-auto"><table className="cc-table min-w-[42rem]"><thead><tr>{(["scope", "eligibility", "age", "coverage", "source_task"] as const).map(key => <th key={key} scope="col">{text(key)}</th>)}</tr></thead><tbody>{items.map(item => <tr key={item.id}>
    <td className="font-medium"><span className="break-all">{item.scope_key}</span><p className="cc-muted mt-1 text-xs">#{item.id}</p></td>
    <td className="min-w-48"><StatusBadge tone={item.publishable ? "success" : "warning"}>{text(item.publishable ? "publishable" : "blocked")}</StatusBadge>{!item.publishable && <p className="cc-muted mt-2 max-w-xs break-words text-xs">{realtimeBlockedReason(item.blocked_reason, text)}</p>}</td>
    <td className="whitespace-nowrap">{realtimeAge(item.age_seconds, text)}</td>
    <td><p>{text(item.precise ? "precise" : "whole_scope")}</p>{item.precise && item.disease_ids && item.disease_ids.length > 0 && <p className="cc-muted mt-1 max-w-xs break-words text-xs">{item.disease_ids.slice(0, 3).join(", ")}{item.disease_ids.length > 3 ? ` +${item.disease_ids.length - 3}` : ""}</p>}<p className="cc-muted mt-1 text-xs">{text("countries")}: {item.affected_country_count}</p></td>
    <td>{item.source_task_uuid ? <Link className="text-[var(--cc-link)] underline underline-offset-2" href={`/collection/runs/${encodeURIComponent(item.source_task_uuid)}`}>{text("open_task")}</Link> : "—"}</td>
  </tr>)}</tbody></table></div>;
}

function RunsTable({ items, lang, text }: { items: RealtimeRun[]; lang: RealtimeLanguage; text: RealtimeText }) {
  if (!items.length) return <p className="cc-muted py-4 text-sm">{text("runs_empty")}</p>;
  return <div className="overflow-x-auto"><table className="cc-table"><thead><tr>{(["task", "status", "changes", "retries", "continuation", "timing"] as const).map(key => <th key={key} scope="col">{text(key)}</th>)}</tr></thead><tbody>{items.map(run => <tr key={run.task_uuid}>
    <td><TaskLink uuid={run.task_uuid}><span className="font-mono text-xs">{run.task_uuid}</span></TaskLink><p className="cc-muted mt-1 whitespace-nowrap text-xs">{realtimeDate(run.created_at, lang)}</p>{run.release_id && <p className="cc-muted mt-1 max-w-xs break-all font-mono text-xs">{run.release_id}</p>}</td>
    <td><StatusBadge status={run.status}>{realtimeStatus(run.status, text)}</StatusBadge>{displayedTaskProgress(run.status, run.progress) != null && Number.isFinite(displayedTaskProgress(run.status, run.progress)) && <p className="cc-muted mt-1 text-xs">{Math.max(0, Math.min(100, displayedTaskProgress(run.status, run.progress) ?? 0))}%</p>}</td>
    <td>{run.selected_change_count ?? "—"}{run.country_count != null && run.country_count > 0 && <p className="cc-muted mt-1 whitespace-nowrap text-xs">{text("countries")}: {run.country_count}</p>}</td>
    <td><p className="mb-2 text-xs">{realtimeRetryStatus(run.retry?.status, text)}</p><p className="text-xs">{text("attempts")}: {run.retry?.scheduled_attempts ?? 0} / {run.retry?.max_attempts ?? 0}</p><p className="cc-muted mt-1 text-xs">{text(run.retry?.exhausted ? "retry_exhausted" : run.retry?.retryable ? "retry_available" : "retry_not_available")}</p>{run.retry?.next_attempt_at && <p className="mt-1 text-xs">{text("next_attempt")}: {realtimeDate(run.retry.next_attempt_at, lang)}</p>}</td>
    <td>{run.continuation?.status && run.continuation.status !== "none" && <p className="mb-2 text-xs">{realtimeContinuationStatus(run.continuation.status, text)}</p>}<p className="text-xs">{(run.continuation?.remaining_count ?? 0) > 0 ? `${text("remaining")}: ${run.continuation?.remaining_count}` : text("no_remaining")}</p>{run.continuation?.task_uuid && <p className="mt-2 text-xs"><TaskLink uuid={run.continuation.task_uuid}>{text("followup_task")}</TaskLink></p>}</td>
    <td>{run.release_timing ? <dl className="space-y-1 whitespace-nowrap text-xs">{([ ["export_time", run.release_timing.export_seconds], ["publish_time", run.release_timing.publish_and_ack_seconds], ["total_time", run.release_timing.elapsed_seconds] ] as const).map(([key, seconds]) => <div key={key}><dt className="cc-muted inline">{text(key)}: </dt><dd className="inline">{realtimeDuration(seconds, text)}</dd></div>)}</dl> : <span className="cc-muted text-xs">{text("no_timing")}</span>}</td>
  </tr>)}</tbody></table></div>;
}

export function RealtimePublishView() {
  const lang = useAppStore(state => state.lang);
  const text = realtimeText(lang);
  const [requestedChannel, setChannel] = useState<RealtimeChannel>("surveillance");
  const [pendingPage, setPendingPage] = useState(1);
  const [eligibility, setEligibility] = useState<PendingEligibility>("all");
  const [runPage, setRunPage] = useState(1);
  const [runStatus, setRunStatus] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const overview = useRealtimeOverview();
  const realtimeChannels = Object.values(overview.data?.channels ?? {}).map(item => item.channel);
  const channel = realtimeChannels.includes(requestedChannel) ? requestedChannel : realtimeChannels[0] ?? requestedChannel;
  const changes = useRealtimePending(channel, pendingPage, eligibility);
  const runs = useRealtimeRuns(channel, runPage, runStatus);
  const action = useRealtimeAction();
  const selected = overview.data?.channels[channel];
  const unavailable = !overview.data || !!overview.error;
  const canPublish = !!selected?.manual_enabled && selected.backlog.publishable.count > 0 && !unavailable && !action.isPending;
  const conflictTaskUuid = action.error instanceof ApiError && action.error.status === 409
    ? action.error.taskUuid ?? selected?.current_run?.task_uuid : undefined;
  const refreshing = overview.isFetching || changes.isFetching || runs.isFetching;
  async function refresh() {
    await Promise.all([overview.refetch(), changes.refetch(), runs.refetch()]);
  }
  async function submit(request: RealtimeAction) {
    setFeedback(null);
    try {
      const result = await action.submit(request);
      if (result) setFeedback({ action: request, result });
    } catch {
      // The mutation exposes only a localized safe problem to the user.
    }
  }
  let feedbackMessage = "";
  let feedbackTask: string | null = null;
  if (feedback) {
    if ("paused" in feedback.result) feedbackMessage = text(feedback.result.error_code ? "control_unavailable" : feedback.result.paused ? "pause_saved" : "resume_saved");
    else {
      feedbackMessage = text(feedback.result.status === "queued" ? "accepted" : feedback.result.task_uuid ? "already_active" : "not_queued");
      feedbackTask = feedback.result.task_uuid ?? null;
    }
  }
  return <WorkspacePage eyebrow={text("center")} title={text("title")} description={text("description")} actions={<>
    <button className="cc-button" disabled={refreshing} onClick={() => void refresh()}>{text(refreshing ? "refreshing" : "refresh")}</button>
    <Link className="cc-button" href="/publish/tasks">{text("task_list")}</Link>
  </>}>
    <Problem error={overview.error} text={text} />
    {overview.error && <button className="cc-button" disabled={overview.isFetching} onClick={() => void overview.refetch()}>{text("reload_overview")}</button>}
    {overview.isLoading && <p role="status" className="cc-muted py-4">{text("loading")}</p>}
    {overview.data && <div className="grid items-start gap-5 xl:grid-cols-2">{realtimeChannels.map(key => <ChannelCard key={key} data={overview.data.channels[key]} lang={lang} text={text} busy={action.isPending} unavailable={unavailable} control={request => void submit(request)} />)}</div>}
    <p className="cc-muted text-sm leading-6">{text("pause_hint")}</p>
    <section className="cc-panel" aria-labelledby="rt-manual-title">
      <div><h2 id="rt-manual-title" className="text-base font-semibold">{text("manual")}</h2><p className="cc-muted mt-1 text-sm">{text("manual_hint")}</p></div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1.5 text-sm"><span className="font-medium">{text("select_channel")}</span><select className="cc-input min-w-52" value={channel} disabled={action.isPending || !realtimeChannels.length} onChange={event => { setChannel(event.target.value as RealtimeChannel); setPendingPage(1); setRunPage(1); setFeedback(null); action.reset(); }}>{realtimeChannels.map(key => <option value={key} key={key}>{text(key)}</option>)}</select></label>
        <button className="cc-button cc-button-primary" disabled={!canPublish} onClick={() => void submit({ kind: "publish", channel })}>{text(action.isPending && action.variables?.kind === "publish" ? "submitting" : "publish")}</button>
        <button className="cc-button" disabled={!canPublish || !selected?.latest_failure} onClick={() => void submit({ kind: "retry", channel })}>{text(action.isPending && action.variables?.kind === "retry" ? "submitting" : "retry")}</button>
      </div>
      {selected && !selected.enabled && <p className="cc-muted text-sm">{text("disabled_hint")}</p>}
      {selected?.manual_enabled && selected.backlog.publishable.count === 0 && <p className="cc-muted text-sm">{text("no_publishable")}</p>}
      <p className="cc-muted text-xs leading-5">{text("retry_hint")}</p>
      <Problem error={action.error} text={text} />
      {conflictTaskUuid && <p className="text-sm"><TaskLink uuid={conflictTaskUuid}>{text("open_task")}</TaskLink></p>}
      {feedback && <div role={"error_code" in feedback.result && feedback.result.error_code ? "alert" : "status"} className="rounded-md border border-[var(--cc-border)] p-3 text-sm"><p><span className="font-semibold">{text(feedback.action.channel)}</span>: {feedbackMessage}</p>{feedbackTask && <p className="mt-2"><TaskLink uuid={feedbackTask}>{text("open_task")}</TaskLink></p>}</div>}
    </section>
    <section className="cc-panel" aria-labelledby="rt-pending-title" aria-busy={changes.isFetching}>
      <div className="flex flex-wrap items-end justify-between gap-3"><h2 id="rt-pending-title" className="text-base font-semibold">{text("changes_title")} · {text(channel)}</h2><label className="grid gap-1 text-sm"><span className="cc-muted text-xs">{text("eligibility")}</span><select className="cc-input" value={eligibility} onChange={event => { setEligibility(event.target.value as PendingEligibility); setPendingPage(1); }}><option value="all">{text("all_changes")}</option><option value="publishable">{text("publishable")}</option><option value="blocked">{text("blocked")}</option></select></label></div>
      <Problem error={changes.error} text={text} />
      {changes.error && <button className="cc-button" disabled={changes.isFetching} onClick={() => void changes.refetch()}>{text("reload_changes")}</button>}
      {changes.isLoading && <p role="status" className="cc-muted">{text("loading")}</p>}
      {changes.data && <ChangesTable items={changes.data.items} text={text} />}
      <Pagination data={changes.data} page={pendingPage} change={setPendingPage} fetching={changes.isFetching} text={text} />
    </section>
    <section className="cc-panel" aria-labelledby="rt-runs-title" aria-busy={runs.isFetching}>
      <div className="flex flex-wrap items-end justify-between gap-3"><h2 id="rt-runs-title" className="text-base font-semibold">{text("runs_title")} · {text(channel)}</h2><label className="grid gap-1 text-sm"><span className="cc-muted text-xs">{text("task_status")}</span><select className="cc-input" value={runStatus} onChange={event => { setRunStatus(event.target.value); setRunPage(1); }}><option value="">{text("all_tasks")}</option><option value="pending,queued,running,retrying">{text("active_tasks")}</option><option value="failed">{text("failed")}</option><option value="completed">{text("completed")}</option></select></label></div>
      <p className="cc-muted text-xs leading-5">{text("progress_hint")}</p>
      <Problem error={runs.error} text={text} />
      {runs.error && <button className="cc-button" disabled={runs.isFetching} onClick={() => void runs.refetch()}>{text("reload_runs")}</button>}
      {runs.isLoading && <p role="status" className="cc-muted">{text("loading")}</p>}
      {runs.data && <RunsTable items={runs.data.items} lang={lang} text={text} />}
      <Pagination data={runs.data} page={runPage} change={setRunPage} fetching={runs.isFetching} text={text} />
    </section>
  </WorkspacePage>;
}
