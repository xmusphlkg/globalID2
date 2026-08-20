"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  GitCompareArrows,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";

import type { components } from "@/generated/api";
import { apiFetch, apiFetchWithMeta } from "@/lib/api";
import { EmptyState, StatusBadge, WorkspacePage } from "@/shared/ui";

type SituationReportV3 = components["schemas"]["SituationReportV3"];
type SituationSignalV3 = components["schemas"]["SituationSignalV3"];
type Tab = "overview" | "runs" | "signals" | "sources" | "events" | "reports" | "audit";
type Tone = "success" | "danger" | "warning" | "info";

type RunSummary = {
  run_id: string;
  checked_at: string;
  status: string;
  method_version: string;
  timings?: Record<string, number>;
  coverage?: Record<string, unknown>;
  quality_gate?: Record<string, unknown>;
  ledger_summary?: Record<string, number>;
  model_failures?: Record<string, number>;
};

type OverviewResponse = {
  schema_version: string;
  publication: {
    channel: string;
    report_id?: string | null;
    previous_report_id?: string | null;
    published_at?: string | null;
  };
  report?: SituationReportV3 | null;
  latest_run?: RunSummary | null;
};

type RunRow = RunSummary & {
  id: number;
  input_hash: string;
  config_hash: string;
  error?: string | null;
};

type SignalRow = {
  id: number;
  run_id: string;
  signal_id: string;
  status: string;
  disease_id?: string | null;
  country_code?: string | null;
  canonical_geography_key?: string | null;
  series_code?: string | null;
  source_system?: string | null;
  metric_type?: string | null;
  cadence?: string | null;
  raw_p_value?: number | null;
  q_value?: number | null;
  anomaly_state?: string | null;
  review_priority?: string | null;
  rejection_reason?: string | null;
  payload: unknown;
};

type ReportRow = {
  report_id: string;
  report_kind: string;
  period_key: string;
  period_start: string;
  period_end: string;
  as_of: string;
  revision: number;
  method_version: string;
  status: string;
  quality_gate?: Record<string, unknown>;
  coverage?: Record<string, unknown>;
  summary?: Record<string, number>;
  sources?: SituationReportV3["sources"];
  run_ids?: string[];
};

type EventRow = {
  cluster_id: string;
  disease_id: string;
  disease_name: string;
  geographies?: Array<Record<string, string>>;
  first_published_at: string;
  last_published_at: string;
  source_state: string;
  review_state: string;
  corrected_payload?: Record<string, unknown>;
  updates?: Array<{ update_id: string; source: string; title: string; source_url: string; published_at: string }>;
};

type AuditRow = {
  decision_id: string;
  target_type: string;
  target_id: string;
  action: string;
  actor?: string | null;
  note: string;
  payload?: Record<string, unknown>;
  created_at: string;
};

type CompareResponse = {
  from_report: string;
  to_report: string;
  signals: { added: string[]; removed: string[]; changed: string[] };
  coverage: { before: unknown; after: unknown };
  sources: { before: unknown; after: unknown };
  method: { before: unknown; after: unknown };
  quality_gate: { before: unknown; after: unknown };
};

type Decision = {
  kind: "event" | "signal" | "rollback";
  targetId: string;
  title: string;
  action: "publish" | "verify" | "reject" | "suppress" | "correct" | "merge" | "rollback";
};

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "runs", label: "Runs" },
  { id: "signals", label: "Signals" },
  { id: "sources", label: "Sources" },
  { id: "events", label: "Events" },
  { id: "reports", label: "Reports" },
  { id: "audit", label: "Audit" },
];

const pretty = (value?: string | null) => String(value ?? "unknown").replaceAll("_", " ");
const when = (value?: string | null) => value ? new Date(value).toLocaleString() : "—";
const fixed = (value?: number | null, digits = 3) => typeof value === "number" ? value.toFixed(digits) : "—";
const tone = (status?: string | null): Tone => {
  if (["passed", "fresh", "published", "completed", "strong"].includes(status || "")) return "success";
  if (["failed", "gate_failed", "stale", "suppressed"].includes(status || "")) return "danger";
  if (["alert", "partial", "degraded", "corrected", "merged"].includes(status || "")) return "warning";
  return "info";
};

function isSignalPayload(value: unknown): value is SituationSignalV3 {
  return Boolean(value && typeof value === "object" && "identity" in value && "anomaly" in value);
}

export default function EventsView() {
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [signals, setSignals] = useState<SignalRow[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [audits, setAudits] = useState<AuditRow[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionPayload, setDecisionPayload] = useState("");
  const [compareLeft, setCompareLeft] = useState("");
  const [compareRight, setCompareRight] = useState("");
  const [comparison, setComparison] = useState<CompareResponse | null>(null);
  const pageSize = 50;

  const readUrlState = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedTab = params.get("tab") as Tab | null;
    if (requestedTab && tabs.some(tab => tab.id === requestedTab)) setActiveTab(requestedTab);
    setPage(Math.max(1, Number(params.get("page") || 1)));
    setQuery(params.get("q") || "");
    setStateFilter(params.get("state") || "");
  }, []);

  useEffect(() => {
    readUrlState();
    window.addEventListener("popstate", readUrlState);
    return () => window.removeEventListener("popstate", readUrlState);
  }, [readUrlState]);

  const writeUrlState = useCallback((next: { tab?: Tab; page?: number; q?: string; state?: string }) => {
    const params = new URLSearchParams(window.location.search);
    if (next.tab) params.set("tab", next.tab);
    if (next.page !== undefined) params.set("page", String(next.page));
    if (next.q !== undefined) next.q ? params.set("q", next.q) : params.delete("q");
    if (next.state !== undefined) next.state ? params.set("state", next.state) : params.delete("state");
    window.history.pushState(null, "", `?${params.toString()}`);
  }, []);

  const loadOverview = useCallback(async () => {
    const next = await apiFetch<OverviewResponse>("/situation/v3/overview");
    setOverview(next);
  }, []);

  useEffect(() => {
    void loadOverview().catch(cause => setError(cause instanceof Error ? cause.message : "Unable to load Situation v3"));
    const timer = window.setInterval(() => void loadOverview(), 15_000);
    return () => window.clearInterval(timer);
  }, [loadOverview]);

  const loadTab = useCallback(async () => {
    setError(null);
    if (activeTab === "overview" || activeTab === "sources") return;
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (stateFilter) {
      if (activeTab === "signals") params.set("state", stateFilter);
      if (activeTab === "runs") params.set("status", stateFilter);
      if (activeTab === "events") params.set("review_state", stateFilter);
      if (activeTab === "reports") params.set("kind", stateFilter);
      if (activeTab === "audit") params.set("target_type", stateFilter);
    }
    if (activeTab === "signals" && query.trim()) params.set("q", query.trim());
    const endpoint = `/situation/v3/${activeTab}?${params.toString()}`;
    try {
      if (activeTab === "runs") {
        const result = await apiFetchWithMeta<RunRow[]>(endpoint); setRuns(result.data); setTotal(Number(result.headers.get("X-Total-Count") || result.data.length));
      } else if (activeTab === "signals") {
        const result = await apiFetchWithMeta<SignalRow[]>(endpoint); setSignals(result.data); setTotal(Number(result.headers.get("X-Total-Count") || result.data.length));
      } else if (activeTab === "events") {
        const result = await apiFetchWithMeta<EventRow[]>(endpoint); setEvents(result.data); setTotal(Number(result.headers.get("X-Total-Count") || result.data.length));
      } else if (activeTab === "reports") {
        const result = await apiFetchWithMeta<ReportRow[]>(endpoint); setReports(result.data); setTotal(Number(result.headers.get("X-Total-Count") || result.data.length));
        setCompareLeft(current => current || result.data[1]?.report_id || result.data[0]?.report_id || "");
        setCompareRight(current => current || result.data[0]?.report_id || "");
      } else if (activeTab === "audit") {
        const result = await apiFetchWithMeta<AuditRow[]>(endpoint); setAudits(result.data); setTotal(Number(result.headers.get("X-Total-Count") || result.data.length));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Unable to load ${activeTab}`);
    }
  }, [activeTab, page, query, stateFilter]);

  useEffect(() => { void loadTab(); }, [loadTab]);

  const chooseTab = (tab: Tab) => {
    setActiveTab(tab); setPage(1); setQuery(""); setStateFilter(""); setTotal(0); setComparison(null);
    writeUrlState({ tab, page: 1, q: "", state: "" });
  };

  const applyFilters = () => {
    setPage(1);
    writeUrlState({ page: 1, q: query, state: stateFilter });
    void loadTab();
  };

  const queueRefresh = async () => {
    setBusy(true); setError(null);
    try {
      await apiFetch("/overview/events/rebuild", { method: "POST" });
      await loadOverview();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to queue Situation v3 refresh");
    } finally { setBusy(false); }
  };

  const submitDecision = async () => {
    if (!decision || decisionNote.trim().length < 3) return;
    setBusy(true); setError(null);
    try {
      let payload: Record<string, unknown> = {};
      if (decisionPayload.trim()) {
        if (decision.action === "merge") {
          payload = { merged_into_cluster_id: decisionPayload.trim() };
        } else {
          const parsed: unknown = JSON.parse(decisionPayload);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Decision payload must be a JSON object");
          }
          payload = parsed as Record<string, unknown>;
        }
      }
      if (decision.kind === "rollback") {
        await apiFetch("/situation/v3/publication/latest/rollback", {
          method: "POST",
          body: JSON.stringify({ report_id: decision.targetId, note: decisionNote.trim(), actor: "dashboard" }),
        });
      } else {
        await apiFetch(`/situation/v3/review/${decision.kind}/${encodeURIComponent(decision.targetId)}`, {
          method: "POST",
          body: JSON.stringify({ action: decision.action, note: decisionNote.trim(), actor: "dashboard", payload }),
        });
      }
      setDecision(null); setDecisionNote(""); setDecisionPayload("");
      await Promise.all([loadOverview(), loadTab()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to save review decision");
    } finally { setBusy(false); }
  };

  const compareReports = async () => {
    if (!compareLeft || !compareRight || compareLeft === compareRight) return;
    setBusy(true); setError(null);
    try {
      const params = new URLSearchParams({ from_report: compareLeft, to_report: compareRight });
      setComparison(await apiFetch<CompareResponse>(`/situation/v3/reports/compare?${params.toString()}`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to compare reports");
    } finally { setBusy(false); }
  };

  const duplicateIdentities = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of signals) {
      if (!isSignalPayload(row.payload)) continue;
      const identity = row.payload.identity;
      const key = `${identity.series_code}|${identity.canonical_geography_key}|${identity.dimension_key}`;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts.values()].filter(count => count > 1).length;
  }, [signals]);

  const qDistribution = useMemo(() => ({
    strong: signals.filter(row => typeof row.q_value === "number" && row.q_value <= 0.01).length,
    alert: signals.filter(row => typeof row.q_value === "number" && row.q_value > 0.01 && row.q_value <= 0.05).length,
    baseline: signals.filter(row => typeof row.q_value === "number" && row.q_value > 0.05).length,
    unavailable: signals.filter(row => row.q_value == null).length,
  }), [signals]);

  const report = overview?.report;
  const latestRun = overview?.latest_run;

  return (
    <WorkspacePage
      eyebrow="Situation Room v3"
      title="Signals & publication"
      description="Immutable analysis runs, one source-native signal identity, attributable risk semantics, period reports, and audited publication decisions."
      actions={<button disabled={busy} onClick={() => void queueRefresh()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-3 text-sm font-semibold text-[#374151] hover:bg-[#F7F7F5] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />Queue refresh</button>}
    >
      {error ? <p role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</p> : null}

      <nav aria-label="Situation v3 workspaces" className="flex flex-wrap gap-1 border-b border-[#D9D9D6]">
        {tabs.map(tab => <button key={tab.id} onClick={() => chooseTab(tab.id)} className={`border-b-2 px-3 py-2 text-sm font-semibold ${activeTab === tab.id ? "border-[#0F766E] text-[#0F766E]" : "border-transparent text-[#6B7280] hover:text-[#1D1D1F]"}`}>{tab.label}</button>)}
      </nav>

      {activeTab === "overview" ? <OverviewPanel overview={overview} /> : null}

      {activeTab === "sources" ? <SourcesPanel report={report} /> : null}

      {activeTab !== "overview" && activeTab !== "sources" ? (
        <section className="app-panel overflow-hidden">
          <div className="flex flex-col gap-3 border-b border-[#E5E5E2] p-4 lg:flex-row lg:items-end lg:justify-between">
            <div><h2 className="text-base font-semibold capitalize text-[#1D1D1F]">{activeTab}</h2><p className="mt-1 text-xs text-[#6B7280]">Server-filtered results · {total} records</p></div>
            <div className="flex flex-wrap items-end gap-2">
              {activeTab === "signals" ? <label className="grid gap-1 text-xs text-[#6B7280]"><span>Search</span><div className="relative"><Search className="absolute left-2 top-2.5 h-3.5 w-3.5" /><input value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === "Enter") applyFilters(); }} className="h-9 w-64 rounded-md border border-[#D9D9D6] pl-8 pr-2 text-sm" placeholder="Disease, series, geography" /></div></label> : null}
              <label htmlFor="events-state-filter" className="grid gap-1 text-xs text-[#6B7280]"><span>Filter</span><select id="events-state-filter" name="events-state-filter" value={stateFilter} onChange={event => setStateFilter(event.target.value)} className="h-9 rounded-md border border-[#D9D9D6] bg-white px-2 text-sm" aria-describedby="events-filter-help"><option value="">All</option>{filterOptions(activeTab).map(option => <option key={option} value={option}>{pretty(option)}</option>)}</select><span id="events-filter-help" className="sr-only">Filter the current {pretty(activeTab)} view by status or type.</span></label>
              <button onClick={applyFilters} className="h-9 rounded-md bg-[#1D1D1F] px-3 text-sm font-semibold text-white">Apply</button>
            </div>
          </div>

          {activeTab === "runs" ? <RunsTable rows={runs} /> : null}
          {activeTab === "signals" ? <SignalsTable rows={signals} distribution={qDistribution} duplicates={duplicateIdentities} onDecision={(next) => { setDecision(next); setDecisionNote(""); setDecisionPayload(""); }} /> : null}
          {activeTab === "events" ? <EventsTable rows={events} onDecision={(next) => { setDecision(next); setDecisionNote(""); setDecisionPayload(""); }} /> : null}
          {activeTab === "reports" ? <ReportsTable rows={reports} compareLeft={compareLeft} compareRight={compareRight} comparison={comparison} setCompareLeft={setCompareLeft} setCompareRight={setCompareRight} onCompare={() => void compareReports()} onRollback={row => { setDecision({ kind: "rollback", targetId: row.report_id, title: `${row.report_kind} ${row.period_key} r${row.revision}`, action: "rollback" }); setDecisionNote(""); setDecisionPayload(""); }} busy={busy} /> : null}
          {activeTab === "audit" ? <AuditTable rows={audits} /> : null}
          <Pagination page={page} pageSize={pageSize} total={total} onPage={next => { setPage(next); writeUrlState({ page: next }); }} />
        </section>
      ) : null}

      {decision ? <DecisionDialog decision={decision} note={decisionNote} payload={decisionPayload} busy={busy} setNote={setDecisionNote} setPayload={setDecisionPayload} onCancel={() => { setDecision(null); setDecisionNote(""); setDecisionPayload(""); }} onSubmit={() => void submitDecision()} /> : null}

      <p className="text-xs text-[#6B7280]">Live run status refreshes every 15 seconds. Latest run: <span className="font-mono">{latestRun?.run_id || "none"}</span>.</p>
    </WorkspacePage>
  );
}

function OverviewPanel({ overview }: { overview: OverviewResponse | null }) {
  const report = overview?.report;
  const run = overview?.latest_run;
  const timings = run?.timings || {};
  return <>
    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <Metric label="Publication" value={overview?.publication.report_id || "No pointer"} detail={when(overview?.publication.published_at)} toneValue={report?.quality_gate.status} />
      <Metric label="Unique signals" value={report?.summary.unique_signal_count ?? "—"} detail={`${report?.summary.strong_count ?? 0} strong · ${report?.summary.alert_count ?? 0} alerts`} />
      <Metric label="Coverage" value={report ? `${report.coverage.modeled_series_count}/${report.coverage.evaluated_series_count}` : "—"} detail={`${report?.coverage.rejected_series_count ?? 0} rejected`} />
      <Metric label="Data currency" value={report?.data_currency.latest_data_through || "—"} detail={`earliest ${report?.data_currency.earliest_data_through || "—"}`} />
    </section>
    <section className="app-panel p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between"><div><h2 className="flex items-center gap-2 text-base font-semibold"><Activity className="h-4 w-4" />Latest immutable run</h2><p className="mt-1 font-mono text-xs text-[#6B7280]">{run?.run_id || "No v3 run"}</p></div><StatusBadge tone={tone(run?.status)}>{pretty(run?.status)}</StatusBadge></div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <MetricMini label="DB read" value={`${fixed(timings.series_fetch_seconds, 2)}s`} />
        <MetricMini label="Model" value={`${fixed(timings.model_seconds, 2)}s`} />
        <MetricMini label="Acquisition" value={`${fixed(timings.source_acquisition_seconds, 2)}s`} />
        <MetricMini label="Modeled" value={run?.ledger_summary?.modeled ?? "—"} />
        <MetricMini label="Rejected" value={run?.ledger_summary?.rejected ?? "—"} />
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2"><JsonSummary title="Quality gate" value={run?.quality_gate} /><JsonSummary title="Model failures" value={run?.model_failures} /></div>
    </section>
    <section className="app-panel p-4"><h2 className="flex items-center gap-2 text-base font-semibold"><ShieldCheck className="h-4 w-4" />Automation and risk semantics</h2><p className="mt-2 text-sm text-[#4B5563]">Collection, modeling, evidence linking, deduplication, and candidate queueing are automatic. Calibration does not yet support unattended statistical publication, so candidates remain private until independently verified; rare-count and fallback candidates always require review. Public-health risk remains <b>not assessed</b> unless an official agency or audited expert assessment is attributable.</p><p className="mt-2 font-mono text-xs text-[#6B7280]">Method {report?.method.version || "—"} · config {report?.method.config_hash?.slice(0, 16) || "—"} · revision {report?.report.revision ?? "—"}</p></section>
  </>;
}

function SourcesPanel({ report }: { report?: SituationReportV3 | null }) {
  const sources = report?.sources || [];
  const currency = report?.data_currency.by_source || [];
  return <section className="grid gap-4 lg:grid-cols-2">
    <div className="app-panel overflow-hidden"><div className="border-b border-[#E5E5E2] p-4"><h2 className="text-base font-semibold">Adapter health</h2><p className="mt-1 text-xs text-[#6B7280]">Timeout, retry, last success, and current item count remain source-specific.</p></div>{sources.length ? sources.map(source => <article key={source.source_id} className="border-b border-[#E5E5E2] p-4 last:border-b-0"><div className="flex items-center justify-between gap-2"><div><p className="font-semibold">{source.label}</p><p className="font-mono text-xs text-[#6B7280]">{source.source_id}</p></div><StatusBadge tone={tone(source.status)}>{pretty(source.status)}</StatusBadge></div><p className="mt-2 text-xs text-[#6B7280]">{source.item_count ?? 0} items · checked {when(source.checked_at)} · last success {when(source.last_success_at)}</p>{source.error ? <p className="mt-2 text-xs text-rose-700">{source.error}</p> : null}</article>) : <EmptyState title="No source status" description="Run a full Situation v3 acquisition." className="min-h-40" />}</div>
    <div className="app-panel overflow-hidden"><div className="border-b border-[#E5E5E2] p-4"><h2 className="text-base font-semibold">Freshness by source and cadence</h2><p className="mt-1 text-xs text-[#6B7280]">No single maximum date can hide an older source.</p></div>{currency.length ? currency.map((slice, index) => <article key={`${slice.source_system}:${slice.cadence}:${index}`} className="grid gap-2 border-b border-[#E5E5E2] p-4 last:border-b-0 sm:grid-cols-[1fr_auto]"><div><p className="font-mono text-xs font-semibold">{slice.source_system}</p><p className="mt-1 text-xs text-[#6B7280]">{slice.cadence || "mixed"} · {slice.analyzed_series_count} series</p></div><div className="text-right"><StatusBadge tone={tone(slice.status)}>{pretty(slice.status)}</StatusBadge><p className="mt-1 text-xs text-[#6B7280]">{slice.earliest_data_through || "—"} → {slice.latest_data_through || "—"}</p></div></article>) : <EmptyState title="No currency slices" description="No eligible series in the latest report." className="min-h-40" />}</div>
  </section>;
}

function RunsTable({ rows }: { rows: RunRow[] }) {
  return rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[920px] text-left text-xs"><thead><tr className="border-b border-[#E5E5E2] text-[#6B7280]"><Th>Run</Th><Th>Status</Th><Th>Checked</Th><Th>Method</Th><Th>DB</Th><Th>Model</Th><Th>Modeled</Th><Th>Rejected</Th></tr></thead><tbody>{rows.map(row => <tr key={row.run_id} className="border-b border-[#E5E5E2]"><Td mono>{row.run_id}</Td><Td><StatusBadge tone={tone(row.status)}>{pretty(row.status)}</StatusBadge></Td><Td>{when(row.checked_at)}</Td><Td mono>{row.method_version}</Td><Td>{fixed(row.timings?.series_fetch_seconds, 2)}s</Td><Td>{fixed(row.timings?.model_seconds, 2)}s</Td><Td>{row.ledger_summary?.modeled ?? "—"}</Td><Td>{row.ledger_summary?.rejected ?? "—"}</Td></tr>)}</tbody></table></div> : <EmptyState title="No v3 runs" description="Immutable analysis runs will appear here." className="min-h-48" />;
}

function SignalsTable({ rows, distribution, duplicates, onDecision }: { rows: SignalRow[]; distribution: Record<string, number>; duplicates: number; onDecision: (decision: Decision) => void }) {
  return <>
    <div className="grid gap-2 border-b border-[#E5E5E2] p-4 sm:grid-cols-5"><MetricMini label="q ≤ .01" value={distribution.strong} /><MetricMini label=".01 < q ≤ .05" value={distribution.alert} /><MetricMini label="q > .05" value={distribution.baseline} /><MetricMini label="Not modeled" value={distribution.unavailable} /><MetricMini label="Duplicate identity" value={duplicates} /></div>
    {rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1320px] text-left text-xs"><thead><tr className="border-b border-[#E5E5E2] text-[#6B7280]"><Th>Signal / disease</Th><Th>Geography</Th><Th>Series</Th><Th>Status</Th><Th>Tier</Th><Th>p</Th><Th>q</Th><Th>Verification</Th><Th>Fit / rejection</Th><Th>Actions</Th></tr></thead><tbody>{rows.map(row => { const payload = isSignalPayload(row.payload) ? row.payload : null; return <tr key={`${row.run_id}:${row.signal_id}`} className="border-b border-[#E5E5E2]"><Td><p className="font-semibold">{payload?.identity.disease_name || row.disease_id || row.signal_id}</p><p className="font-mono text-[10px] text-[#6B7280]">{row.signal_id}</p></Td><Td mono>{row.canonical_geography_key || "—"}</Td><Td mono>{row.series_code || "—"}<br />{row.metric_type || "—"}</Td><Td><StatusBadge tone={tone(row.anomaly_state || row.status)}>{pretty(row.anomaly_state || row.status)}</StatusBadge></Td><Td>{pretty(payload?.anomaly.detector_tier)}</Td><Td mono>{fixed(row.raw_p_value, 5)}</Td><Td mono>{fixed(row.q_value, 5)}</Td><Td><StatusBadge tone={tone(payload?.assessment.verification_status)}>{pretty(payload?.assessment.verification_status)}</StatusBadge><p className="mt-1 text-[10px] text-[#6B7280]">{pretty(payload?.assessment.verification_basis)} · {pretty(payload?.assessment.temporal_relevance)}</p></Td><Td>{payload?.anomaly.fit_status ? pretty(payload.anomaly.fit_status) : pretty(row.rejection_reason)}</Td><Td>{payload ? <div className="flex gap-1">{(["verify", "reject", "suppress"] as const).map(action => <button key={action} onClick={() => onDecision({ kind: "signal", targetId: row.signal_id, title: `${payload.identity.disease_name} · ${row.signal_id}`, action })} className="h-7 rounded border border-[#D9D9D6] px-2 text-[10px] font-semibold capitalize">{action}</button>)}</div> : "—"}</Td></tr>; })}</tbody></table></div> : <EmptyState title="No signal ledger rows" description="Adjust the server filters or run v3 analysis." className="min-h-48" />}
  </>;
}

function EventsTable({ rows, onDecision }: { rows: EventRow[]; onDecision: (decision: Decision) => void }) {
  return rows.length ? <div>{rows.map(row => <article key={row.cluster_id} className="border-b border-[#E5E5E2] p-4 last:border-b-0"><div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between"><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{row.disease_name}</h3><StatusBadge tone={tone(row.review_state)}>{pretty(row.review_state)}</StatusBadge></div><p className="mt-1 text-xs text-[#6B7280]">{row.geographies?.map(place => place.name || place.code).join(" · ") || "No geography"} · {row.first_published_at} → {row.last_published_at}</p><p className="mt-1 font-mono text-[10px] text-[#6B7280]">{row.cluster_id}</p></div><div className="flex flex-wrap gap-2">{(["publish", "correct", "merge", "suppress"] as const).map(action => <button key={action} onClick={() => onDecision({ kind: "event", targetId: row.cluster_id, title: `${row.disease_name} · ${row.cluster_id}`, action })} className="h-8 rounded border border-[#D9D9D6] px-2 text-xs font-semibold capitalize">{action}</button>)}</div></div><ol className="mt-3 space-y-2 border-l-2 border-[#D9D9D6] pl-3">{row.updates?.map(update => <li key={update.update_id} className="grid gap-1 text-xs sm:grid-cols-[90px_1fr_auto]"><span className="text-[#6B7280]">{update.published_at}</span><a href={update.source_url} target="_blank" rel="noreferrer" className="font-semibold hover:underline">{update.title}</a><span className="text-[#6B7280]">{update.source}</span></li>)}</ol></article>)}</div> : <EmptyState title="No event clusters" description="Official updates will be clustered by disease, time, and overlapping geography." className="min-h-48" />;
}

function ReportsTable({ rows, compareLeft, compareRight, comparison, setCompareLeft, setCompareRight, onCompare, onRollback, busy }: { rows: ReportRow[]; compareLeft: string; compareRight: string; comparison: CompareResponse | null; setCompareLeft: (value: string) => void; setCompareRight: (value: string) => void; onCompare: () => void; onRollback: (row: ReportRow) => void; busy: boolean }) {
  return <>
    <div className="grid gap-2 border-b border-[#E5E5E2] p-4 lg:grid-cols-[1fr_1fr_auto]"><label htmlFor="report-compare-baseline" className="grid gap-1 text-xs font-medium text-[#6B7280]"><span>Baseline report</span><select id="report-compare-baseline" name="report-compare-baseline" value={compareLeft} onChange={event => setCompareLeft(event.target.value)} className="h-9 min-w-0 rounded border border-[#D9D9D6] bg-white px-2 font-mono text-xs">{rows.map(row => <option key={`left:${row.report_id}`} value={row.report_id}>{row.report_id}</option>)}</select></label><label htmlFor="report-compare-candidate" className="grid gap-1 text-xs font-medium text-[#6B7280]"><span>Candidate report</span><select id="report-compare-candidate" name="report-compare-candidate" value={compareRight} onChange={event => setCompareRight(event.target.value)} className="h-9 min-w-0 rounded border border-[#D9D9D6] bg-white px-2 font-mono text-xs">{rows.map(row => <option key={`right:${row.report_id}`} value={row.report_id}>{row.report_id}</option>)}</select></label><button disabled={busy || !compareLeft || !compareRight || compareLeft === compareRight} onClick={onCompare} className="mt-auto inline-flex h-9 items-center justify-center gap-2 rounded border border-[#D9D9D6] px-3 text-sm font-semibold disabled:opacity-40"><GitCompareArrows className="h-4 w-4" />Compare</button></div>
    {comparison ? <div className="border-b border-[#E5E5E2] bg-[#F7F7F5] p-4"><p className="text-sm font-semibold">{comparison.signals.added.length} added · {comparison.signals.removed.length} removed · {comparison.signals.changed.length} changed</p><div className="mt-2 grid gap-2 lg:grid-cols-4"><JsonSummary title="Coverage" value={comparison.coverage} /><JsonSummary title="Sources" value={comparison.sources} /><JsonSummary title="Method" value={comparison.method} /><JsonSummary title="Quality gate" value={comparison.quality_gate} /></div></div> : null}
    {rows.length ? rows.map(row => <article key={row.report_id} className="grid gap-3 border-b border-[#E5E5E2] p-4 last:border-b-0 lg:grid-cols-[1fr_auto]"><div><div className="flex flex-wrap items-center gap-2"><StatusBadge tone={tone(row.status)}>{pretty(row.status)}</StatusBadge><p className="font-semibold">{pretty(row.report_kind)} · {row.period_key} · r{row.revision}</p></div><p className="mt-1 text-xs text-[#6B7280]">{row.period_start} → {row.period_end} · {row.run_ids?.length || 0} member runs · {row.summary?.unique_signal_count || 0} unique signals</p><p className="mt-1 font-mono text-[10px] text-[#6B7280]">{row.report_id}</p></div><button disabled={busy || row.status !== "published"} onClick={() => onRollback(row)} className="inline-flex h-8 items-center gap-1 rounded border border-[#D9D9D6] px-2 text-xs font-semibold disabled:opacity-40"><RotateCcw className="h-3.5 w-3.5" />Rollback pointer</button></article>) : <EmptyState title="No reports" description="Gate-passed period reports will appear here." className="min-h-48" />}
  </>;
}

function AuditTable({ rows }: { rows: AuditRow[] }) {
  return rows.length ? <div>{rows.map(row => <article key={row.decision_id} className="grid gap-2 border-b border-[#E5E5E2] p-4 last:border-b-0 md:grid-cols-[150px_1fr_auto]"><div><StatusBadge tone="warning">{pretty(row.action)}</StatusBadge></div><div><p className="font-mono text-xs">{row.target_type} · {row.target_id}</p><p className="mt-1 text-sm text-[#4B5563]">{row.note}</p><p className="mt-1 font-mono text-[10px] text-[#6B7280]">{row.decision_id}</p></div><p className="text-xs text-[#6B7280]">{row.actor || "unknown"}<br />{when(row.created_at)}</p></article>)}</div> : <EmptyState title="No v3 audit decisions" description="Correction, suppression, merge, and rollback decisions are append-only." className="min-h-48" />;
}

function DecisionDialog({ decision, note, payload, busy, setNote, setPayload, onCancel, onSubmit }: { decision: Decision; note: string; payload: string; busy: boolean; setNote: (value: string) => void; setPayload: (value: string) => void; onCancel: () => void; onSubmit: () => void }) {
  const requiresPayload = decision.action === "correct" || decision.action === "merge";
  const supportsPayload = requiresPayload || decision.action === "verify";
  const payloadLabel = decision.action === "merge" ? "Target cluster ID" : "Structured evidence (JSON)";
  const payloadPlaceholder = decision.action === "merge"
    ? "event-cluster:…"
    : decision.action === "correct"
      ? '{"disease_name":"…","geographies":[{"code":"…","name":"…"}]}'
      : '{"risk_level":"high","risk_rationale":"…","evidence_url":"https://…"}';
  return <div className="fixed inset-0 z-50 grid place-items-center bg-black/35 p-4" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onCancel(); }}>
    <section role="dialog" aria-modal="true" aria-labelledby="decision-title" className="w-full max-w-lg rounded-lg bg-white shadow-xl">
      <header className="flex items-start justify-between border-b border-[#E5E5E2] p-4"><div><p className="text-xs font-semibold uppercase tracking-wide text-[#9A3412]">Confirm audited action</p><h2 id="decision-title" className="mt-1 text-lg font-semibold capitalize">{pretty(decision.action)}</h2></div><button onClick={onCancel} aria-label="Close decision form" className="rounded p-1 hover:bg-[#F3F4F6]"><X className="h-5 w-5" /></button></header>
      <div className="space-y-4 p-4">
        <dl className="grid gap-2 rounded border border-[#E5E5E2] bg-[#F7F7F5] p-3 text-sm"><div><dt className="text-xs uppercase text-[#6B7280]">Target</dt><dd className="mt-1 font-semibold">{decision.title}</dd></div><div><dt className="text-xs uppercase text-[#6B7280]">Action</dt><dd className="mt-1 capitalize">{pretty(decision.action)}</dd></div><div><dt className="text-xs uppercase text-[#6B7280]">Effect</dt><dd className="mt-1 text-[#4B5563]">{decision.kind === "rollback" ? "Advance the public pointer to this existing gate-passed immutable report; no report payload is overwritten." : "Store an append-only decision that the next analysis run applies without rewriting source facts."}</dd></div></dl>
        {supportsPayload ? <label className="grid gap-1 text-sm font-semibold">{payloadLabel}{decision.action === "verify" ? <span className="font-normal text-[#6B7280]">Optional; omit to verify the signal without assigning public-health risk.</span> : null}<textarea required={requiresPayload} value={payload} onChange={event => setPayload(event.target.value)} rows={4} className="rounded border border-[#D9D9D6] p-2 font-mono text-xs font-normal" placeholder={payloadPlaceholder} /></label> : null}
        <label className="grid gap-1 text-sm font-semibold">Audit note<textarea autoFocus required minLength={3} value={note} onChange={event => setNote(event.target.value)} rows={4} className="rounded border border-[#D9D9D6] p-2 font-normal" placeholder="Why is this decision being made?" /></label>
        <p className="text-xs text-[#6B7280]">Actor: dashboard · this action will be retained in the v3 audit ledger.</p>
      </div>
      <footer className="flex justify-end gap-2 border-t border-[#E5E5E2] p-4"><button disabled={busy} onClick={onCancel} className="h-9 rounded border border-[#D9D9D6] px-3 text-sm font-semibold">Cancel</button><button disabled={busy || note.trim().length < 3 || (requiresPayload && !payload.trim())} onClick={onSubmit} className="h-9 rounded bg-[#9A3412] px-3 text-sm font-semibold text-white disabled:opacity-40">{busy ? "Saving…" : `Confirm ${pretty(decision.action)}`}</button></footer>
    </section>
  </div>;
}

function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <footer className="flex items-center justify-between p-4 text-xs text-[#6B7280]"><span>Page {page} of {pages}</span><div className="flex gap-2"><button disabled={page <= 1} onClick={() => onPage(page - 1)} className="h-8 rounded border border-[#D9D9D6] px-3 disabled:opacity-40">Previous</button><button disabled={page >= pages} onClick={() => onPage(page + 1)} className="h-8 rounded border border-[#D9D9D6] px-3 disabled:opacity-40">Next</button></div></footer>;
}

function filterOptions(tab: Tab): string[] {
  if (tab === "runs") return ["staged", "published", "completed_unchanged", "gate_failed"];
  if (tab === "signals") return ["strong", "alert", "routine", "not_modeled"];
  if (tab === "events") return ["unreviewed", "publish", "correct", "merge", "suppress"];
  if (tab === "reports") return ["daily", "weekly", "monthly"];
  if (tab === "audit") return ["event", "signal", "report", "publication"];
  return [];
}

function Metric({ label, value, detail, toneValue }: { label: string; value: string | number; detail: string; toneValue?: string }) {
  return <div className="app-panel p-4"><div className="flex items-center justify-between"><span className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">{label}</span>{toneValue ? <StatusBadge tone={tone(toneValue)}>{pretty(toneValue)}</StatusBadge> : null}</div><p className="mt-3 break-words text-xl font-semibold text-[#1D1D1F]">{value}</p><p className="mt-1 text-xs text-[#6B7280]">{detail}</p></div>;
}

function MetricMini({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded border border-[#E5E5E2] p-3"><p className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>;
}

function JsonSummary({ title, value }: { title: string; value: unknown }) {
  return <details className="rounded border border-[#E5E5E2] p-3"><summary className="cursor-pointer text-xs font-semibold">{title}</summary><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[10px] text-[#4B5563]">{JSON.stringify(value ?? {}, null, 2)}</pre></details>;
}

function Th({ children }: { children: ReactNode }) { return <th className="px-3 py-2 font-semibold">{children}</th>; }
function Td({ children, mono = false }: { children: ReactNode; mono?: boolean }) { return <td className={`px-3 py-2 align-top ${mono ? "font-mono text-[10px]" : ""}`}>{children}</td>; }
