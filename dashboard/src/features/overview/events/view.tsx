"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Clock3, Database, GitCompareArrows, History, RefreshCw, RotateCcw, ShieldOff } from "lucide-react";

import { EmptyState, StatusBadge, WorkspacePage } from "@/shared/ui";
import { apiFetch } from "@/lib/api";

type EventRecord = {
  id: string;
  source: string;
  title: string;
  source_url: string;
  published_at?: string | null;
  disease_name?: string | null;
  geographies?: Array<{ name?: string }>;
  confidence: string;
  status?: string;
};

type SourceHealth = { status?: string; checked_at?: string; item_count?: number; error?: string; url?: string };
type RiskSignal = { id?: string; disease_name?: string; country_name?: string; detector_votes?: number; risk?: { score?: number; level?: string; confidence?: string; dimensions?: Record<string, number | null>; missing_dimensions?: string[] } };
type AlgorithmExecution = {
  series_count?: number;
  analyzed_count?: number;
  rejected_count?: number;
  methods?: Record<string, { executed_count?: number; completed_count?: number; alert_count?: number; unavailable_count?: number }>;
  source_usage?: Record<string, { series_count?: number; analyzed_count?: number; rejected_count?: number; candidate_count?: number; rejection_reasons?: Record<string, number> }>;
  official_events?: { available_count?: number; used_in_composite_risk_count?: number; event_evidence_only_count?: number };
};
type EventUsage = { id?: string; source?: string; title?: string; disease_name?: string; geographies?: Array<{ name?: string }>; usage?: { status?: string; matched_numeric_series_count?: number; official_concern_applied_count?: number; not_used_in_risk_reason?: string | null } };
type SituationHealth = {
  schema_version?: string;
  public_enabled: boolean;
  snapshot_id?: string | null;
  checked_at?: string | null;
  content_updated_at?: string | null;
  data_through?: string | null;
  quality_gate_status: string;
  quality_gate?: { failed_checks?: string[]; checks?: Array<{ id: string; passed: boolean }> };
  coverage?: { analyzed_series_count?: number; source_series_count?: number; candidate_signal_count?: number; rejected_reasons?: Record<string, number> };
  analysis_execution?: AlgorithmExecution;
  source_health?: Record<string, SourceHealth>;
  section_counts?: Record<string, number>;
  event_counts?: Record<string, number>;
  risk_signals?: RiskSignal[];
  event_usage?: EventUsage[];
  release?: { jobs?: Array<{ job_id?: string; daily_time?: string; timezone?: string; auto_after_crawls?: boolean; next_run_at?: string }> };
  history?: HistoryHealth;
  shadow_run?: { consecutive_quality_days?: number; target_days?: number; ready_for_review?: boolean; started_at?: string | null };
};

type HistoryHealth = {
  status: string;
  database?: string;
  isolated_from_primary?: boolean;
  size_bytes?: number | null;
  snapshot_count?: number;
  signal_count?: number;
  source_check_count?: number;
  audit_count?: number;
  latest_snapshot_id?: string | null;
  latest_checked_at?: string | null;
  error?: string;
  last_sync?: { run_id?: string; status?: string; started_at?: string; finished_at?: string | null; snapshots_seen?: number; snapshots_written?: number; error?: string | null } | null;
};

type SnapshotRow = {
  snapshot_id: string;
  snapshot_kind: string;
  period_key: string;
  checked_at: string;
  content_updated_at: string;
  data_through?: string | null;
  status: string;
  quality_gate_status: string;
  revision: number;
  supersedes_snapshot_id?: string | null;
  coverage?: { analyzed_series_count?: number; candidate_signal_count?: number };
  archived_at?: string;
  signal_count?: number;
};

type AuditRow = { audit_id: string; target_type: string; target_id: string; action: string; actor?: string | null; note?: string | null; happened_at: string };
type HistoryCompare = { summary: { added: number; removed: number; changed: number }; changes: Array<{ signal_id: string; section: string; status: string; disease_name?: string | null; country_name?: string | null; before?: { risk_score?: number | null; standard_z?: number | null } | null; after?: { risk_score?: number | null; standard_z?: number | null } | null }> };

const tone = (status?: string): "success" | "danger" | "warning" => ["passed", "fresh", "published", "healthy", "completed"].includes(status || "") ? "success" : ["failed", "quality_failed", "unavailable"].includes(status || "") ? "danger" : "warning";
const pretty = (value?: string) => String(value ?? "unknown").replaceAll("_", " ");
const when = (value?: string | null) => value ? new Date(value).toLocaleString() : "—";
const bytes = (value?: number | null) => value == null ? "—" : value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;

export default function EventsView() {
  const [health, setHealth] = useState<SituationHealth | null>(null);
  const [candidates, setCandidates] = useState<EventRecord[]>([]);
  const [published, setPublished] = useState<EventRecord[]>([]);
  const [snapshots, setSnapshots] = useState<SnapshotRow[]>([]);
  const [audits, setAudits] = useState<AuditRow[]>([]);
  const [historyKind, setHistoryKind] = useState("");
  const [historyDisease, setHistoryDisease] = useState("");
  const [historyCountry, setHistoryCountry] = useState("");
  const [compareLeft, setCompareLeft] = useState("");
  const [compareRight, setCompareRight] = useState("");
  const [comparison, setComparison] = useState<HistoryCompare | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextHealth, nextCandidates, nextPublished, nextSnapshots, nextAudits] = await Promise.all([
        apiFetch<SituationHealth>("/overview/events/health"),
        apiFetch<EventRecord[]>("/overview/events?status=candidate&page_size=50"),
        apiFetch<EventRecord[]>("/overview/events?status=published&page_size=30"),
        apiFetch<SnapshotRow[]>("/overview/events/history/snapshots?page_size=50"),
        apiFetch<AuditRow[]>("/overview/events/history/audit?page_size=20"),
      ]);
      setHealth(nextHealth); setCandidates(nextCandidates); setPublished(nextPublished); setSnapshots(nextSnapshots); setAudits(nextAudits);
      setCompareLeft((current) => current || nextSnapshots[1]?.snapshot_id || nextSnapshots[0]?.snapshot_id || "");
      setCompareRight((current) => current || nextSnapshots[0]?.snapshot_id || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load Situation Room monitor");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const queueRefresh = async () => {
    setBusy(true); setError(null);
    try { await apiFetch("/overview/events/rebuild", { method: "POST" }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to queue the release pipeline"); }
    finally { setBusy(false); }
  };

  const queueHistorySync = async () => {
    setBusy(true); setError(null);
    try { await apiFetch("/overview/events/history/sync", { method: "POST" }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to queue history reconciliation"); }
    finally { setBusy(false); }
  };

  const searchHistory = async () => {
    setBusy(true); setError(null); setComparison(null);
    const query = new URLSearchParams({ page_size: "100" });
    if (historyKind) query.set("snapshot_kind", historyKind);
    if (historyDisease.trim()) query.set("disease", historyDisease.trim());
    if (historyCountry.trim()) query.set("country", historyCountry.trim());
    try {
      const rows = await apiFetch<SnapshotRow[]>(`/overview/events/history/snapshots?${query.toString()}`);
      setSnapshots(rows);
      setCompareLeft(rows[1]?.snapshot_id || rows[0]?.snapshot_id || "");
      setCompareRight(rows[0]?.snapshot_id || "");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to search history"); }
    finally { setBusy(false); }
  };

  const compareHistory = async () => {
    if (!compareLeft || !compareRight || compareLeft === compareRight) return;
    setBusy(true); setError(null);
    try { setComparison(await apiFetch<HistoryCompare>(`/overview/events/history/compare?left=${encodeURIComponent(compareLeft)}&right=${encodeURIComponent(compareRight)}`)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to compare revisions"); }
    finally { setBusy(false); }
  };

  const decideEvent = async (item: EventRecord, action: "suppress" | "correct") => {
    const note = window.prompt(`${action === "suppress" ? "Suppress" : "Request correction for"} “${item.title}” — enter an audit note:`);
    if (!note) return;
    setBusy(true);
    try { await apiFetch(`/overview/events/${item.id}`, { method: "PATCH", body: JSON.stringify({ action, note, actor: "dashboard" }) }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save event decision"); }
    finally { setBusy(false); }
  };

  const decideSnapshot = async (snapshot: SnapshotRow, action: "suppress" | "correct" | "rollback") => {
    const note = window.prompt(`${pretty(action)} ${snapshot.snapshot_id} — enter an audit note:`);
    if (!note) return;
    setBusy(true);
    try { await apiFetch(`/overview/events/snapshots/${snapshot.snapshot_id}`, { method: "PATCH", body: JSON.stringify({ action, note, actor: "dashboard" }) }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to update snapshot"); }
    finally { setBusy(false); }
  };

  const releaseJob = health?.release?.jobs?.find((job) => job.job_id === "site-release");

  return (
    <WorkspacePage
      eyebrow="Overview"
      title="Events & Signals"
      description="Automatic-publication monitor for source health, statistical risk, revision history, and auditable post-publication corrections."
      actions={<div className="flex flex-wrap gap-2"><button disabled={busy} onClick={() => void queueHistorySync()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-3 text-sm font-semibold text-[#374151] hover:bg-[#F7F7F5] disabled:opacity-50"><Database className="h-4 w-4" />Sync history</button><button disabled={busy} onClick={() => void queueRefresh()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-3 text-sm font-semibold text-[#374151] hover:bg-[#F7F7F5] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />Queue release pipeline</button></div>}
    >
      {error ? <p className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</p> : null}

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="app-panel p-4"><div className="flex items-center justify-between"><span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Quality gate</span><StatusBadge tone={tone(health?.quality_gate_status)}>{pretty(health?.quality_gate_status)}</StatusBadge></div><p className="mt-3 text-2xl font-semibold text-[#1D1D1F]">{health?.coverage?.analyzed_series_count ?? "—"}</p><p className="text-xs text-[#6B7280]">eligible series · {health?.coverage?.candidate_signal_count ?? 0} candidates</p></div>
        <div className="app-panel p-4"><span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Data through</span><p className="mt-3 text-2xl font-semibold text-[#1D1D1F]">{health?.data_through ?? "—"}</p><p className="text-xs text-[#6B7280]">Checked {when(health?.checked_at)}</p></div>
        <div className="app-panel p-4"><span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Public switch</span><p className="mt-3 text-2xl font-semibold text-[#1D1D1F]">{health?.public_enabled ? "Enabled" : "Shadow"}</p><p className="text-xs text-[#6B7280]">{health?.public_enabled ? `Content updated ${when(health?.content_updated_at)}` : `${health?.shadow_run?.consecutive_quality_days ?? 0}/${health?.shadow_run?.target_days ?? 14} consecutive quality-passed days`}</p></div>
        <div className="app-panel p-4"><span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Daily release</span><p className="mt-3 text-2xl font-semibold text-[#1D1D1F]">{releaseJob?.daily_time ?? "02:00"} {releaseJob?.timezone ?? "UTC"}</p><p className="text-xs text-[#6B7280]">After-crawl trigger {releaseJob?.auto_after_crawls ? "on" : "off"}</p></div>
      </section>

      <section className="app-panel p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div><h2 className="flex items-center gap-2 text-base font-semibold text-[#1D1D1F]"><Database className="h-4 w-4" />Dedicated history database</h2><p className="mt-1 text-xs text-[#6B7280]">Revision payloads, flattened detector evidence, source checks, and operator actions are retained outside the runtime database.</p></div>
          <div className="flex items-center gap-2"><StatusBadge tone={tone(health?.history?.status)}>{pretty(health?.history?.status)}</StatusBadge><span className="font-mono text-xs text-[#4B5563]">{health?.history?.database ?? "not configured"}</span></div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="Snapshots" value={health?.history?.snapshot_count ?? "—"} />
          <Metric label="Signals" value={health?.history?.signal_count ?? "—"} />
          <Metric label="Source checks" value={health?.history?.source_check_count ?? "—"} />
          <Metric label="Audit entries" value={health?.history?.audit_count ?? "—"} />
          <Metric label="Database size" value={bytes(health?.history?.size_bytes)} />
        </div>
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[#6B7280]"><span>Isolated: {health?.history?.isolated_from_primary ? "yes" : "no"}</span><span>Latest archived check: {when(health?.history?.latest_checked_at)}</span><span>Last sync: {pretty(health?.history?.last_sync?.status)} · {when(health?.history?.last_sync?.finished_at)}</span></div>
        {health?.history?.error ? <p className="mt-2 rounded bg-rose-50 p-2 text-xs text-rose-700">{health.history.error}</p> : null}
      </section>

      <section className="app-panel p-4">
        <h2 className="text-base font-semibold text-[#1D1D1F]">Source health</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          {Object.entries(health?.source_health ?? {}).map(([source, item]) => <div key={source} className="rounded-md border border-[#E5E5E2] p-3"><div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold">{pretty(source)}</span><StatusBadge tone={tone(item.status)}>{pretty(item.status)}</StatusBadge></div><p className="mt-2 text-xs text-[#6B7280]">{item.item_count ?? 0} items · {when(item.checked_at)}</p>{item.error ? <p className="mt-1 line-clamp-2 text-xs text-rose-700" title={item.error}>{item.error}</p> : null}</div>)}
          {!Object.keys(health?.source_health ?? {}).length ? <p className="text-sm text-[#6B7280]">No source-health report in the latest offline shadow snapshot.</p> : null}
        </div>
      </section>

      <section className="app-panel overflow-hidden">
        <div className="border-b border-[#E5E5E2] p-4">
          <h2 className="text-base font-semibold text-[#1D1D1F]">Five-method execution evidence</h2>
          <p className="mt-1 text-xs text-[#6B7280]">Counts come from the complete per-series ledger, including baseline series that do not become public signals.</p>
        </div>
        <div className="grid gap-0 sm:grid-cols-2 xl:grid-cols-5">
          {Object.entries(health?.analysis_execution?.methods ?? {}).map(([method, result]) => <div key={method} className="border-b border-r border-[#E5E5E2] p-4"><p className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">{pretty(method)}</p><p className="mt-2 text-2xl font-semibold text-[#1D1D1F]">{result.executed_count ?? 0}</p><p className="mt-1 text-xs text-[#6B7280]">executed · {result.alert_count ?? 0} alerts · {result.unavailable_count ?? 0} unavailable</p></div>)}
          {!Object.keys(health?.analysis_execution?.methods ?? {}).length ? <p className="col-span-full p-4 text-sm text-[#6B7280]">No algorithm execution ledger in this snapshot.</p> : null}
        </div>
        <div className="grid gap-4 p-4 xl:grid-cols-2">
          <div><h3 className="text-sm font-semibold text-[#1D1D1F]">Source utilization</h3><div className="mt-2 max-h-64 overflow-auto rounded border border-[#E5E5E2]">{Object.entries(health?.analysis_execution?.source_usage ?? {}).map(([source, usage]) => <div key={source} className="grid grid-cols-[1fr_auto] gap-3 border-b border-[#E5E5E2] p-2 text-xs last:border-b-0"><div><p className="font-mono text-[#374151]">{source}</p>{Object.keys(usage.rejection_reasons ?? {}).length ? <p className="mt-1 text-[#9A3412]">{Object.entries(usage.rejection_reasons ?? {}).map(([reason, count]) => `${pretty(reason)} ${count}`).join(" · ")}</p> : null}</div><p className="text-right text-[#6B7280]">{usage.analyzed_count ?? 0}/{usage.series_count ?? 0} analyzed<br />{usage.candidate_count ?? 0} candidates</p></div>)}</div></div>
          <div><h3 className="text-sm font-semibold text-[#1D1D1F]">Official-event use</h3><p className="mt-2 text-xs text-[#6B7280]">{health?.analysis_execution?.official_events?.available_count ?? 0} eligible official events · {health?.analysis_execution?.official_events?.used_in_composite_risk_count ?? 0} used in matching composite risk · {health?.analysis_execution?.official_events?.event_evidence_only_count ?? 0} event evidence only</p><div className="mt-3 max-h-64 overflow-auto rounded border border-[#E5E5E2]">{health?.event_usage?.map((event) => <div key={event.id} className="border-b border-[#E5E5E2] p-2 text-xs last:border-b-0"><div className="flex items-center justify-between gap-2"><p className="font-semibold text-[#374151]">{event.disease_name || event.title}</p><StatusBadge tone={event.usage?.status === "used_in_composite_risk" ? "success" : "info"}>{pretty(event.usage?.status)}</StatusBadge></div><p className="mt-1 text-[#6B7280]">{event.source} · {event.usage?.matched_numeric_series_count ?? 0} exact numerical matches</p>{event.usage?.not_used_in_risk_reason ? <p className="mt-1 text-amber-700">{event.usage.not_used_in_risk_reason}</p> : null}</div>) || <p className="p-3 text-xs text-[#6B7280]">No current official events.</p>}</div></div>
        </div>
      </section>

      <section className="app-panel overflow-hidden">
        <div className="border-b border-[#E5E5E2] p-4"><h2 className="text-base font-semibold text-[#1D1D1F]">Latest risk decomposition</h2><p className="mt-1 text-xs text-[#6B7280]">Missing dimensions reduce confidence rather than being scored as zero.</p></div>
        {health?.risk_signals?.length ? health.risk_signals.map((signal) => <article key={signal.id} className="grid gap-3 border-b border-[#E5E5E2] p-4 last:border-b-0 lg:grid-cols-[1fr_auto]"><div><p className="font-semibold text-[#1D1D1F]">{signal.disease_name} · {signal.country_name}</p><div className="mt-2 flex flex-wrap gap-2">{Object.entries(signal.risk?.dimensions ?? {}).map(([name, value]) => <span key={name} className="rounded border border-[#E5E5E2] px-2 py-1 text-xs text-[#4B5563]">{pretty(name)}: {value == null ? "missing" : value.toFixed(1)}</span>)}</div></div><div className="text-right"><p className="font-mono text-lg font-semibold">{signal.risk?.score?.toFixed(1) ?? "—"}</p><p className="text-xs capitalize text-[#6B7280]">{pretty(signal.risk?.level)} · {signal.risk?.confidence} confidence · {signal.detector_votes ?? 0} votes</p></div></article>) : <EmptyState title="No increasing signals" description="The latest snapshot contains no risk-qualified increasing signal." className="min-h-40" />}
      </section>

      <section className="app-panel overflow-hidden">
        <div className="border-b border-[#E5E5E2] p-4">
          <h2 className="flex items-center gap-2 text-base font-semibold text-[#1D1D1F]"><History className="h-4 w-4" />Snapshot history explorer</h2><p className="mt-1 text-xs text-[#6B7280]">Search the isolated history store, compare detector/risk outputs, or restore a gate-passed revision with an audit note.</p>
          <div className="mt-3 grid gap-2 md:grid-cols-[140px_1fr_1fr_auto]">
            <select value={historyKind} onChange={(event) => setHistoryKind(event.target.value)} className="h-9 rounded-md border border-[#D9D9D6] bg-white px-2 text-sm"><option value="">All periods</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select>
            <input value={historyDisease} onChange={(event) => setHistoryDisease(event.target.value)} placeholder="Disease ID or name" className="h-9 rounded-md border border-[#D9D9D6] px-3 text-sm" />
            <input value={historyCountry} onChange={(event) => setHistoryCountry(event.target.value)} placeholder="Country code or name" className="h-9 rounded-md border border-[#D9D9D6] px-3 text-sm" />
            <button disabled={busy} onClick={() => void searchHistory()} className="h-9 rounded-md bg-[#1D1D1F] px-4 text-sm font-semibold text-white disabled:opacity-50">Search history</button>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-[1fr_1fr_auto]">
            <select value={compareLeft} onChange={(event) => setCompareLeft(event.target.value)} className="h-9 min-w-0 rounded-md border border-[#D9D9D6] bg-white px-2 font-mono text-xs">{snapshots.map((item) => <option key={`left-${item.snapshot_id}`} value={item.snapshot_id}>{item.snapshot_id}</option>)}</select>
            <select value={compareRight} onChange={(event) => setCompareRight(event.target.value)} className="h-9 min-w-0 rounded-md border border-[#D9D9D6] bg-white px-2 font-mono text-xs">{snapshots.map((item) => <option key={`right-${item.snapshot_id}`} value={item.snapshot_id}>{item.snapshot_id}</option>)}</select>
            <button disabled={busy || !compareLeft || !compareRight || compareLeft === compareRight} onClick={() => void compareHistory()} className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-[#D9D9D6] px-3 text-sm font-semibold disabled:opacity-40"><GitCompareArrows className="h-4 w-4" />Compare</button>
          </div>
          {comparison ? <div className="mt-3 rounded-md border border-[#E5E5E2] bg-[#F7F7F5] p-3"><p className="text-sm font-semibold">Revision difference · {comparison.summary.added} added · {comparison.summary.removed} removed · {comparison.summary.changed} changed</p><div className="mt-2 max-h-48 space-y-1 overflow-auto">{comparison.changes.length ? comparison.changes.map((item) => <p key={`${item.section}:${item.signal_id}`} className="text-xs text-[#4B5563]"><span className="font-semibold">{pretty(item.status)}</span> · {item.disease_name || item.signal_id}{item.country_name ? ` · ${item.country_name}` : ""} · z {item.before?.standard_z?.toFixed(2) ?? "—"} → {item.after?.standard_z?.toFixed(2) ?? "—"} · risk {item.before?.risk_score?.toFixed(1) ?? "—"} → {item.after?.risk_score?.toFixed(1) ?? "—"}</p>) : <p className="text-xs text-[#6B7280]">No signal-level differences.</p>}</div></div> : null}
        </div>
        {snapshots.length ? snapshots.map((snapshot) => <article key={snapshot.snapshot_id} className="flex flex-col gap-3 border-b border-[#E5E5E2] p-4 last:border-b-0 lg:flex-row lg:items-center lg:justify-between"><div><div className="flex flex-wrap items-center gap-2"><StatusBadge tone={tone(snapshot.quality_gate_status)}>{pretty(snapshot.quality_gate_status)}</StatusBadge><StatusBadge tone={tone(snapshot.status)}>{pretty(snapshot.status)}</StatusBadge><span className="text-sm font-semibold">{snapshot.snapshot_kind} · {snapshot.period_key} · r{snapshot.revision}</span></div><p className="mt-1 text-xs text-[#6B7280]">{snapshot.coverage?.analyzed_series_count ?? 0} analyzed · data through {snapshot.data_through ?? "—"} · checked {when(snapshot.checked_at)}</p></div><div className="flex gap-2"><button disabled={busy} onClick={() => void decideSnapshot(snapshot, "correct")} className="inline-flex h-8 items-center gap-1 rounded border border-[#D9D9D6] px-2 text-xs font-semibold"><AlertTriangle className="h-3.5 w-3.5" />Correct</button><button disabled={busy} onClick={() => void decideSnapshot(snapshot, "suppress")} className="inline-flex h-8 items-center gap-1 rounded border border-[#D9D9D6] px-2 text-xs font-semibold"><ShieldOff className="h-3.5 w-3.5" />Suppress</button><button disabled={busy || snapshot.quality_gate_status !== "passed"} onClick={() => void decideSnapshot(snapshot, "rollback")} className="inline-flex h-8 items-center gap-1 rounded border border-[#D9D9D6] px-2 text-xs font-semibold disabled:opacity-40"><RotateCcw className="h-3.5 w-3.5" />Restore</button></div></article>) : <EmptyState title="No snapshots" description="Snapshot runs will appear here." className="min-h-48" />}
      </section>

      <section className="app-panel overflow-hidden">
        <div className="border-b border-[#E5E5E2] p-4"><h2 className="text-base font-semibold text-[#1D1D1F]">History audit trail</h2><p className="mt-1 text-xs text-[#6B7280]">Append-only operator decisions stored in the dedicated database.</p></div>
        {audits.length ? audits.map((item) => <article key={item.audit_id} className="grid gap-1 border-b border-[#E5E5E2] p-4 last:border-b-0 md:grid-cols-[160px_1fr_auto]"><div><StatusBadge tone="warning">{pretty(item.action)}</StatusBadge></div><div><p className="font-mono text-xs text-[#374151]">{item.target_type} · {item.target_id}</p><p className="mt-1 text-sm text-[#4B5563]">{item.note || "No note"}</p></div><p className="text-xs text-[#6B7280]">{item.actor || "unknown"} · {when(item.happened_at)}</p></article>) : <EmptyState title="No audit events" description="Suppress, correction, and restore decisions will be retained here." className="min-h-40" />}
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        <div className="app-panel overflow-hidden"><div className="border-b border-[#E5E5E2] p-4"><h2 className="flex items-center gap-2 text-base font-semibold"><Clock3 className="h-4 w-4" />Ambiguous internal candidates</h2><p className="mt-1 text-xs text-[#6B7280]">These do not block the daily snapshot and are never auto-published.</p></div>{candidates.length ? candidates.map((item) => <EventRow key={item.id} item={item} />) : <EmptyState title="No ambiguous candidates" description="All current official items were mapped or filtered deterministically." className="min-h-48" />}</div>
        <div className="app-panel overflow-hidden"><div className="border-b border-[#E5E5E2] p-4"><h2 className="flex items-center gap-2 text-base font-semibold"><CheckCircle2 className="h-4 w-4" />Auto-published official events</h2><p className="mt-1 text-xs text-[#6B7280]">Post-publication suppression and correction remain auditable.</p></div>{published.length ? published.map((item) => <EventRow key={item.id} item={item} actions={<div className="mt-2 flex gap-2"><button disabled={busy} onClick={() => void decideEvent(item, "correct")} className="text-xs font-semibold text-amber-700">Request correction</button><button disabled={busy} onClick={() => void decideEvent(item, "suppress")} className="text-xs font-semibold text-rose-700">Suppress</button></div>} />) : <EmptyState title="No auto-published events" description="Exact high-confidence official events will appear here." className="min-h-48" />}</div>
      </section>
    </WorkspacePage>
  );
}

function EventRow({ item, actions }: { item: EventRecord; actions?: ReactNode }) {
  return <article className="border-b border-[#E5E5E2] p-4 last:border-b-0"><div className="mb-1.5 flex flex-wrap items-center gap-2"><StatusBadge tone="info">{item.source}</StatusBadge><span className="text-xs text-[#6B7280]">{item.published_at || "Undated"} · {item.confidence}</span></div><a href={item.source_url} target="_blank" rel="noreferrer" className="font-semibold text-[#1D1D1F] hover:text-[#C2410C] hover:underline">{item.title}</a><div className="mt-1 text-sm text-[#6B7280]">{item.disease_name || "Disease mapping required"}{item.geographies?.length ? ` · ${item.geographies.map((geography) => geography.name).join(", ")}` : " · Geography mapping required"}</div>{actions}</article>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded-md border border-[#E5E5E2] p-3"><p className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">{label}</p><p className="mt-1 text-lg font-semibold text-[#1D1D1F]">{value}</p></div>;
}
