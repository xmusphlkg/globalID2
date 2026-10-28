"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, RefreshCw, RotateCcw, Search } from "lucide-react";

import { apiFetch, apiFetchWithMeta } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { EmptyState, StatusBadge, WorkspacePage } from "@/shared/ui";

type Tab = "overview" | "notices" | "events" | "digests" | "sources";
type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "primary";

type Overview = {
  prompt_versions: { notice: string; digest: string };
  notice_total: number;
  ai_status: Record<string, number>;
  by_source: Record<string, number>;
  risk_levels: Record<string, number>;
  events: Record<string, number>;
  latest_review_mode: "single_model" | "cross_model" | null;
  latest_run: {
    run_id: string;
    started_at: string;
    sources: { source_id: string; status: string; fetched_count: number; new_count: number; updated_count: number; error: string | null }[];
  } | null;
  latest_digest: { period_key: string; headline_en: string | null; headline_zh: string | null; notice_count: number } | null;
};

type Entity = { entity_type: string; entity_id: string; name_en: string | null; name_zh: string | null; method: string; active: boolean };

type Assessment = {
  id: number;
  status: string;
  prompt_version: string;
  title_zh: string | null;
  summary_en: string | null;
  summary_zh: string | null;
  event_type: string | null;
  risk_level: string | null;
  risk_rationale: string | null;
  author_model: string | null;
  reviewer_model: string | null;
  review_mode: string | null;
  review_verdict: string | null;
  review_rounds: number;
  review_notes: string[];
  created_at: string | null;
};

type Notice = {
  notice_id: string;
  source_id: string;
  source_label: string | null;
  record_type: string;
  title_en: string;
  title_zh: string | null;
  url: string;
  published_date: string;
  iso_week_key: string;
  ai_status: string;
  ai_attempts: number;
  ai_last_error: string | null;
  risk_level: string | null;
  event_type: string | null;
  review_mode: string | null;
  entities: Entity[];
  assessments?: Assessment[];
};

type EventRow = {
  event_id: string;
  disease_id: string;
  country_codes: string[];
  first_published: string;
  last_published: string;
  notice_count: number;
  notice_ids: string[];
  source_ids: string[];
  risk_level: string | null;
  status: string;
};

type Digest = {
  id: number;
  period_key: string;
  status: string;
  headline_en: string | null;
  headline_zh: string | null;
  points: { text_en: string; text_zh: string | null; notice_ids: string[] }[];
  notice_count: number;
  review_mode: string | null;
  review_verdict: string | null;
  review_score: number | null;
  review_notes: string[];
  author_model: string | null;
  reviewer_model: string | null;
  created_at: string | null;
};

type Source = {
  source_id: string;
  label_en: string | null;
  label_zh: string | null;
  homepage: string | null;
  endpoint: string | null;
  redistribution: string | null;
  stored_notices: number;
  status: string;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  history: { run_id: string; started_at: string; status: string; fetched_count: number; new_count: number; updated_count: number }[];
};

const PAGE_SIZE = 50;

const riskTone = (level: string | null | undefined): Tone =>
  level === "very_high" || level === "high" ? "danger" : level === "moderate" ? "warning" : level === "low" ? "success" : "neutral";

const aiTone = (status: string): Tone =>
  status === "enriched" ? "success" : status === "failed" ? "danger" : status === "rejected" ? "warning" : "neutral";

const fmt = (value: string | null | undefined) => (value ? new Date(value).toLocaleString() : "—");

export default function SituationControlView() {
  const { lang } = useAppStore();
  const zh = lang === "zh";
  const t = (en: string, zhText: string) => (zh ? zhText : en);

  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [digests, setDigests] = useState<Digest[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [aiFilter, setAiFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [detail, setDetail] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadOverview = useCallback(async () => {
    setOverview(await apiFetch<Overview>("/situation/overview"));
  }, []);

  useEffect(() => {
    void loadOverview().catch(cause => setError(cause instanceof Error ? cause.message : String(cause)));
    const timer = window.setInterval(() => void loadOverview().catch(() => undefined), 20_000);
    return () => window.clearInterval(timer);
  }, [loadOverview]);

  const loadTab = useCallback(async () => {
    setError(null);
    try {
      if (tab === "notices") {
        const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
        if (query.trim()) params.set("q", query.trim());
        if (aiFilter) params.set("ai_status", aiFilter);
        if (sourceFilter) params.set("source_id", sourceFilter);
        const result = await apiFetchWithMeta<Notice[]>(`/situation/notices?${params.toString()}`);
        setNotices(result.data);
        setTotal(Number(result.headers.get("X-Total-Count") || result.data.length));
      } else if (tab === "events") {
        setEvents(await apiFetch<EventRow[]>("/situation/events?page_size=300"));
      } else if (tab === "digests") {
        setDigests(await apiFetch<Digest[]>("/situation/digests"));
      } else if (tab === "sources") {
        setSources(await apiFetch<Source[]>("/situation/sources"));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [tab, page, query, aiFilter, sourceFilter]);

  useEffect(() => { void loadTab(); }, [loadTab]);

  const queueRun = async (mode: "full" | "no_fetch" | "metadata_only") => {
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await apiFetch<{ task_uuid: string; href: string }>("/situation/refresh", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      setMessage(`${t("Queued task", "已排队任务")} ${result.task_uuid}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  };

  const openDetail = async (noticeId: string) => {
    try {
      setDetail(await apiFetch<Notice>(`/situation/notices/${encodeURIComponent(noticeId)}/detail`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const requeue = async (noticeId: string) => {
    setBusy(true);
    try {
      await apiFetch(`/situation/notices/${encodeURIComponent(noticeId)}/requeue`, { method: "POST" });
      setMessage(t("Notice re-queued for AI on the next run", "该通报将在下一次运行时重新进入 AI 流程"));
      setDetail(null);
      await Promise.all([loadOverview(), loadTab()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "overview", label: t("Overview", "概览") },
    { id: "notices", label: t("Notices", "通报") },
    { id: "events", label: t("Events", "事件") },
    { id: "digests", label: t("Weekly digests", "周综述") },
    { id: "sources", label: t("Sources", "来源") },
  ];

  const card = "rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background";
  const muted = "text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle";
  const strong = "text-tremor-content-strong dark:text-dark-tremor-content-strong";
  const button = "inline-flex h-8 items-center gap-1.5 rounded-tremor-default border border-tremor-border px-3 text-sm font-medium hover:bg-tremor-background-subtle disabled:opacity-50 dark:border-dark-tremor-border dark:hover:bg-dark-tremor-background-subtle";

  return (
    <WorkspacePage
      eyebrow={t("Situation", "全球态势")}
      title={t("International authority notices", "国际权威通报")}
      description={t(
        "WHO, ECDC, Africa CDC and PAHO notices with peer-reviewed AI enrichment. Publication is automatic once the reviewer model accepts.",
        "世卫组织、欧洲疾控中心、非洲疾控中心与泛美卫生组织的通报，经 AI 同行评审后自动发布。",
      )}
      actions={<>
        <button type="button" className={button} disabled={busy} onClick={() => void queueRun("full")}><RefreshCw className="h-4 w-4" />{t("Collect + AI + export", "抓取 + AI + 导出")}</button>
        <button type="button" className={button} disabled={busy} onClick={() => void queueRun("no_fetch")}>{t("AI + export only", "仅 AI + 导出")}</button>
        <button type="button" className={button} disabled={busy} onClick={() => void queueRun("metadata_only")}>{t("Collect without AI", "仅抓取（无 AI）")}</button>
      </>}
    >
      {error ? <p className="rounded border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">{error}</p> : null}
      {message ? <p className="rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">{message}</p> : null}

      <nav className="flex flex-wrap gap-1 border-b border-tremor-border dark:border-dark-tremor-border" aria-label={t("Situation sections", "态势分区")}>
        {tabs.map(item => (
          <button
            key={item.id}
            type="button"
            aria-current={tab === item.id ? "page" : undefined}
            onClick={() => { setTab(item.id); setPage(1); }}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${tab === item.id ? "border-tremor-brand text-tremor-brand" : "border-transparent text-tremor-content-subtle hover:text-tremor-content-strong"}`}
          >{item.label}</button>
        ))}
      </nav>

      {tab === "overview" && (overview ? <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className={card}><p className={muted}>{t("Stored notices", "已入库通报")}</p><p className={`mt-1 text-2xl font-semibold ${strong}`}>{overview.notice_total}</p></div>
          <div className={card}><p className={muted}>{t("AI enriched", "AI 已发布")}</p><p className={`mt-1 text-2xl font-semibold ${strong}`}>{overview.ai_status.enriched ?? 0}</p><p className={muted}>{t("pending", "待处理")} {overview.ai_status.pending ?? 0} · {t("failed", "失败")} {overview.ai_status.failed ?? 0} · {t("rejected", "拒绝")} {overview.ai_status.rejected ?? 0}</p></div>
          <div className={card}><p className={muted}>{t("Events", "事件")}</p><p className={`mt-1 text-2xl font-semibold ${strong}`}>{overview.events.active ?? 0}</p><p className={muted}>{t("active", "活跃")} · {overview.events.quiet ?? 0} {t("quiet", "平静")}</p></div>
          <div className={card}><p className={muted}>{t("Review mode", "评审模式")}</p><p className="mt-2"><StatusBadge tone={overview.latest_review_mode === "cross_model" ? "success" : "warning"}>{overview.latest_review_mode ?? "—"}</StatusBadge></p><p className={`mt-2 ${muted}`}>{overview.prompt_versions.notice} · {overview.prompt_versions.digest}</p></div>
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <div className={card}>
            <h2 className={`text-sm font-semibold ${strong}`}>{t("Latest collection run", "最近一次抓取")}</h2>
            {overview.latest_run ? <>
              <p className={`mt-1 ${muted}`}>{overview.latest_run.run_id} · {fmt(overview.latest_run.started_at)}</p>
              <ul className="mt-3 space-y-1.5 text-sm">
                {overview.latest_run.sources.map(row => <li key={row.source_id} className="flex items-center justify-between gap-2"><span className={strong}>{row.source_id}</span><span className="flex items-center gap-2"><span className={muted}>{row.fetched_count} · +{row.new_count} · ~{row.updated_count}</span><StatusBadge tone={row.status === "ok" ? "success" : row.status === "failed" ? "danger" : "neutral"}>{row.status}</StatusBadge></span></li>)}
              </ul>
            </> : <p className={`mt-2 ${muted}`}>{t("No run recorded yet", "尚无运行记录")}</p>}
          </div>
          <div className={card}>
            <h2 className={`text-sm font-semibold ${strong}`}>{t("Peer-reviewed risk levels", "同行评审风险等级")}</h2>
            <ul className="mt-3 flex flex-wrap gap-2">{Object.entries(overview.risk_levels).map(([level, count]) => <li key={level}><StatusBadge tone={riskTone(level)}>{level} · {count}</StatusBadge></li>)}</ul>
            <h2 className={`mt-4 text-sm font-semibold ${strong}`}>{t("Latest weekly digest", "最新周综述")}</h2>
            {overview.latest_digest ? <p className="mt-1 text-sm">{overview.latest_digest.period_key} · {zh ? overview.latest_digest.headline_zh || overview.latest_digest.headline_en : overview.latest_digest.headline_en}</p> : <p className={`mt-1 ${muted}`}>{t("No digest published yet", "尚无已发布的周综述")}</p>}
          </div>
        </div>
      </div> : <p className={muted}>{t("Loading…", "加载中…")}</p>)}

      {tab === "notices" && <div className="space-y-3">
        <form className="flex flex-wrap items-center gap-2" onSubmit={event => { event.preventDefault(); setPage(1); void loadTab(); }}>
          <label className="relative"><span className="sr-only">{t("Search titles", "搜索标题")}</span><Search className="absolute left-2 top-2 h-4 w-4 text-tremor-content-subtle" aria-hidden="true" /><input value={query} onChange={event => setQuery(event.target.value)} placeholder={t("Search titles", "搜索标题")} className="h-8 rounded-tremor-default border border-tremor-border bg-transparent pl-8 pr-2 text-sm dark:border-dark-tremor-border" /></label>
          <label className="text-sm">{t("AI", "AI")} <select value={aiFilter} onChange={event => { setAiFilter(event.target.value); setPage(1); }} className="h-8 rounded-tremor-default border border-tremor-border bg-transparent px-2 text-sm dark:border-dark-tremor-border"><option value="">{t("all", "全部")}</option>{["enriched", "pending", "failed", "rejected"].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <label className="text-sm">{t("Source", "来源")} <select value={sourceFilter} onChange={event => { setSourceFilter(event.target.value); setPage(1); }} className="h-8 rounded-tremor-default border border-tremor-border bg-transparent px-2 text-sm dark:border-dark-tremor-border"><option value="">{t("all", "全部")}</option>{Object.keys(overview?.by_source ?? {}).map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <button type="submit" className={button}>{t("Apply", "应用")}</button>
          <span className={muted}>{total} {t("notices", "条")}</span>
        </form>
        {notices.length === 0 ? <EmptyState title={t("No notices match", "没有匹配的通报")} className="py-10" /> : <div className="overflow-x-auto rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
          <table className="min-w-full text-sm">
            <thead className="bg-tremor-background-subtle text-left text-xs uppercase text-tremor-content-subtle dark:bg-dark-tremor-background-subtle"><tr><th className="px-3 py-2">{t("Date", "日期")}</th><th className="px-3 py-2">{t("Source", "来源")}</th><th className="px-3 py-2">{t("Title", "标题")}</th><th className="px-3 py-2">{t("Entities", "实体")}</th><th className="px-3 py-2">{t("Risk", "风险")}</th><th className="px-3 py-2">AI</th></tr></thead>
            <tbody>{notices.map(row => <tr key={row.notice_id} className="border-t border-tremor-border align-top dark:border-dark-tremor-border">
              <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{row.published_date}</td>
              <td className="px-3 py-2 text-xs">{row.source_id}</td>
              <td className="px-3 py-2"><button type="button" className={`text-left font-medium hover:underline ${strong}`} onClick={() => void openDetail(row.notice_id)}>{zh ? row.title_zh || row.title_en : row.title_en}</button></td>
              <td className="px-3 py-2"><div className="flex flex-wrap gap-1">{row.entities.filter(e => e.active).map(e => <span key={`${e.entity_type}-${e.entity_id}`} className="rounded border border-tremor-border px-1.5 py-0.5 font-mono text-[10px] dark:border-dark-tremor-border">{e.entity_id}{e.method === "ai" ? "·ai" : ""}</span>)}</div></td>
              <td className="px-3 py-2">{row.risk_level ? <StatusBadge tone={riskTone(row.risk_level)}>{row.risk_level}</StatusBadge> : "—"}</td>
              <td className="px-3 py-2"><StatusBadge tone={aiTone(row.ai_status)}>{row.ai_status}</StatusBadge></td>
            </tr>)}</tbody>
          </table>
        </div>}
        <div className="flex items-center gap-2">
          <button type="button" className={button} disabled={page <= 1} onClick={() => setPage(value => Math.max(1, value - 1))}>{t("Previous", "上一页")}</button>
          <span className={muted}>{page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
          <button type="button" className={button} disabled={page * PAGE_SIZE >= total} onClick={() => setPage(value => value + 1)}>{t("Next", "下一页")}</button>
        </div>
      </div>}

      {tab === "events" && (events.length === 0 ? <EmptyState title={t("No events yet", "尚无事件")} className="py-10" /> : <div className="overflow-x-auto rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
        <table className="min-w-full text-sm">
          <thead className="bg-tremor-background-subtle text-left text-xs uppercase text-tremor-content-subtle dark:bg-dark-tremor-background-subtle"><tr><th className="px-3 py-2">{t("Event", "事件")}</th><th className="px-3 py-2">{t("Window", "时间")}</th><th className="px-3 py-2">{t("Notices", "通报")}</th><th className="px-3 py-2">{t("Sources", "来源")}</th><th className="px-3 py-2">{t("Risk", "风险")}</th><th className="px-3 py-2">{t("Status", "状态")}</th></tr></thead>
          <tbody>{events.map(row => <tr key={row.event_id} className="border-t border-tremor-border dark:border-dark-tremor-border">
            <td className="px-3 py-2 font-mono text-xs">{row.disease_id} · {row.country_codes.join(", ") || t("regional", "区域")}</td>
            <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{row.first_published} → {row.last_published}</td>
            <td className="px-3 py-2">{row.notice_count}</td>
            <td className="px-3 py-2 text-xs">{row.source_ids.join(", ")}</td>
            <td className="px-3 py-2">{row.risk_level ? <StatusBadge tone={riskTone(row.risk_level)}>{row.risk_level}</StatusBadge> : "—"}</td>
            <td className="px-3 py-2"><StatusBadge tone={row.status === "active" ? "info" : "neutral"}>{row.status}</StatusBadge></td>
          </tr>)}</tbody>
        </table>
      </div>)}

      {tab === "digests" && (digests.length === 0 ? <EmptyState title={t("No digests written yet", "尚未生成周综述")} description={t("A digest is written once a week has peer-reviewed notices.", "某一周有经过同行评审的通报后才会生成周综述。")} className="py-10" /> : <div className="space-y-3">
        {digests.map(row => <article key={row.id} className={card}>
          <div className="flex flex-wrap items-center justify-between gap-2"><h2 className={`text-sm font-semibold ${strong}`}>{row.period_key} · {zh ? row.headline_zh || row.headline_en : row.headline_en}</h2><span className="flex gap-1"><StatusBadge tone={row.status === "published" ? "success" : row.status === "rejected" ? "warning" : "neutral"}>{row.status}</StatusBadge><StatusBadge tone={row.review_mode === "cross_model" ? "success" : "warning"}>{row.review_mode ?? "—"}</StatusBadge></span></div>
          <p className={`mt-1 ${muted}`}>{row.notice_count} {t("notices", "条通报")} · {row.author_model} → {row.reviewer_model} · {row.review_verdict} {row.review_score != null ? `(${row.review_score.toFixed(2)})` : ""} · {fmt(row.created_at)}</p>
          <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-sm">{row.points.map((point, index) => <li key={index}>{zh ? point.text_zh || point.text_en : point.text_en} <span className="font-mono text-[10px] text-tremor-content-subtle">[{point.notice_ids.join(", ")}]</span></li>)}</ol>
          {row.review_notes.length > 0 ? <details className="mt-2"><summary className={`cursor-pointer ${muted}`}>{t("Review notes", "评审意见")} ({row.review_notes.length})</summary><ul className={`mt-1 list-disc space-y-1 pl-5 ${muted}`}>{row.review_notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details> : null}
        </article>)}
      </div>)}

      {tab === "sources" && <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {sources.map(row => <article key={row.source_id} className={card}>
          <div className="flex items-start justify-between gap-2"><div><p className={`text-sm font-semibold ${strong}`}>{zh ? row.label_zh || row.label_en : row.label_en}</p><p className={`mt-0.5 ${muted}`}>{row.source_id} · {row.redistribution}</p></div><StatusBadge tone={row.status === "ok" ? "success" : row.status === "failed" ? "danger" : "neutral"}>{row.status}</StatusBadge></div>
          <p className={`mt-2 ${muted}`}>{t("Stored", "已入库")} {row.stored_notices} · {t("checked", "检查")} {fmt(row.last_checked_at)}</p>
          <p className={muted}>{t("Last success", "最后成功")} {fmt(row.last_success_at)}</p>
          {row.last_error ? <p className="mt-2 line-clamp-2 text-xs text-rose-700 dark:text-rose-300" title={row.last_error}>{row.last_error}</p> : null}
          <ul className={`mt-2 space-y-0.5 font-mono ${muted}`}>{row.history.slice(0, 5).map(run => <li key={run.run_id}>{run.started_at.slice(0, 16)} {run.status} {run.fetched_count} +{run.new_count}</li>)}</ul>
          {row.homepage ? <a href={row.homepage} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-tremor-brand hover:underline">{t("Official page", "官方页面")}<ExternalLink className="h-3 w-3" aria-hidden="true" /></a> : null}
        </article>)}
      </div>}

      {detail ? <div role="dialog" aria-modal="true" aria-labelledby="situation-notice-detail" className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setDetail(null)}>
        <div className="h-full w-full max-w-2xl overflow-y-auto bg-tremor-background p-5 shadow-xl dark:bg-dark-tremor-background" onClick={event => event.stopPropagation()}>
          <div className="flex items-start justify-between gap-3">
            <h2 id="situation-notice-detail" className={`text-base font-semibold ${strong}`}>{detail.title_en}</h2>
            <button type="button" className={button} onClick={() => setDetail(null)}>{t("Close", "关闭")}</button>
          </div>
          <p className={`mt-1 ${muted}`}>{detail.notice_id} · {detail.published_date} · {detail.record_type}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <a href={detail.url} target="_blank" rel="noreferrer" className={button}>{t("Original notice", "原文")}<ExternalLink className="h-3.5 w-3.5" aria-hidden="true" /></a>
            <button type="button" className={button} disabled={busy} onClick={() => void requeue(detail.notice_id)}><RotateCcw className="h-3.5 w-3.5" />{t("Re-run AI", "重新跑 AI")}</button>
          </div>
          {detail.ai_last_error ? <p className="mt-3 text-xs text-rose-700 dark:text-rose-300">{detail.ai_last_error}</p> : null}
          <h3 className={`mt-4 text-sm font-semibold ${strong}`}>{t("Entities", "实体")}</h3>
          <ul className="mt-1 flex flex-wrap gap-1">{detail.entities.map(e => <li key={`${e.entity_type}-${e.entity_id}`} className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${e.active ? "border-tremor-border" : "border-dashed opacity-50"}`}>{e.entity_type}:{e.entity_id} · {e.method}{e.active ? "" : ` (${t("inactive", "已停用")})`}</li>)}</ul>
          <h3 className={`mt-4 text-sm font-semibold ${strong}`}>{t("AI assessments", "AI 评估记录")}</h3>
          {(detail.assessments ?? []).length === 0 ? <p className={`mt-1 ${muted}`}>{t("None yet", "暂无")}</p> : (detail.assessments ?? []).map(a => <section key={a.id} className={`mt-2 ${card}`}>
            <div className="flex flex-wrap items-center gap-1"><StatusBadge tone={a.status === "published" ? "success" : a.status === "rejected" ? "warning" : "neutral"}>{a.status}</StatusBadge><StatusBadge tone={riskTone(a.risk_level)}>{a.risk_level ?? "no risk"}</StatusBadge><span className={muted}>{a.event_type} · {a.prompt_version} · {a.review_mode} · {a.review_verdict} · {t("rounds", "轮次")} {a.review_rounds}</span></div>
            {a.title_zh ? <p className="mt-2 text-sm">{a.title_zh}</p> : null}
            {(zh ? a.summary_zh || a.summary_en : a.summary_en) ? <p className={`mt-1 text-sm ${strong}`}>{zh ? a.summary_zh || a.summary_en : a.summary_en}</p> : null}
            {a.risk_rationale ? <p className={`mt-1 ${muted}`}>{a.risk_rationale}</p> : null}
            {a.review_notes.length > 0 ? <details className="mt-2"><summary className={`cursor-pointer ${muted}`}>{t("Review notes", "评审意见")} ({a.review_notes.length})</summary><ul className={`mt-1 list-disc space-y-1 pl-5 ${muted}`}>{a.review_notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details> : null}
          </section>)}
        </div>
      </div> : null}
    </WorkspacePage>
  );
}
