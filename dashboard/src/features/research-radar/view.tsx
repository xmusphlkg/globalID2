"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bot, BookOpenCheck, CheckCircle2, ExternalLink, Network, Radar, RefreshCw, Search, ShieldCheck, Sparkles, WandSparkles, XCircle } from "lucide-react";

import { apiFetch, apiFetchWithMeta, type PaginationMeta } from "@/lib/api";
import { DataTable, Drawer, EmptyState, FilterBar, MetricStrip, StatusBadge, WorkspacePage, type DataTableColumn } from "@/shared/ui";

type EvidenceSummary = {
  language: "en" | "zh";
  status: "draft" | "review" | "published" | "rejected";
  research_question?: string | null;
  study_design?: string | null;
  population_setting?: string | null;
  main_findings?: string | null;
  public_health_relevance?: string | null;
  limitations?: string | null;
  gids_interpretation?: string | null;
  generated_by?: string | null;
  model?: string | null;
  provider?: string | null;
  quality_score?: number | null;
  evidence_map?: Record<string, { sources?: string[]; confidence?: number }>;
  generated_at?: string | null;
  review_notes?: string | null;
};

type Article = {
  article_id: string;
  slug: string;
  doi?: string | null;
  title: string;
  journal?: string | null;
  published_at?: string | null;
  indexed_at?: string | null;
  study_type?: string | null;
  discovery_score: number;
  publication_status: "review" | "published" | "excluded";
  integrity_status: string;
  open_access_status: string;
  is_featured: boolean;
  diseases: Array<{ disease_id: string; name_en: string }>;
  topics: Array<{ topic: string }>;
  summaries: EvidenceSummary[];
};

type ArticlePatch = {
  publication_status?: Article["publication_status"];
  is_featured?: boolean;
  summary_language?: "en" | "zh";
  summary_status?: "draft" | "review" | "published" | "rejected";
};

type GapCandidate = {
  id: number;
  article_id: string;
  article_slug: string;
  article_title: string;
  journal?: string | null;
  published_at?: string | null;
  publication_status: Article["publication_status"];
  integrity_status: string;
  relation_level: "exact_disease_geography" | "disease_context" | "candidate";
  status: "review" | "confirmed" | "rejected";
  confidence: number;
  match_reasons: string[];
};

type EvidenceGap = {
  gap_id: string;
  signal_id: string;
  signal_kind: string;
  signal_section: string;
  disease_id: string;
  disease_name: string;
  country_codes: string[];
  country_names: string[];
  gap_type: string;
  status: "open" | "searching" | "review" | "covered" | "no_results" | "dismissed" | "error" | "inactive";
  priority_score: number;
  query_plan: {
    crossref?: { exact?: string; disease_context?: string };
    europe_pmc?: { exact?: string; disease_context?: string };
  };
  latest_metrics: {
    fetched?: number;
    normalized?: number;
    candidate_links?: number;
    exact_candidates?: number;
    context_candidates?: number;
    weak_candidates?: number;
    last_run_at?: string;
  };
  last_searched_at?: string | null;
  next_search_at?: string | null;
  resolution_note?: string | null;
  error?: string | null;
  candidates: GapCandidate[];
};

type Dashboard = {
  total_articles: number;
  published_articles: number;
  review_queue: number;
  excluded_articles: number;
  featured_articles: number;
  published_last_7_days: number;
  summaries_awaiting_review: number;
  surveillance_context: {
    available?: boolean;
    visibility?: "public" | "shadow" | "unavailable";
    snapshot_id?: string | null;
    data_through?: string | null;
    method_version?: string | null;
    metrics?: {
      active_signals?: number;
      signals_with_exact_evidence?: number;
      exact_evidence_links?: number;
      signals_with_disease_context?: number;
      contextual_evidence_links?: number;
      evidence_gaps?: number;
    };
    gap_lifecycle?: {
      open?: number;
      review?: number;
      covered?: number;
      error?: number;
      links_awaiting_review?: number;
    };
  };
  latest_runs: Array<{ run_uuid: string; status: string; started_at: string; counts: Record<string, number>; error?: string | null }>;
  schedule: { jobs?: Array<{ last_status: string; next_run_at?: string | null; ai_enrichment_enabled?: boolean }> };
  automation: {
    enabled: boolean;
    policy_version: string;
    mode: string;
    thresholds: {
      article_min_score?: number;
      exact_relation_min_confidence?: number;
      context_relation_min_confidence?: number;
      summary_min_quality?: number;
    };
    automatic: { published_articles?: number; confirmed_links?: number; published_summaries?: number };
    exceptions: { articles?: number; links?: number; summaries?: number; total?: number };
    last_run?: { completed_at?: string | null; counts?: Record<string, number> } | null;
  };
};

const ARTICLE_PAGE_SIZE = 50;
const GAP_PAGE_SIZE = 50;

function formatDate(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
}

export default function ResearchRadarView() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("review");
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [articlePage, setArticlePage] = useState(1);
  const [gapStatus, setGapStatus] = useState("open");
  const [gapPage, setGapPage] = useState(1);
  const [selectedGapId, setSelectedGapId] = useState<string | null>(null);
  const [selectedSummary, setSelectedSummary] = useState<{ article: Article; summary: EvidenceSummary } | null>(null);
  const dashboard = useQuery({
    queryKey: ["research-radar", "dashboard"],
    queryFn: () => apiFetch<Dashboard>("/research-radar/dashboard"),
    refetchInterval: 20_000,
  });
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchDraft.trim());
      setArticlePage(1);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchDraft]);
  const articles = useQuery({
    queryKey: ["research-radar", "articles", status, search, articlePage],
    queryFn: () => {
      const params = new URLSearchParams({
        status,
        page: String(articlePage),
        page_size: String(ARTICLE_PAGE_SIZE),
      });
      if (search) params.set("search", search);
      return apiFetchWithMeta<Article[]>(`/research-radar/articles?${params}`).then(({ data, meta }) => ({
        rows: data,
        pagination: meta.pagination ?? {
          page: articlePage,
          page_size: ARTICLE_PAGE_SIZE,
          total: data.length,
          total_pages: data.length ? 1 : 0,
        },
      }));
    },
  });
  const gaps = useQuery({
    queryKey: ["research-radar", "gaps", gapStatus, gapPage],
    queryFn: () => {
      const params = new URLSearchParams({
        status: gapStatus,
        page: String(gapPage),
        page_size: String(GAP_PAGE_SIZE),
      });
      return apiFetchWithMeta<EvidenceGap[]>(`/research-radar/gaps?${params}`).then(({ data, meta }) => ({
        rows: data,
        pagination: meta.pagination ?? {
          page: gapPage,
          page_size: GAP_PAGE_SIZE,
          total: data.length,
          total_pages: data.length ? 1 : 0,
        },
      }));
    },
  });
  const refreshAll = () => queryClient.invalidateQueries({ queryKey: ["research-radar"] });
  const data = dashboard.data;
  const signalContext = data?.surveillance_context;
  const signalMetrics = signalContext?.metrics;
  const gapLifecycle = signalContext?.gap_lifecycle;
  const gapRows = gaps.data?.rows ?? [];
  const gapPagination: PaginationMeta | undefined = gaps.data?.pagination ?? undefined;
  const selectedGap = gapRows.find((gap) => gap.gap_id === selectedGapId) ?? null;
  const articleRows = articles.data?.rows ?? [];
  const articlePagination: PaginationMeta | undefined = articles.data?.pagination ?? undefined;
  const sync = useMutation({
    mutationFn: () => apiFetch<{ task_uuid?: string; status: string; reason?: string }>("/research-radar/sync", {
      method: "POST",
      body: JSON.stringify({}),
    }),
    onSuccess: refreshAll,
  });
  const enrichmentEnabled = Boolean(data?.schedule.jobs?.[0]?.ai_enrichment_enabled);
  const enrich = useMutation({
    mutationFn: (articleIds: string[] = []) => apiFetch<{ task_uuid?: string; status: string; reason?: string }>("/research-radar/enrich", {
      method: "POST",
      body: JSON.stringify({ article_ids: articleIds, languages: ["en", "zh"] }),
    }),
    onSuccess: refreshAll,
  });
  const update = useMutation({
    mutationFn: ({ articleId, patch }: { articleId: string; patch: ArticlePatch }) =>
      apiFetch<Article>(`/research-radar/articles/${articleId}`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: () => {
      refreshAll();
      setSelectedSummary(null);
    },
  });
  const refreshGaps = useMutation({
    mutationFn: () => apiFetch<Record<string, number | string | boolean | null>>("/research-radar/gaps/refresh", {
      method: "POST",
      body: JSON.stringify({}),
    }),
    onSuccess: refreshAll,
  });
  const runAutopilot = useMutation({
    mutationFn: () => apiFetch<Record<string, unknown>>("/research-radar/automation/run", {
      method: "POST",
      body: JSON.stringify({ dry_run: false, export: true }),
    }),
    onSuccess: refreshAll,
  });
  const discoverGaps = useMutation({
    mutationFn: (gapIds: string[] = []) => apiFetch<{ task_uuid?: string; status: string; reason?: string }>("/research-radar/gaps/discover", {
      method: "POST",
      body: JSON.stringify({ gap_ids: gapIds, limit: gapIds.length || undefined }),
    }),
    onSuccess: refreshAll,
  });
  const reviewLink = useMutation({
    mutationFn: ({ linkId, status: reviewStatus, relationLevel }: { linkId: number; status: "confirmed" | "rejected"; relationLevel?: GapCandidate["relation_level"] }) =>
      apiFetch<Record<string, unknown>>(`/research-radar/evidence-links/${linkId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: reviewStatus, relation_level: relationLevel }),
      }),
    onSuccess: refreshAll,
  });
  const dismissGap = useMutation({
    mutationFn: (gapId: string) => apiFetch<EvidenceGap>(`/research-radar/gaps/${gapId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "dismissed", note: "Dismissed by editor in the Research Radar control plane." }),
    }),
    onSuccess: () => {
      refreshAll();
      setSelectedGapId(null);
    },
  });
  const columns: DataTableColumn<Article>[] = [
    {
      key: "article",
      header: "Article",
      render: (row) => (
        <div className="max-w-xl py-1">
          <p className="font-semibold leading-5 text-[#1D1D1F]">{row.title}</p>
          <p className="mt-1 text-xs text-[#6B7280]">{row.journal || "Unknown journal"} · {formatDate(row.published_at)}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {row.diseases.slice(0, 3).map((disease) => <span key={disease.disease_id} className="rounded bg-[#FFF1E8] px-1.5 py-0.5 text-[10px] font-semibold text-[#C2410C]">{disease.name_en}</span>)}
            {row.topics.slice(0, 2).map((topic) => <span key={topic.topic} className="rounded bg-[#F3F4F6] px-1.5 py-0.5 text-[10px] text-[#4B5563]">{topic.topic}</span>)}
            {row.summaries.map((summary) => <span key={summary.language} className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${summary.status === "published" ? "bg-emerald-50 text-emerald-700" : "bg-violet-50 text-violet-700"}`}>{summary.language.toUpperCase()} summary · {summary.status}</span>)}
          </div>
        </div>
      ),
    },
    { key: "study", header: "Study", render: (row) => <span className="text-xs text-[#4B5563]">{row.study_type || "Journal article"}</span> },
    { key: "score", header: "Discovery", render: (row) => <span className="font-mono text-xs font-semibold text-[#374151]">{Math.round(row.discovery_score * 100)}</span> },
    { key: "integrity", header: "Integrity", render: (row) => <StatusBadge status={row.integrity_status}>{row.integrity_status.replaceAll("_", " ")}</StatusBadge> },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (row) => (
        <div className="flex justify-end gap-1.5">
          {row.doi ? <a href={`https://doi.org/${row.doi}`} target="_blank" rel="noreferrer" aria-label="Open DOI" className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-[#D9D9D6] text-[#6B7280] hover:text-[#C2410C]"><ExternalLink className="h-3.5 w-3.5" /></a> : null}
          <button type="button" title={enrichmentEnabled ? "Generate an evidence summary for automatic quality gating" : "Enable Literature AI enrichment in configuration first"} disabled={!enrichmentEnabled || enrich.isPending} onClick={() => enrich.mutate([row.article_id])} className="inline-flex h-8 items-center gap-1 rounded-md border border-[#D9D9D6] px-2 text-xs font-semibold text-[#6B7280] hover:border-violet-300 hover:text-violet-700 disabled:opacity-40"><WandSparkles className="h-3.5 w-3.5" />Summarize</button>
          {status === "review" ? <>
            <button type="button" disabled={update.isPending} onClick={() => update.mutate({ articleId: row.article_id, patch: { publication_status: "excluded" } })} className="h-8 rounded-md border border-[#D9D9D6] px-2.5 text-xs font-semibold text-[#6B7280] hover:border-rose-300 hover:text-rose-700">Exclude</button>
            <button type="button" disabled={update.isPending || row.integrity_status !== "current"} onClick={() => update.mutate({ articleId: row.article_id, patch: { publication_status: "published" } })} className="h-8 rounded-md bg-[#C2410C] px-2.5 text-xs font-semibold text-white hover:bg-[#9A3412] disabled:opacity-40">Publish</button>
          </> : null}
          {status === "published" ? <button type="button" disabled={update.isPending} onClick={() => update.mutate({ articleId: row.article_id, patch: { is_featured: !row.is_featured } })} className={`h-8 rounded-md border px-2.5 text-xs font-semibold ${row.is_featured ? "border-amber-300 bg-amber-50 text-amber-700" : "border-[#D9D9D6] text-[#6B7280]"}`}>{row.is_featured ? "Featured" : "Feature"}</button> : null}
          {row.summaries.filter((summary) => summary.status === "review").map((summary) => <button key={`review-${summary.language}`} type="button" onClick={() => setSelectedSummary({ article: row, summary })} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-violet-200 bg-violet-50 px-2 text-[10px] font-semibold text-violet-700 hover:bg-violet-100"><BookOpenCheck className="h-3.5 w-3.5" />Review {summary.language.toUpperCase()}</button>)}
        </div>
      ),
    },
  ];

  return (
    <WorkspacePage
      eyebrow="AI & Reports"
      title="Research Radar"
      description="Monitor an automated evidence pipeline and review only records that fall outside its quality gates."
      actions={<button type="button" onClick={() => sync.mutate()} disabled={sync.isPending} className="inline-flex h-9 items-center gap-2 rounded-md bg-[#C2410C] px-3 text-sm font-semibold text-white hover:bg-[#9A3412] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${sync.isPending ? "animate-spin" : ""}`} />Sync sources</button>}
    >
      <MetricStrip items={[
        { label: "Indexed", value: data?.total_articles ?? "—", detail: "Unique literature records", icon: BookOpenCheck },
        { label: "Exceptions", value: data?.automation.exceptions.total ?? "—", detail: "Outside automatic quality gates", icon: Search, tone: data?.automation.exceptions.total ? "warning" : "neutral" },
        { label: "Published", value: data?.published_articles ?? "—", detail: `${data?.published_last_7_days ?? 0} from the past 7 days`, icon: Sparkles, tone: "success" },
        { label: "Automated decisions", value: (data?.automation.automatic.published_articles ?? 0) + (data?.automation.automatic.confirmed_links ?? 0) + (data?.automation.automatic.published_summaries ?? 0), detail: "Audited publications and links", icon: Bot, tone: "success" },
      ]} />
      <section className="rounded-lg border border-emerald-200 bg-gradient-to-r from-emerald-50 to-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-emerald-700" /><h2 className="text-sm font-semibold text-[#1D1D1F]">Research Radar autopilot</h2><StatusBadge status={data?.automation.enabled ? "published" : "review"}>{data?.automation.enabled ? "active" : "paused"}</StatusBadge></div>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-[#4B5563]">High-confidence peer-reviewed records, exact evidence relationships, and source-grounded summaries are released automatically. Integrity flags, missing metadata, stale evidence, preprints, and boundary scores remain private as exceptions.</p>
          </div>
          <button type="button" disabled={!data?.automation.enabled || runAutopilot.isPending} onClick={() => runAutopilot.mutate()} className="inline-flex h-8 items-center gap-1.5 rounded-md bg-emerald-700 px-3 text-xs font-semibold text-white hover:bg-emerald-800 disabled:opacity-40"><Bot className={`h-3.5 w-3.5 ${runAutopilot.isPending ? "animate-pulse" : ""}`} />Run policy now</button>
        </div>
        <dl className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Auto-published", data?.automation.automatic.published_articles ?? 0],
            ["Auto-confirmed links", data?.automation.automatic.confirmed_links ?? 0],
            ["Auto-published summaries", data?.automation.automatic.published_summaries ?? 0],
            ["Human exceptions", data?.automation.exceptions.total ?? 0],
          ].map(([label, value]) => <div key={String(label)} className="rounded-md border border-emerald-100 bg-white/80 p-3"><dt className="text-[10px] font-semibold uppercase tracking-wide text-[#6B7280]">{label}</dt><dd className="mt-1 font-mono text-lg font-semibold text-[#1D1D1F]">{value}</dd></div>)}
        </dl>
        <p className="mt-3 font-mono text-[10px] text-[#6B7280]">{data?.automation.policy_version || "Policy unavailable"} · article ≥ {Math.round((data?.automation.thresholds.article_min_score ?? 0) * 100)} · exact relation ≥ {Math.round((data?.automation.thresholds.exact_relation_min_confidence ?? 0) * 100)} · context ≥ {Math.round((data?.automation.thresholds.context_relation_min_confidence ?? 0) * 100)} · summary ≥ {Math.round((data?.automation.thresholds.summary_min_quality ?? 0) * 100)}</p>
      </section>
      <section className="rounded-lg border border-[#E5E7EB] bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-[#1D1D1F]">Evidence enrichment & knowledge graph</h2><p className="mt-1 max-w-3xl text-xs leading-5 text-[#6B7280]">The public relationship graph remains deterministic. Model Center generates bilingual summaries on schedule; validated summaries publish automatically, while failed quality gates stay private.</p></div><button type="button" disabled={!enrichmentEnabled || enrich.isPending} onClick={() => enrich.mutate([])} className="inline-flex h-8 items-center gap-2 rounded-md border border-violet-200 bg-violet-50 px-3 text-xs font-semibold text-violet-700 hover:bg-violet-100 disabled:opacity-40"><WandSparkles className={`h-3.5 w-3.5 ${enrich.isPending ? "animate-pulse" : ""}`} />Generate next batch</button></div>
        {!enrichmentEnabled ? <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">Model enrichment is safely disabled. Set LITERATURE__AI_ENRICHMENT_ENABLED=true after configuring and testing a Model Center route.</p> : null}
      </section>
      <section className="rounded-lg border border-[#E5E7EB] bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><h2 className="text-sm font-semibold text-[#1D1D1F]">Surveillance evidence readiness</h2><StatusBadge status={signalContext?.visibility === "public" ? "published" : signalContext?.visibility === "shadow" ? "review" : "unavailable"}>{signalContext?.visibility || "unavailable"}</StatusBadge></div>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-[#6B7280]">Deterministic links use the latest Situation Room snapshot. Exact evidence requires high-confidence disease and geography matches; disease-only context never validates a signal.</p>
          </div>
          <p className="font-mono text-[10px] text-[#6B7280]">Data through {signalContext?.data_through || "—"}</p>
        </div>
        <dl className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Active signals", signalMetrics?.active_signals ?? 0],
            ["Exact evidence links", signalMetrics?.exact_evidence_links ?? 0],
            ["Disease-context links", signalMetrics?.contextual_evidence_links ?? 0],
            ["Coverage gaps", signalMetrics?.evidence_gaps ?? 0],
          ].map(([label, value]) => <div key={String(label)} className="rounded-md bg-[#F7F7F5] p-3"><dt className="text-[10px] font-semibold uppercase tracking-wide text-[#6B7280]">{label}</dt><dd className="mt-1 font-mono text-lg font-semibold text-[#1D1D1F]">{value}</dd></div>)}
        </dl>
        {signalContext?.visibility === "shadow" ? <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">Preview only: these relationships remain outside the public site until Situation Room publication is enabled and the normal release succeeds.</p> : null}
      </section>
      <section className="rounded-lg border border-[#E5E7EB] bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><Radar className="h-4 w-4 text-[#C2410C]" /><h2 className="text-sm font-semibold text-[#1D1D1F]">Evidence-gap discovery</h2></div>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-[#6B7280]">Persistent gaps are derived from active surveillance signals. Targeted candidates pass automatic bibliographic, integrity, relationship, and publication gates; only exceptions remain here.</p>
          </div>
          <div className="flex gap-2">
            <button type="button" disabled={refreshGaps.isPending} onClick={() => refreshGaps.mutate()} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[#D9D9D6] px-3 text-xs font-semibold text-[#4B5563] hover:border-[#C2410C] hover:text-[#C2410C] disabled:opacity-40"><RefreshCw className={`h-3.5 w-3.5 ${refreshGaps.isPending ? "animate-spin" : ""}`} />Reconcile signals</button>
            <button type="button" disabled={discoverGaps.isPending} onClick={() => discoverGaps.mutate([])} className="inline-flex h-8 items-center gap-1.5 rounded-md bg-[#C2410C] px-3 text-xs font-semibold text-white hover:bg-[#9A3412] disabled:opacity-40"><Search className="h-3.5 w-3.5" />Discover evidence</button>
          </div>
        </div>
        <dl className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {[
            ["Open", gapLifecycle?.open ?? 0],
            ["Gap review", gapLifecycle?.review ?? 0],
            ["Link review", gapLifecycle?.links_awaiting_review ?? 0],
            ["Covered", gapLifecycle?.covered ?? 0],
            ["Errors", gapLifecycle?.error ?? 0],
          ].map(([label, value]) => <div key={String(label)} className="rounded-md bg-[#F7F7F5] p-3"><dt className="text-[10px] font-semibold uppercase tracking-wide text-[#6B7280]">{label}</dt><dd className="mt-1 font-mono text-lg font-semibold text-[#1D1D1F]">{value}</dd></div>)}
        </dl>
        <div className="mt-4 flex flex-wrap items-center gap-1 border-t border-[#E5E7EB] pt-4">
          {["review", "open", "no_results", "covered", "error", "dismissed"].map((value) => <button key={value} type="button" onClick={() => { setGapStatus(value); setGapPage(1); setSelectedGapId(null); }} className={`h-8 rounded-md px-3 text-xs font-semibold capitalize ${gapStatus === value ? "bg-[#FFF1E8] text-[#C2410C]" : "text-[#4B5563] hover:bg-[#F7F7F5]"}`}>{value.replaceAll("_", " ")}</button>)}
        </div>
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          {gapRows.map((gap) => <button key={gap.gap_id} type="button" onClick={() => setSelectedGapId(gap.gap_id)} className="rounded-md border border-[#E5E7EB] p-3 text-left hover:border-[#C2410C] hover:bg-[#FFFCFA]">
            <div className="flex items-start justify-between gap-3"><div><p className="font-semibold text-[#1D1D1F]">{gap.disease_name}</p><p className="mt-1 text-xs text-[#6B7280]">{gap.country_names.join(" · ") || "No signal geography"} · {gap.signal_section.replaceAll("_", " ")}</p></div><div className="flex items-center gap-2"><span className="font-mono text-[10px] text-[#6B7280]">P{Math.round(gap.priority_score)}</span><StatusBadge status={gap.status}>{gap.status.replaceAll("_", " ")}</StatusBadge></div></div>
            <div className="mt-3 flex flex-wrap gap-2 text-[10px] text-[#6B7280]"><span>{gap.candidates.length} candidates</span><span>·</span><span>{gap.latest_metrics.fetched ?? 0} fetched</span><span>·</span><span>searched {formatDate(gap.last_searched_at)}</span></div>
          </button>)}
        </div>
        {gapPagination ? <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-[#E5E7EB] bg-[#FAFAF9] px-3 py-2 text-xs text-[#4B5563]">
          <span>{gapPagination.total} gaps · page {gapPagination.total_pages ? gapPagination.page : 0} of {gapPagination.total_pages}</span>
          <div className="flex gap-1.5">
            <button type="button" disabled={gaps.isFetching || gapPage <= 1} onClick={() => setGapPage((value) => Math.max(1, value - 1))} className="h-8 rounded-md border border-[#D9D9D6] px-2.5 font-semibold disabled:opacity-40">Previous</button>
            <button type="button" disabled={gaps.isFetching || gapPage >= gapPagination.total_pages} onClick={() => setGapPage((value) => value + 1)} className="h-8 rounded-md border border-[#D9D9D6] px-2.5 font-semibold disabled:opacity-40">Next</button>
          </div>
        </div> : null}
        {!gaps.isLoading && !gapRows.length ? <EmptyState title={`No ${gapStatus.replaceAll("_", " ")} gaps`} description="Reconcile the latest Situation Room snapshot or choose another lifecycle status." className="min-h-36" /> : null}
      </section>
      <FilterBar>
        <div className="flex flex-wrap items-center gap-1">{["review", "published", "excluded"].map((value) => <button key={value} type="button" onClick={() => { setStatus(value); setArticlePage(1); }} className={`h-8 rounded-md px-3 text-sm font-medium capitalize ${status === value ? "bg-[#FFF1E8] text-[#C2410C]" : "text-[#4B5563] hover:bg-[#F7F7F5]"}`}>{value}</button>)}</div>
        <label className="ml-auto flex h-8 min-w-64 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-2.5"><Search className="h-3.5 w-3.5 text-[#9CA3AF]" /><input value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="Title, DOI, or journal" className="w-full border-0 bg-transparent p-0 text-sm outline-none" /></label>
      </FilterBar>
      {(sync.isSuccess || enrich.isSuccess || discoverGaps.isSuccess || refreshGaps.isSuccess || reviewLink.isSuccess || runAutopilot.isSuccess) ? <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">Research Radar state updated. Automatic discovery, enrichment, publication, and release work can be followed in Task Runs.</div> : null}
      {(sync.isError || enrich.isError || update.isError || dashboard.isError || articles.isError || gaps.isError || refreshGaps.isError || discoverGaps.isError || reviewLink.isError || dismissGap.isError || runAutopilot.isError) ? <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{String(sync.error || enrich.error || update.error || dashboard.error || articles.error || gaps.error || refreshGaps.error || discoverGaps.error || reviewLink.error || dismissGap.error || runAutopilot.error)}</div> : null}
      <DataTable columns={columns} rows={articleRows} getRowKey={(row) => row.article_id} emptyState={<EmptyState title={articles.isLoading ? "Loading literature…" : "No articles in this queue"} description="Run a source sync or choose another editorial status." className="min-h-48" />} />
      {articlePagination ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-[#E5E7EB] bg-white px-3 py-2 text-xs text-[#4B5563]">
        <span>{articlePagination.total} records · page {articlePagination.total_pages ? articlePagination.page : 0} of {articlePagination.total_pages}</span>
        <div className="flex gap-1.5">
          <button type="button" disabled={articles.isFetching || articlePage <= 1} onClick={() => setArticlePage((value) => Math.max(1, value - 1))} className="h-8 rounded-md border border-[#D9D9D6] px-2.5 font-semibold disabled:opacity-40">Previous</button>
          <button type="button" disabled={articles.isFetching || articlePage >= articlePagination.total_pages} onClick={() => setArticlePage((value) => value + 1)} className="h-8 rounded-md border border-[#D9D9D6] px-2.5 font-semibold disabled:opacity-40">Next</button>
        </div>
      </div> : null}
      {data?.latest_runs?.length ? <section className="rounded-lg border border-[#E5E7EB] bg-white p-4"><h2 className="text-sm font-semibold text-[#1D1D1F]">Recent synchronization runs</h2><div className="mt-3 grid gap-2 md:grid-cols-3">{data.latest_runs.slice(0, 3).map((run) => <div key={run.run_uuid} className="rounded-md bg-[#F7F7F5] p-3"><div className="flex items-center justify-between gap-2"><span className="font-mono text-[10px] text-[#6B7280]">{run.run_uuid.slice(0, 8)}</span><StatusBadge status={run.status}>{run.status}</StatusBadge></div><p className="mt-2 text-xs text-[#4B5563]">{formatDate(run.started_at)} · {run.counts.inserted ?? 0} new, {run.counts.updated ?? 0} updated</p></div>)}</div></section> : null}
      <Drawer
        open={selectedSummary !== null}
        onClose={() => setSelectedSummary(null)}
        title={selectedSummary ? `${selectedSummary.summary.language.toUpperCase()} evidence draft` : "Evidence draft"}
        description={selectedSummary?.article.title}
      >
        {selectedSummary ? <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 rounded-md bg-[#F7F7F5] p-3 text-xs"><div><p className="text-[#6B7280]">Model</p><p className="mt-1 font-semibold text-[#1D1D1F]">{selectedSummary.summary.model || "Model Center"}</p></div><div><p className="text-[#6B7280]">Quality</p><p className="mt-1 font-semibold text-[#1D1D1F]">{selectedSummary.summary.quality_score == null ? "—" : `${Math.round(selectedSummary.summary.quality_score * 100)} / 100`}</p></div></div>
          {([
            ["Research question", "research_question"], ["Study design", "study_design"],
            ["Population & setting", "population_setting"], ["Main findings", "main_findings"],
            ["Public-health relevance", "public_health_relevance"], ["Limitations", "limitations"],
            ["GIDS interpretation", "gids_interpretation"],
          ] as const).map(([label, key]) => <section key={key} className="border-t border-[#E5E7EB] pt-4"><div className="flex items-center justify-between gap-3"><h3 className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">{label}</h3>{selectedSummary.summary.evidence_map?.[key] ? <span className="text-[10px] text-violet-700">{Math.round((selectedSummary.summary.evidence_map[key].confidence ?? 0) * 100)}% · {(selectedSummary.summary.evidence_map[key].sources ?? []).join(", ")}</span> : null}</div><p className={`mt-2 text-sm leading-6 ${selectedSummary.summary[key] ? "text-[#1D1D1F]" : "italic text-[#9CA3AF]"}`}>{selectedSummary.summary[key] || "No supported draft was generated for this field."}</p></section>)}
          {selectedSummary.summary.review_notes ? <p className="rounded-md bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">{selectedSummary.summary.review_notes}</p> : null}
          <div className="sticky bottom-0 flex flex-wrap justify-end gap-2 border-t border-[#E5E7EB] bg-white py-4"><button type="button" disabled={update.isPending} onClick={() => update.mutate({ articleId: selectedSummary.article.article_id, patch: { summary_language: selectedSummary.summary.language, summary_status: "rejected" } })} className="h-9 rounded-md border border-rose-200 px-3 text-sm font-semibold text-rose-700 hover:bg-rose-50">Reject draft</button><button type="button" disabled={update.isPending || selectedSummary.article.publication_status !== "published"} title={selectedSummary.article.publication_status === "published" ? "Approve for public release" : "Publish the article first"} onClick={() => update.mutate({ articleId: selectedSummary.article.article_id, patch: { summary_language: selectedSummary.summary.language, summary_status: "published" } })} className="h-9 rounded-md bg-emerald-700 px-3 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-40">Approve for public release</button></div>
        </div> : null}
      </Drawer>
      <Drawer
        open={selectedGap !== null}
        onClose={() => setSelectedGapId(null)}
        title={selectedGap ? `${selectedGap.disease_name} evidence gap` : "Evidence gap"}
        description={selectedGap ? `${selectedGap.country_names.join(" · ") || "No signal geography"} · priority ${Math.round(selectedGap.priority_score)}` : undefined}
      >
        {selectedGap ? <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 rounded-md bg-[#F7F7F5] p-3 text-xs"><div><p className="text-[#6B7280]">Lifecycle</p><div className="mt-1"><StatusBadge status={selectedGap.status}>{selectedGap.status.replaceAll("_", " ")}</StatusBadge></div></div><div><p className="text-[#6B7280]">Signal</p><p className="mt-1 font-mono text-[10px] text-[#1D1D1F]">{selectedGap.signal_id}</p></div></div>
          <section className="border-t border-[#E5E7EB] pt-4"><h3 className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Transparent query plan</h3><dl className="mt-2 space-y-2 text-xs"><div><dt className="font-semibold text-[#1D1D1F]">Crossref</dt><dd className="mt-1 rounded bg-[#F7F7F5] p-2 font-mono text-[10px] leading-5 text-[#4B5563]">{selectedGap.query_plan.crossref?.exact || "—"}</dd></div><div><dt className="font-semibold text-[#1D1D1F]">Europe PMC</dt><dd className="mt-1 rounded bg-[#F7F7F5] p-2 font-mono text-[10px] leading-5 text-[#4B5563]">{selectedGap.query_plan.europe_pmc?.exact || "—"}</dd></div></dl></section>
          <section className="border-t border-[#E5E7EB] pt-4"><div className="flex items-center justify-between gap-3"><h3 className="text-xs font-semibold uppercase tracking-wide text-[#6B7280]">Candidate relationships</h3><button type="button" disabled={discoverGaps.isPending} onClick={() => discoverGaps.mutate([selectedGap.gap_id])} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[#D9D9D6] px-2.5 text-xs font-semibold text-[#4B5563]"><Search className="h-3.5 w-3.5" />Search again</button></div>
            <div className="mt-3 space-y-3">
              {selectedGap.candidates.map((candidate) => <article key={candidate.id} className="rounded-md border border-[#E5E7EB] p-3">
                <div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold leading-5 text-[#1D1D1F]">{candidate.article_title}</p><p className="mt-1 text-xs text-[#6B7280]">{candidate.journal || "Unknown journal"} · {formatDate(candidate.published_at)}</p></div><StatusBadge status={candidate.status}>{candidate.status}</StatusBadge></div>
                <div className="mt-2 flex flex-wrap gap-1">{candidate.match_reasons.map((reason) => <span key={reason} className="rounded bg-[#F3F4F6] px-1.5 py-0.5 text-[10px] text-[#4B5563]">{reason}</span>)}</div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-[10px] text-[#6B7280]">{Math.round(candidate.confidence * 100)}% · {candidate.relation_level.replaceAll("_", " ")}</span>
                  <div className="flex flex-wrap gap-1.5">
                    {candidate.publication_status === "review" ? <button type="button" disabled={update.isPending || candidate.integrity_status !== "current"} onClick={() => update.mutate({ articleId: candidate.article_id, patch: { publication_status: "published" } })} className="inline-flex h-8 items-center gap-1 rounded-md border border-[#D9D9D6] px-2 text-xs font-semibold text-[#4B5563] hover:border-[#C2410C] hover:text-[#C2410C]">Publish article</button> : null}
                    {candidate.status === "review" ? <>
                      <button type="button" disabled={reviewLink.isPending} onClick={() => reviewLink.mutate({ linkId: candidate.id, status: "rejected" })} className="inline-flex h-8 items-center gap-1 rounded-md border border-rose-200 px-2 text-xs font-semibold text-rose-700"><XCircle className="h-3.5 w-3.5" />Reject</button>
                      <button type="button" disabled={reviewLink.isPending} onClick={() => reviewLink.mutate({ linkId: candidate.id, status: "confirmed", relationLevel: "disease_context" })} className="inline-flex h-8 items-center gap-1 rounded-md border border-amber-200 px-2 text-xs font-semibold text-amber-700"><AlertTriangle className="h-3.5 w-3.5" />Context only</button>
                      <button type="button" disabled={reviewLink.isPending} onClick={() => reviewLink.mutate({ linkId: candidate.id, status: "confirmed", relationLevel: "exact_disease_geography" })} className="inline-flex h-8 items-center gap-1 rounded-md bg-emerald-700 px-2 text-xs font-semibold text-white"><CheckCircle2 className="h-3.5 w-3.5" />Confirm exact</button>
                    </> : null}
                  </div>
                </div>
                {candidate.status === "confirmed" && candidate.publication_status !== "published" ? <p className="mt-3 rounded bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800">Relationship confirmed, but still private until this article is published.</p> : null}
              </article>)}
              {!selectedGap.candidates.length ? <p className="rounded-md bg-[#F7F7F5] p-3 text-xs leading-5 text-[#6B7280]">No candidates are attached yet. Run targeted discovery; a no-result run remains auditable and will be retried on schedule.</p> : null}
            </div>
          </section>
          {selectedGap.error ? <p className="rounded-md bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">{selectedGap.error}</p> : null}
          <div className="sticky bottom-0 flex flex-wrap justify-end gap-2 border-t border-[#E5E7EB] bg-white py-4"><button type="button" disabled={dismissGap.isPending} onClick={() => dismissGap.mutate(selectedGap.gap_id)} className="h-9 rounded-md border border-[#D9D9D6] px-3 text-sm font-semibold text-[#6B7280]">Dismiss gap</button><button type="button" disabled={discoverGaps.isPending} onClick={() => discoverGaps.mutate([selectedGap.gap_id])} className="h-9 rounded-md bg-[#C2410C] px-3 text-sm font-semibold text-white">Run targeted discovery</button></div>
        </div> : null}
      </Drawer>
    </WorkspacePage>
  );
}
