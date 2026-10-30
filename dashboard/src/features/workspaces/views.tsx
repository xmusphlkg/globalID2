"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, BrainCircuit, Cpu, FileText, Settings2, ShieldCheck } from "lucide-react";
import { WorkspacePage, MetricStrip, StatusBadge } from "@/shared/ui";
import { t, type LangKey } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";
import { apiFetch } from "@/lib/api";
import type { components } from "@/generated/api";
import { useMappingCoverage, useMappingSummary } from "@/features/ai/disease-mapping/api";
import { usePaginatedTasks } from "@/features/operations/tasks/api";
import { AI_TASK_TYPES, taskScopeHref } from "@/features/operations/task-scope";
import { CreateAITaskModal } from "@/features/ai/tasks/CreateAITaskModal";
import { CreateDiseaseKnowledgeTaskModal } from "@/features/ai/tasks/CreateDiseaseKnowledgeTaskModal";

function Feedback({ query }: { query: { isLoading: boolean; isFetching: boolean; error: unknown; refetch: () => unknown } }) {
  const { lang } = useAppStore();
  if (query.isLoading) return <p role="status" className="cc-muted animate-pulse">{t(lang,"cc_loading")}</p>;
  return query.error ? <div role="alert" className="cc-panel border-rose-300"><p>{t(lang,"cc_load_failed")}</p><p className="cc-muted break-words">{query.error instanceof Error ? query.error.message : String(query.error)}</p><button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{t(lang,"cc_retry_load")}</button></div> : null;
}
function ToolCard({ href, title, description, icon: Icon = ArrowRight }: { href: string; title: LangKey; description: LangKey; icon?: typeof ArrowRight }) {
  const { lang } = useAppStore();
  return <Link href={href} className="cc-panel flex flex-col hover:border-[var(--cc-brand)]"><Icon className="h-5 w-5 text-[var(--cc-brand)]" /><h2 className="font-semibold">{t(lang,title)}</h2><p className="cc-muted flex-1">{t(lang,description)}</p><span className="flex items-center gap-2 text-sm font-medium text-[var(--cc-link)]">{t(lang,"wf_open")}<ArrowRight className="h-4 w-4" /></span></Link>;
}
export function GovernanceView() {
  const { lang } = useAppStore();
  const summary = useMappingSummary();
  const coverage = useMappingCoverage();
  const countries = [...(coverage.data?.countries ?? [])].sort((a,b) => b.holding_series_count - a.holding_series_count || a.mapping_coverage - b.mapping_coverage);
  return <WorkspacePage eyebrow={t(lang,"cc_center")} title={t(lang,"wf_governance")} description={t(lang,"wf_governance_desc")} actions={<button className="cc-button" disabled={summary.isFetching || coverage.isFetching} onClick={() => { void summary.refetch(); void coverage.refetch(); }}>{t(lang,"cc_refresh")}</button>}>
    <Feedback query={summary} /><Feedback query={coverage} />
    <MetricStrip items={[
      { label:t(lang,"wf_mapping_pending"), value:coverage.data?.holding_series_total ?? "—", tone:coverage.data?.holding_series_total ? "warning" : "neutral" },
      { label:t(lang,"wf_mapping_candidates"),value:summary.data?.ai_review_ready_total ?? "—",tone:summary.data?.ai_review_ready_total ? "warning" : "neutral" },
      { label:t(lang,"wf_mapping_coverage"),value:coverage.data ? `${(coverage.data.mapping_coverage * 100).toFixed(1)}%` : "—" },
      { label:t(lang,"wf_mapping_active"),value:summary.data?.active_release?.release_code ?? "—" },
    ]} />
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <ToolCard href="/data/quality" title="wf_quality" description="wf_quality_desc" icon={ShieldCheck} />
      <ToolCard href="/data/mappings" title="wf_mapping" description="wf_mapping_desc" />
      <ToolCard href="/data/audit" title="wf_duplicates" description="wf_duplicates_desc" />
      <ToolCard href="/data/explorer" title="wf_inspection" description="wf_inspection_desc" />
    </div>
    <section className="cc-panel"><h2 className="font-semibold">{t(lang,"wf_mapping")} · {t(lang,"country")}</h2>
      {coverage.data && <div className="overflow-x-auto"><table className="cc-table"><thead><tr><th>{t(lang,"country")}</th><th>{t(lang,"wf_mapping_pending")}</th><th>{t(lang,"wf_mapping_coverage")}</th><th>{t(lang,"wf_resolve")}</th></tr></thead><tbody>{countries.map(country => <tr key={country.country_code}><td className="font-medium">{country.country_code}</td><td><StatusBadge tone={country.holding_series_count ? "warning" : "neutral"}>{country.holding_series_count}</StatusBadge></td><td>{(country.mapping_coverage * 100).toFixed(1)}%</td><td><Link className="text-[var(--cc-link)] underline" href={`/data/mappings?country=${encodeURIComponent(country.country_code)}`}>{t(lang,"wf_mapping")}</Link></td></tr>)}</tbody></table>{!countries.length && <p className="cc-muted py-4">{t(lang,"no_data")}</p>}</div>}
    </section>
    <div className="grid gap-4 sm:grid-cols-2"><ToolCard href="/data/knowledge" title="wf_catalogue" description="wf_knowledge_readonly" icon={BookOpen} /><ToolCard href="/data/literature" title="wf_research" description="wf_view_evidence" icon={BookOpen} /></div>
  </WorkspacePage>;
}
export function AIWorkbenchView() {
  const { lang, countryCode } = useAppStore();
  const [form, setForm] = useState<"report" | "knowledge" | null>(null);
  const tasks = usePaginatedTasks("queued,pending,running,retrying",AI_TASK_TYPES.join(","),undefined,undefined,8,0);
  const failed = usePaginatedTasks("failed",AI_TASK_TYPES.join(","),undefined,undefined,1,0);
  const summary = useQuery({queryKey:["/collection/summary"],queryFn:() => apiFetch<{model_queue:{queued:number;leased:number}}>("/collection/summary"),refetchInterval:15000});
  return <WorkspacePage eyebrow={t(lang,"cc_center")} title={t(lang,"wf_ai_home")} description={t(lang,"wf_ai_desc")} actions={<><button className="cc-button" onClick={() => setForm("knowledge")}>{t(lang,"wf_create_brief")}</button><button className="cc-button-primary" disabled={!countryCode} title={!countryCode ? t(lang,"wf_country_needed") : undefined} onClick={() => setForm("report")}>{t(lang,"wf_create_report")}</button></>}>
    <Feedback query={tasks} /><Feedback query={failed} /><Feedback query={summary} />
    <MetricStrip items={[{label:t(lang,"wf_ai_business"),value:tasks.data?.totalCount ?? "—",tone:"warning"},{label:t(lang,"wf_ai_failed"),value:failed.data?.totalCount ?? "—",tone:failed.data?.totalCount ? "danger" : "neutral"},{label:t(lang,"wf_ai_waiting"),value:summary.data?.model_queue.queued ?? "—"},{label:t(lang,"wf_ai_leased"),value:summary.data?.model_queue.leased ?? "—"}]} />
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"><ToolCard href="/models/tasks" title="tasks" description="wf_ai_task_desc" icon={BrainCircuit} /><ToolCard href="/models/queue" title="wf_queue" description="wf_ai_queue_desc" icon={Cpu} /><ToolCard href="/models/agents" title="wf_agents" description="wf_ai_agents_desc" /><ToolCard href="/models/reports" title="wf_reports" description="wf_ai_reports_desc" icon={FileText} /><ToolCard href="/models/knowledge" title="wf_briefs" description="wf_ai_briefs_desc" icon={BookOpen} /><ToolCard href="/models/literature" title="wf_literature_ai" description="wf_ai_literature_desc" icon={BookOpen} /></div>
    <section className="cc-panel"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">{t(lang,"wf_ai_recent")}</h2><Link className="text-sm text-[var(--cc-link)] underline" href="/models/tasks">{t(lang,"cc_view_all")}</Link></div>
      {tasks.data?.items.map(task => <Link className="flex flex-wrap items-center justify-between gap-3 border-t border-tremor-border py-3 text-sm" key={task.task_uuid} href={taskScopeHref(task)}><span className="min-w-0 break-words font-medium">{task.task_name}</span><StatusBadge status={task.status}>{t(lang,`cc_${task.status}` as LangKey)}</StatusBadge></Link>)}{tasks.data && !tasks.data.items.length && <p className="cc-muted">{t(lang,"cc_no_runs")}</p>}
    </section>
    {form === "report" && <CreateAITaskModal open countryCode={countryCode} lang={lang} onClose={() => setForm(null)} />}
    {form === "knowledge" && <CreateDiseaseKnowledgeTaskModal open lang={lang} onClose={() => setForm(null)} />}
  </WorkspacePage>;
}
export function SettingsWorkbenchView() {
  const { lang } = useAppStore();
  const query = useQuery({queryKey:["settings"],queryFn:() => apiFetch<components["schemas"]["RuntimeSettingsOut"]>("/settings")});
  const data = query.data;
  const sections = data ? [
    { id:"smtp",title:"wf_smtp",ready:data.smtp.smtp_configured,detail:data.smtp.smtp_host },
    { id:"github",title:"wf_github",ready:data.github.github_configured,detail:data.github.github_data_share_repo_url },
    { id:"archive",title:"wf_archive",ready:data.github.raw_archive_configured,detail:data.github.raw_archive_repo_url },
    { id:"cloudflare",title:"wf_hosting",ready:data.cloudflare.cloudflare_configured,detail:data.cloudflare.default_cloudflare_project_name },
    { id:"site",title:"wf_site",ready:data.site.ga4_configured,detail:data.site.public_ga4_measurement_id },
  ] : [];
  return <WorkspacePage eyebrow={t(lang,"cc_center")} title={t(lang,"settings")} description={t(lang,"wf_settings_desc")} actions={<button className="cc-button" disabled={query.isFetching} onClick={() => query.refetch()}>{t(lang,"cc_refresh")}</button>}>
    <Feedback query={query} /><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{sections.sort((a,b) => Number(a.ready)-Number(b.ready)).map(item => <Link href={`/settings/integrations?section=${item.id}`} key={item.id} className="cc-panel hover:border-[var(--cc-brand)]"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{t(lang,item.title as LangKey)}</h2><StatusBadge tone={item.ready ? "success" : "warning"}>{t(lang,item.ready ? "wf_ready" : "wf_needs_setup")}</StatusBadge></div><p className="cc-muted break-all">{item.detail || "—"}</p><span className="text-sm text-[var(--cc-link)]">{t(lang,"wf_configuration")} →</span></Link>)}<ToolCard href="/settings/models" title="wf_providers" description="wf_providers_desc" icon={Settings2} /></div>
    <section className="cc-panel"><h2 className="font-semibold">{t(lang,"wf_operations_links")}</h2><p className="cc-muted">{t(lang,"wf_settings_boundary")}</p><div className="flex flex-wrap gap-2"><Link className="cc-button" href="/collection/sources/ingestion">{t(lang,"wf_collection")}</Link><Link className="cc-button" href="/collection/schedules">{t(lang,"cc_schedules")}</Link><Link className="cc-button" href="/publish">{t(lang,"publish")}</Link></div></section>
  </WorkspacePage>;
}
