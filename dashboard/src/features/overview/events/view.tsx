"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, RefreshCw, RotateCcw, Search } from "lucide-react";

import { apiFetch, apiFetchWithMeta } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { EmptyState, StatusBadge, WorkspacePage } from "@/shared/ui";

type Tab = "overview" | "reports" | "outbreaks" | "sources";
type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "primary";
type RefreshMode = "full" | "no_fetch" | "metadata_only";

type Overview = {
  prompt_version: string;
  notice_total: number;
  ai_status: Record<string, number>;
  document_status: Record<string, number>;
  by_source: Record<string, number>;
  alert_status: Record<string, number>;
  input_modes: Record<string, number>;
  outbreaks: Record<string, number>;
  latest_review_mode: "single_model" | "cross_model" | null;
  latest_run: {
    run_id: string;
    started_at: string;
    sources: { source_id: string; status: string; fetched_count: number; new_count: number; updated_count: number; error: string | null }[];
  } | null;
};

type DocumentInfo = {
  status: string;
  media_type: string | null;
  byte_size: number | null;
  page_count: number | null;
  text_chars: number;
  has_original: boolean;
  attempts: number;
  fetched_at: string | null;
  next_attempt_at: string | null;
  error: string | null;
};

type Extraction = {
  id: number;
  status: string;
  prompt_version: string;
  document_type: string | null;
  input_mode: string | null;
  alert_count: number;
  author_model: string | null;
  reviewer_model: string | null;
  review_mode: string | null;
  review_verdict: string | null;
  review_rounds: number;
  review_score: number | null;
  review_notes: string[];
  created_at: string | null;
};

type Alert = {
  alert_id: string;
  outbreak_id: string | null;
  disease_id: string | null;
  disease_label: string;
  country_code: string;
  admin_area: string | null;
  status: string;
  cases: number | null;
  deaths: number | null;
  count_basis: string | null;
  as_of: string | null;
  quote: string;
  summary_en: string | null;
  published_date: string;
};

type Notice = {
  notice_id: string;
  source_id: string;
  source_label: string | null;
  record_type: string;
  title_en: string;
  url: string;
  published_date: string;
  iso_week_key: string;
  ai_status: string;
  ai_attempts: number;
  ai_last_error: string | null;
  ai_next_attempt_at: string | null;
  document: DocumentInfo | null;
  document_type: string | null;
  input_mode: string | null;
  alert_count: number;
  review_mode: string | null;
  extractions?: Extraction[];
  alerts?: Alert[];
};

type Outbreak = {
  outbreak_id: string;
  disease_id: string | null;
  disease_label: string;
  country_codes: string[];
  first_published: string;
  last_published: string;
  alert_count: number;
  notice_count: number;
  source_ids: string[];
  status: string;
};

type Source = {
  source_id: string;
  label_en: string | null;
  label_zh: string | null;
  homepage: string | null;
  redistribution: string | null;
  stored_notices: number;
  status: string;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  history: { run_id: string; started_at: string; status: string; fetched_count: number; new_count: number; updated_count: number }[];
};

const PAGE_SIZE = 50;
const AI_STATUSES = ["pending", "extracted", "failed", "rejected"];

const aiTone = (status: string): Tone =>
  status === "extracted" || status === "published" ? "success" : status === "failed" ? "danger" : status === "rejected" ? "warning" : "neutral";

const documentTone = (status: string | undefined): Tone =>
  status === "ready" ? "success" : status === "failed" ? "danger" : status === "unavailable" ? "warning" : "neutral";

const inputTone = (mode: string | null): Tone => (mode === "native_file" ? "primary" : mode === "text" ? "info" : "neutral");

const alertTone = (status: string): Tone =>
  status === "new" ? "danger" : status === "ongoing" || status === "imported" ? "warning" : status === "ended" ? "success" : "neutral";

const fmt = (value: string | null | undefined) => (value ? new Date(value).toLocaleString() : "—");
const errorText = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));
const count = (value: number | null) => (value == null ? "—" : value.toLocaleString());

export default function SituationControlView() {
  const { lang } = useAppStore();
  const zh = lang === "zh";
  const t = (en: string, zhText: string) => (zh ? zhText : en);

  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [outbreaks, setOutbreaks] = useState<Outbreak[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [aiFilter, setAiFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [aiLimit, setAiLimit] = useState(30);
  const [detail, setDetail] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadOverview = useCallback(async () => {
    setOverview(await apiFetch<Overview>("/situation/overview"));
  }, []);

  useEffect(() => {
    void loadOverview().catch(cause => setError(errorText(cause)));
    const timer = window.setInterval(() => void loadOverview().catch(() => undefined), 20_000);
    return () => window.clearInterval(timer);
  }, [loadOverview]);

  const loadTab = useCallback(async () => {
    setError(null);
    try {
      if (tab === "reports") {
        const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
        if (query.trim()) params.set("q", query.trim());
        if (aiFilter) params.set("ai_status", aiFilter);
        if (sourceFilter) params.set("source_id", sourceFilter);
        const result = await apiFetchWithMeta<Notice[]>(`/situation/notices?${params.toString()}`);
        setNotices(result.data);
        setTotal(Number(result.headers.get("X-Total-Count") || result.data.length));
      } else if (tab === "outbreaks") {
        setOutbreaks(await apiFetch<Outbreak[]>("/situation/outbreaks?page_size=300"));
      } else if (tab === "sources") {
        setSources(await apiFetch<Source[]>("/situation/sources"));
      }
    } catch (cause) {
      setError(errorText(cause));
    }
  }, [tab, page, query, aiFilter, sourceFilter]);

  useEffect(() => { void loadTab(); }, [loadTab]);

  const queueRun = async (mode: RefreshMode) => {
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await apiFetch<{ task_uuid: string; href: string }>("/situation/refresh", {
        method: "POST",
        body: JSON.stringify({ mode, ai_limit: aiLimit }),
      });
      setMessage(`${t("Queued task", "已排队任务")} ${result.task_uuid}`);
    } catch (cause) {
      setError(errorText(cause));
    } finally { setBusy(false); }
  };

  const openDetail = async (noticeId: string) => {
    try {
      setDetail(await apiFetch<Notice>(`/situation/notices/${encodeURIComponent(noticeId)}/detail`));
    } catch (cause) {
      setError(errorText(cause));
    }
  };

  const requeue = async (noticeId: string) => {
    setBusy(true);
    try {
      await apiFetch(`/situation/notices/${encodeURIComponent(noticeId)}/requeue`, { method: "POST" });
      setMessage(t("Report re-queued: document fetch and extraction run on the next run", "该报告已重新排队：下一次运行时重新取文并抽取"));
      setDetail(null);
      await Promise.all([loadOverview(), loadTab()]);
    } catch (cause) {
      setError(errorText(cause));
    } finally { setBusy(false); }
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "overview", label: t("Overview", "概览") },
    { id: "reports", label: t("Reports", "报告") },
    { id: "outbreaks", label: t("Outbreaks", "疫情") },
    { id: "sources", label: t("Sources", "来源") },
  ];

  const card = "rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background";
  const muted = "text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle";
  const strong = "text-tremor-content-strong dark:text-dark-tremor-content-strong";
  const button = "inline-flex h-8 items-center gap-1.5 rounded-tremor-default border border-tremor-border px-3 text-sm font-medium hover:bg-tremor-background-subtle disabled:opacity-50 dark:border-dark-tremor-border dark:hover:bg-dark-tremor-background-subtle";
  const field = "h-8 rounded-tremor-default border border-tremor-border bg-transparent px-2 text-sm dark:border-dark-tremor-border";
  const head = "bg-tremor-background-subtle text-left text-xs uppercase text-tremor-content-subtle dark:bg-dark-tremor-background-subtle";
  const sum = (record: Record<string, number> | undefined) => Object.values(record ?? {}).reduce((total, value) => total + value, 0);

  return (
    <WorkspacePage
      eyebrow={t("Situation", "全球态势")}
      title={t("Official reports and alerts", "官方报告与警报")}
      description={t(
        "Each report is read in full by two models and turned into alerts: one disease in one place, with the counts and a quoted sentence. Publication is automatic once the reviewer model accepts.",
        "每份官方报告由两个模型通读全文，抽取为警报：某病在某地，附报告写明的数字和一句原话。评审模型接受后自动发布。",
      )}
      actions={<>
        <label className="flex items-center gap-1.5 text-sm">
          <span className={muted}>{t("AI reports per run", "每次 AI 报告数")}</span>
          <input type="number" min={1} max={200} value={aiLimit} onChange={event => setAiLimit(Math.max(1, Math.min(200, Number(event.target.value) || 1)))} className={`${field} w-16`} />
        </label>
        <button type="button" className={button} disabled={busy} onClick={() => void queueRun("full")}><RefreshCw className="h-4 w-4" />{t("Collect + read + export", "抓取 + 精读 + 导出")}</button>
        <button type="button" className={button} disabled={busy} onClick={() => void queueRun("no_fetch")}>{t("Read + export only", "仅精读 + 导出")}</button>
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
          <div className={card}>
            <p className={muted}>{t("Reports by extraction", "报告抽取状态")}</p>
            <p className={`mt-1 text-2xl font-semibold ${strong}`}>{overview.notice_total}</p>
            <ul className="mt-2 flex flex-wrap gap-1">{Object.entries(overview.ai_status).map(([status, value]) => <li key={status}><StatusBadge tone={aiTone(status)}>{status} · {value}</StatusBadge></li>)}</ul>
          </div>
          <div className={card}>
            <p className={muted}>{t("Documents", "全文文档")}</p>
            <p className={`mt-1 text-2xl font-semibold ${strong}`}>{sum(overview.document_status)}</p>
            <ul className="mt-2 flex flex-wrap gap-1">{Object.entries(overview.document_status).map(([status, value]) => <li key={status}><StatusBadge tone={documentTone(status)}>{status} · {value}</StatusBadge></li>)}</ul>
          </div>
          <div className={card}>
            <p className={muted}>{t("Alerts", "警报")}</p>
            <p className={`mt-1 text-2xl font-semibold ${strong}`}>{sum(overview.alert_status)}</p>
            <ul className="mt-2 flex flex-wrap gap-1">{Object.entries(overview.alert_status).map(([status, value]) => <li key={status}><StatusBadge tone={alertTone(status)}>{status} · {value}</StatusBadge></li>)}</ul>
          </div>
          <div className={card}>
            <p className={muted}>{t("Outbreaks", "疫情")}</p>
            <p className={`mt-1 text-2xl font-semibold ${strong}`}>{overview.outbreaks.active ?? 0}</p>
            <p className={muted}>{t("active", "活跃")} · {overview.outbreaks.quiet ?? 0} {t("quiet", "平静")}</p>
          </div>
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <div className={card}>
            <h2 className={`text-sm font-semibold ${strong}`}>{t("Review", "评审")}</h2>
            <p className="mt-2 flex flex-wrap items-center gap-2">
              <StatusBadge tone={overview.latest_review_mode === "cross_model" ? "success" : "warning"}>{overview.latest_review_mode ?? "—"}</StatusBadge>
              <span className={muted}>{overview.prompt_version}</span>
            </p>
            <h3 className={`mt-4 text-sm font-semibold ${strong}`}>{t("What the models received", "模型实际收到的内容")}</h3>
            <ul className="mt-2 flex flex-wrap gap-2">{Object.entries(overview.input_modes).map(([mode, value]) => <li key={mode}><StatusBadge tone={inputTone(mode)}>{mode} · {value}</StatusBadge></li>)}</ul>
            {Object.keys(overview.input_modes).length === 0 ? <p className={`mt-1 ${muted}`}>{t("No published extraction yet", "尚无已发布的抽取结果")}</p> : null}
          </div>
          <div className={card}>
            <h2 className={`text-sm font-semibold ${strong}`}>{t("Latest collection run", "最近一次抓取")}</h2>
            {overview.latest_run ? <>
              <p className={`mt-1 ${muted}`}>{overview.latest_run.run_id} · {fmt(overview.latest_run.started_at)}</p>
              <ul className="mt-3 space-y-1.5 text-sm">
                {overview.latest_run.sources.map(row => <li key={row.source_id} className="flex items-center justify-between gap-2" title={row.error ?? undefined}><span className={strong}>{row.source_id}</span><span className="flex items-center gap-2"><span className={muted}>{row.fetched_count} · +{row.new_count} · ~{row.updated_count}</span><StatusBadge tone={row.status === "ok" ? "success" : row.status === "failed" ? "danger" : "neutral"}>{row.status}</StatusBadge></span></li>)}
              </ul>
            </> : <p className={`mt-2 ${muted}`}>{t("No run recorded yet", "尚无运行记录")}</p>}
          </div>
        </div>
      </div> : <p className={muted}>{t("Loading…", "加载中…")}</p>)}

      {tab === "reports" && <div className="space-y-3">
        <form className="flex flex-wrap items-center gap-2" onSubmit={event => { event.preventDefault(); setPage(1); void loadTab(); }}>
          <label className="relative"><span className="sr-only">{t("Search titles", "搜索标题")}</span><Search className="absolute left-2 top-2 h-4 w-4 text-tremor-content-subtle" aria-hidden="true" /><input value={query} onChange={event => setQuery(event.target.value)} placeholder={t("Search titles", "搜索标题")} className={`${field} pl-8`} /></label>
          <label className="text-sm">{t("Extraction", "抽取")} <select value={aiFilter} onChange={event => { setAiFilter(event.target.value); setPage(1); }} className={field}><option value="">{t("all", "全部")}</option>{AI_STATUSES.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <label className="text-sm">{t("Source", "来源")} <select value={sourceFilter} onChange={event => { setSourceFilter(event.target.value); setPage(1); }} className={field}><option value="">{t("all", "全部")}</option>{Object.keys(overview?.by_source ?? {}).map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <button type="submit" className={button}>{t("Apply", "应用")}</button>
          <span className={muted}>{total} {t("reports", "份报告")}</span>
        </form>
        {notices.length === 0 ? <EmptyState title={t("No reports match", "没有匹配的报告")} className="py-10" /> : <div className="overflow-x-auto rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
          <table className="min-w-full text-sm">
            <thead className={head}><tr><th className="px-3 py-2">{t("Date", "日期")}</th><th className="px-3 py-2">{t("Source", "来源")}</th><th className="px-3 py-2">{t("Title", "标题")}</th><th className="px-3 py-2">{t("Document", "全文")}</th><th className="px-3 py-2">{t("Extraction", "抽取")}</th><th className="px-3 py-2">{t("Input", "输入")}</th><th className="px-3 py-2">{t("Alerts", "警报")}</th></tr></thead>
            <tbody>{notices.map(row => <tr key={row.notice_id} className="border-t border-tremor-border align-top dark:border-dark-tremor-border">
              <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{row.published_date}</td>
              <td className="px-3 py-2 text-xs">{row.source_id}</td>
              <td className="px-3 py-2"><button type="button" className={`text-left font-medium hover:underline ${strong}`} onClick={() => void openDetail(row.notice_id)}>{row.title_en}</button></td>
              <td className="px-3 py-2">{row.document ? <StatusBadge tone={documentTone(row.document.status)}>{row.document.status}</StatusBadge> : "—"}</td>
              <td className="px-3 py-2"><div className="flex flex-wrap items-center gap-1"><StatusBadge tone={aiTone(row.ai_status)}>{row.ai_status}</StatusBadge>{row.document_type ? <span className={muted}>{row.document_type}</span> : null}</div></td>
              <td className="px-3 py-2">{row.input_mode ? <StatusBadge tone={inputTone(row.input_mode)}>{row.input_mode}</StatusBadge> : "—"}</td>
              <td className="px-3 py-2 font-mono text-xs">{row.alert_count}</td>
            </tr>)}</tbody>
          </table>
        </div>}
        <div className="flex items-center gap-2">
          <button type="button" className={button} disabled={page <= 1} onClick={() => setPage(value => Math.max(1, value - 1))}>{t("Previous", "上一页")}</button>
          <span className={muted}>{page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
          <button type="button" className={button} disabled={page * PAGE_SIZE >= total} onClick={() => setPage(value => value + 1)}>{t("Next", "下一页")}</button>
        </div>
      </div>}

      {tab === "outbreaks" && (outbreaks.length === 0 ? <EmptyState title={t("No outbreaks yet", "尚无疫情")} className="py-10" /> : <div className="overflow-x-auto rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
        <table className="min-w-full text-sm">
          <thead className={head}><tr><th className="px-3 py-2">{t("Outbreak", "疫情")}</th><th className="px-3 py-2">{t("Places", "地点")}</th><th className="px-3 py-2">{t("Window", "时间")}</th><th className="px-3 py-2">{t("Alerts", "警报")}</th><th className="px-3 py-2">{t("Reports", "报告")}</th><th className="px-3 py-2">{t("Sources", "来源")}</th><th className="px-3 py-2">{t("Status", "状态")}</th></tr></thead>
          <tbody>{outbreaks.map(row => <tr key={row.outbreak_id} className="border-t border-tremor-border dark:border-dark-tremor-border">
            <td className="px-3 py-2"><span className={`font-medium ${strong}`}>{row.disease_label}</span> <span className="font-mono text-[10px] text-tremor-content-subtle">{row.disease_id ?? t("not in catalogue", "目录外")}</span></td>
            <td className="px-3 py-2 font-mono text-xs">{row.country_codes.join(", ")}</td>
            <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{row.first_published} → {row.last_published}</td>
            <td className="px-3 py-2">{row.alert_count}</td>
            <td className="px-3 py-2">{row.notice_count}</td>
            <td className="px-3 py-2 text-xs">{row.source_ids.join(", ")}</td>
            <td className="px-3 py-2"><StatusBadge tone={row.status === "active" ? "info" : "neutral"}>{row.status}</StatusBadge></td>
          </tr>)}</tbody>
        </table>
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
            <a href={detail.url} target="_blank" rel="noreferrer" className={button}>{t("Original report", "原文")}<ExternalLink className="h-3.5 w-3.5" aria-hidden="true" /></a>
            <button type="button" className={button} disabled={busy} onClick={() => void requeue(detail.notice_id)}><RotateCcw className="h-3.5 w-3.5" />{t("Re-read report", "重新精读")}</button>
          </div>
          {detail.ai_last_error ? <p className="mt-3 text-xs text-rose-700 dark:text-rose-300">{detail.ai_last_error}</p> : null}

          <h3 className={`mt-4 text-sm font-semibold ${strong}`}>{t("Document", "全文文档")}</h3>
          {detail.document ? <section className={`mt-1 ${card}`}>
            <div className="flex flex-wrap items-center gap-1">
              <StatusBadge tone={documentTone(detail.document.status)}>{detail.document.status}</StatusBadge>
              <span className={muted}>{detail.document.media_type ?? "—"} · {detail.document.text_chars.toLocaleString()} {t("chars", "字符")}{detail.document.page_count ? ` · ${detail.document.page_count} ${t("pages", "页")}` : ""} · {detail.document.has_original ? t("original file kept", "已保留原文件") : t("text only", "仅文本")}</span>
            </div>
            <p className={`mt-1 ${muted}`}>{t("Fetched", "取回")} {fmt(detail.document.fetched_at)} · {t("attempts", "尝试")} {detail.document.attempts}{detail.document.next_attempt_at ? ` · ${t("next", "下次")} ${fmt(detail.document.next_attempt_at)}` : ""}</p>
            {detail.document.error ? <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{detail.document.error}</p> : null}
          </section> : <p className={`mt-1 ${muted}`}>{t("No document record", "无文档记录")}</p>}

          <h3 className={`mt-4 text-sm font-semibold ${strong}`}>{t("Alerts", "警报")} ({(detail.alerts ?? []).length})</h3>
          {(detail.alerts ?? []).length === 0 ? <p className={`mt-1 ${muted}`}>{t("None yet", "暂无")}</p> : (detail.alerts ?? []).map(alert => <section key={alert.alert_id} className={`mt-2 ${card}`}>
            <div className="flex flex-wrap items-center gap-1">
              <StatusBadge tone={alertTone(alert.status)}>{alert.status}</StatusBadge>
              <span className={`text-sm font-medium ${strong}`}>{alert.disease_label}</span>
              <span className="font-mono text-xs">{alert.country_code}{alert.admin_area ? ` · ${alert.admin_area}` : ""}</span>
              {alert.outbreak_id ? <span className="font-mono text-[10px] text-tremor-content-subtle">{alert.outbreak_id}</span> : null}
            </div>
            <p className={`mt-1 ${muted}`}>
              {t("cases", "病例")} {count(alert.cases)} · {t("deaths", "死亡")} {count(alert.deaths)}
              {alert.count_basis ? ` (${alert.count_basis})` : ""}{alert.as_of ? ` · ${t("as of", "截至")} ${alert.as_of}` : ""}
            </p>
            {alert.summary_en ? <p className={`mt-1 text-sm ${strong}`}>{alert.summary_en}</p> : null}
            <blockquote className="mt-2 border-l-2 border-tremor-border pl-3 text-sm italic dark:border-dark-tremor-border">{alert.quote}</blockquote>
          </section>)}

          <h3 className={`mt-4 text-sm font-semibold ${strong}`}>{t("Extractions", "抽取记录")}</h3>
          {(detail.extractions ?? []).length === 0 ? <p className={`mt-1 ${muted}`}>{t("None yet", "暂无")}</p> : (detail.extractions ?? []).map(item => <section key={item.id} className={`mt-2 ${card}`}>
            <div className="flex flex-wrap items-center gap-1">
              <StatusBadge tone={aiTone(item.status)}>{item.status}</StatusBadge>
              <StatusBadge tone={inputTone(item.input_mode)}>{item.input_mode ?? "—"}</StatusBadge>
              <StatusBadge tone={item.review_mode === "cross_model" ? "success" : "warning"}>{item.review_mode ?? "—"}</StatusBadge>
              <span className={muted}>{item.document_type ?? "—"} · {item.alert_count} {t("alerts", "条警报")} · {item.prompt_version}</span>
            </div>
            <p className={`mt-1 ${muted}`}>{item.author_model ?? "—"} → {item.reviewer_model ?? "—"} · {item.review_verdict ?? "—"}{item.review_score != null ? ` (${item.review_score.toFixed(2)})` : ""} · {t("rounds", "轮次")} {item.review_rounds} · {fmt(item.created_at)}</p>
            {item.review_notes.length > 0 ? <details className="mt-2"><summary className={`cursor-pointer ${muted}`}>{t("Review notes", "评审意见")} ({item.review_notes.length})</summary><ul className={`mt-1 list-disc space-y-1 pl-5 ${muted}`}>{item.review_notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details> : null}
          </section>)}
        </div>
      </div> : null}
    </WorkspacePage>
  );
}
