"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/ui/StatusBadge";

interface Workflow {
  country_code: string;
  method_version: string;
  max_figures: number;
  max_figures_per_section: number;
  figure_catalog: { figure_type: string; section_type: string; title: Record<string, string>; purpose: string }[];
  schedule: { enabled: boolean; interval_minutes: number; last_status: string; last_task_uuid?: string; last_error?: string; next_run_at?: string };
  periods: { period_start: string; period_end: string; frequency: string; latest_observation: string; record_count: number; fresh: boolean; state: string }[];
}

export function ReportWorkflow({ lang }: { lang: "zh" | "en" }) {
  const zh = lang === "zh";
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["report-workflow"], queryFn: () => apiFetch<Workflow>("/reports/workflow"), staleTime: 30_000, refetchInterval: 30_000 });
  const [result, setResult] = useState<{ status: string; task_uuid?: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const taskId = result?.task_uuid || query.data?.schedule.last_task_uuid;
  const task = useQuery({
    queryKey: ["report-workflow-task", taskId], enabled: !!taskId,
    queryFn: () => apiFetch<{ status: string; progress: number; last_error?: string }>(`/tasks/${taskId}`),
    refetchInterval: (current) => ["completed", "failed", "cancelled"].includes(current.state.data?.status ?? "") ? false : 5_000,
  });
  useEffect(() => {
    if (task.data?.status === "completed") {
      void client.invalidateQueries({ queryKey: ["reports"] });
      void client.invalidateQueries({ queryKey: ["report-workflow"] });
    }
  }, [task.data?.status, client]);
  async function generate() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    try {
      setResult(await apiFetch("/schedules/report:reports-auto/runs", { method: "POST" }));
      await Promise.all([client.invalidateQueries({ queryKey: ["report-workflow"] }), client.invalidateQueries({ queryKey: ["reports"] })]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { lock.current = false; setBusy(false); }
  }
  const data = query.data;
  const stateLabels: Record<string, string> = zh ? { ready: "可生成", existing: "已有该周期报告，跳过重复生成", active: "生成中", queued: "已排队", idle: "没有新增周期", busy: "调度处理中" } : { ready: "Ready", existing: "Report exists; generation skipped", active: "Active", queued: "Queued", idle: "No new period", busy: "Scheduling in progress" };
  return <section className="cc-panel space-y-4" aria-labelledby="report-workflow-title">
    <div className="flex flex-wrap items-center justify-between gap-3"><div>
      <h2 id="report-workflow-title" className="font-semibold">{zh ? "日本报告试点" : "Japan report pilot"}</h2>
      <p className="cc-muted mt-1 text-sm">{zh ? "已存数据 → 确定性统计 → AI 解读与选图 → 同行评审 → 预览 → R2 增量发布" : "Stored data → calculated statistics → AI narrative and figures → peer review → preview → incremental R2 publication"}</p>
    </div><StatusBadge tone="info">JP</StatusBadge></div>
    {(query.error || error) && <p role="alert" className="text-sm text-red-700">{error || query.error?.message}</p>}
    {query.isLoading && <p role="status">{zh ? "正在读取报告周期…" : "Loading report periods…"}</p>}
    {data && <>
      <p className="cc-muted text-sm">{zh ? `每份报告最多 ${data.max_figures} 张图，每节最多 ${data.max_figures_per_section} 张。AI 仅选择已有数据支持的图表；允许不插图。` : `At most ${data.max_figures} figures per report and ${data.max_figures_per_section} per section. AI selects only evidence-supported charts; no chart is also valid.`}</p>
      <p className="cc-muted text-xs">{zh ? "自动检查" : "Scheduled checks"}: {data.schedule.enabled ? (zh ? `已启用，每 ${data.schedule.interval_minutes} 分钟` : `Enabled, every ${data.schedule.interval_minutes} minutes`) : (zh ? "已关闭，仍可手动生成" : "Disabled; manual generation available")}</p>
      {data.schedule.last_error && <p role="alert" className="text-sm text-red-700">{data.schedule.last_error}</p>}
      {data.periods.length ? <div className="overflow-x-auto"><table className="cc-table w-full text-left text-sm"><thead><tr><th scope="col">{zh ? "报告周期" : "Report period"}</th><th scope="col">{zh ? "记录数" : "Records"}</th><th scope="col">{zh ? "状态" : "State"}</th></tr></thead><tbody>
        {data.periods.map(period => <tr key={period.period_start}><td>{period.period_start} — {period.period_end}<p className="cc-muted text-xs">{period.frequency} · {zh ? "最新观测" : "Latest observation"} {period.latest_observation}</p></td><td>{period.record_count.toLocaleString(lang)}</td><td>{stateLabels[period.state] || period.state}{!period.fresh && <p className="mt-1 text-xs text-amber-700">{zh ? "历史数据，仅手动生成；不能作为当前疫情报告。" : "Historical data; manual generation only. Does not describe current conditions."}</p>}</td></tr>)}
      </tbody></table></div> : <p className="cc-muted text-sm">{zh ? "日本暂无数据充足的完整周期，请先完成日本数据采集。" : "No complete Japan period with enough data. Complete Japan collection first."}</p>}
      <details className="rounded border border-[var(--cc-border)] p-3"><summary className="cursor-pointer text-sm font-medium">{zh ? "可选图表与插入规则" : "Figure catalog and placement rules"}</summary>
        <ul className="mt-3 space-y-3 text-sm">{data.figure_catalog.map(item => <li key={item.figure_type}><strong>{item.title[lang]}</strong><p className="cc-muted text-xs">{item.purpose}</p><p className="cc-muted text-xs">{item.section_type === "data_notes" ? (zh ? "插入“数据口径”节末；仅解释字段缺失，不能据此推断漏报。" : "After Data Notes; field missingness does not establish underreporting.") : item.section_type === "signal_evidence" ? (zh ? "插入“关键证据”节末；仅比较该节疾病的监测关注分。" : "After Signal Evidence; compares review-priority scores for diseases in that section.") : (zh ? "插入“疾病背景”节末；仅限该节讨论的疾病，各种视图选其一；重点疾病可选历史背景，明确标注本报告期。" : "After Disease Context; choose one view for a discussed disease; priority signals may use history with the report window marked.")}</p></li>)}</ul>
        <p className="cc-muted mt-3 text-xs">{zh ? "数据不足时该图不会提供给 AI；禁止自定义代码、改写统计值和任意移动位置。" : "Unavailable charts are excluded from AI choices. Custom code, altered values and arbitrary placement are rejected."}</p>
      </details>
      <div className="flex flex-wrap gap-2"><button type="button" className="cc-button cc-button-primary" disabled={busy || !data.periods.some(period => period.state === "ready")} onClick={() => void generate()}>{busy ? (zh ? "正在提交…" : "Submitting…") : (zh ? "按所示周期生成日本报告" : "Generate Japan report for shown period")}</button><button type="button" className="cc-button" disabled={query.isFetching} onClick={() => { void query.refetch(); void client.invalidateQueries({ queryKey: ["reports"] }); }}>{zh ? "刷新" : "Refresh"}</button><Link className="cc-button" href="/publish">{zh ? "进入增量发布" : "Open incremental publication"}</Link></div>
    </>}
    {result && <p role="status" className="text-sm">{stateLabels[result.status] || result.status}</p>}
    {taskId && <div className="flex flex-wrap items-center gap-3 text-sm"><Link className="text-[var(--cc-link)] underline" href={`/operations/tasks?task=${encodeURIComponent(taskId)}`}>{zh ? "查看生成与评审任务" : "Open generation and review task"}</Link>{task.data && <><StatusBadge status={task.data.status}>{task.data.status}</StatusBadge><span>{task.data.progress}%</span></>}</div>}
    {task.data?.last_error && <p role="alert" className="text-sm text-red-700">{task.data.last_error}</p>}
    {task.error && <p role="alert" className="text-sm text-red-700">{task.error.message}</p>}
  </section>;
}
