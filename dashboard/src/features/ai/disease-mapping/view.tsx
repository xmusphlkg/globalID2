"use client";

import { useState } from "react";
import { Bot, Check, Database, Mail, Play, RotateCw, ShieldCheck, X } from "lucide-react";

import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  useActivateMappingRelease,
  useCreateMappingRelease,
  useMappingAudit,
  useMappingCategories,
  useMappingCoverage,
  useMappingReleases,
  useMappingSummary,
  useReviewCandidate,
  useRunMappingAutomation,
  useSuggestCategory,
} from "@/features/ai/disease-mapping/api";
import { useAppStore } from "@/stores/app-store";

const text = {
  en: {
    eyebrow: "Semantic control plane", title: "Disease Mapping Registry v3",
    subtitle: "Source-first identities, multi-model suggestions, human approval, immutable releases, and email-backed review events for every country.",
    run: "Run automation", release: "Create release", all: "All countries", allStates: "All AI states",
    categories: "Source categories", pending: "Awaiting AI", approved: "Approved mappings", coverage: "Canonical coverage",
    review: "Review queue", releases: "Mapping releases", suggest: "Suggest", accept: "Accept", reject: "Reject", activate: "Activate",
    noRows: "No source categories match these filters.", email: "Email", active: "Active",
    audit: "Old vs v3 migration audit", oldCoverage: "Legacy coverage", safeExcluded: "Unsafe legacy projections removed",
    migrationGaps: "Exact migration gaps", targetChanges: "Unreviewed target changes",
  },
  zh: {
    eyebrow: "语义控制面", title: "疾病映射注册表 v3",
    subtitle: "面向所有国家的源端稳定身份、双模型建议、人工审批、不可变发布版本与邮件审核提醒。",
    run: "运行自动化", release: "创建发布版本", all: "全部国家", allStates: "全部 AI 状态",
    categories: "源疾病项", pending: "等待 AI", approved: "已审核映射", coverage: "规范映射覆盖率",
    review: "映射审核队列", releases: "映射发布版本", suggest: "生成建议", accept: "接受", reject: "拒绝", activate: "激活",
    noRows: "当前筛选条件下没有源疾病项。", email: "邮件", active: "已激活",
    audit: "旧映射与 v3 迁移审计", oldCoverage: "旧映射覆盖率", safeExcluded: "已移除的不安全旧投影",
    migrationGaps: "精确映射迁移缺口", targetChanges: "未经审核的目标变化",
  },
};

const button = "inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong hover:bg-tremor-background-muted disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:bg-dark-tremor-background";

export default function DiseaseMappingView() {
  const lang = useAppStore((state) => state.lang);
  const copy = text[lang];
  const [country, setCountry] = useState("");
  const [aiStatus, setAiStatus] = useState("pending");
  const summary = useMappingSummary();
  const coverage = useMappingCoverage();
  const audit = useMappingAudit();
  const categories = useMappingCategories(country, aiStatus);
  const releases = useMappingReleases();
  const automation = useRunMappingAutomation();
  const suggest = useSuggestCategory();
  const accept = useReviewCandidate("accept");
  const reject = useReviewCandidate("reject");
  const createRelease = useCreateMappingRelease();
  const activate = useActivateMappingRelease();
  const busy = automation.isPending || suggest.isPending || accept.isPending || reject.isPending;
  const releaseCode = () => `DMR-${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 12)}-GLOBAL`;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={copy.eyebrow}
        title={copy.title}
        description={copy.subtitle}
        meta={summary.data?.active_release ? <StatusBadge tone="success">{copy.active}: {summary.data.active_release.release_code}</StatusBadge> : <StatusBadge tone="warning">No active release</StatusBadge>}
        actions={<>
          <button className={button} disabled={automation.isPending} onClick={() => automation.mutate()}><Play className="h-4 w-4" />{copy.run}</button>
          <button className={button} disabled={createRelease.isPending} onClick={() => createRelease.mutate(releaseCode())}><ShieldCheck className="h-4 w-4" />{copy.release}</button>
        </>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label={copy.categories} value={summary.data?.category_total ?? "—"} icon={<Database className="h-5 w-5" />} tone="info" />
        <MetricTile label={copy.pending} value={summary.data?.ai_pending_total ?? "—"} icon={<Bot className="h-5 w-5" />} tone="warning" />
        <MetricTile label={copy.approved} value={summary.data?.assertions?.approved ?? 0} icon={<Check className="h-5 w-5" />} tone="success" />
        <MetricTile label={copy.coverage} value={coverage.data ? `${(coverage.data.canonical_coverage * 100).toFixed(1)}%` : "—"} hint={coverage.data ? `${coverage.data.canonical_total.toLocaleString()} / ${coverage.data.observation_total.toLocaleString()}` : undefined} icon={<ShieldCheck className="h-5 w-5" />} tone="primary" />
      </div>

      <section className="app-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">{copy.audit}</h2>
          <div className="flex flex-wrap gap-2">
            <StatusBadge tone={audit.data?.quality_gates.no_orphan_observations ? "success" : "danger"}>orphan {audit.data?.quality_gates.orphan_observations ?? "—"}</StatusBadge>
            <StatusBadge tone={audit.data?.quality_gates.single_mapping_per_category ? "success" : "danger"}>conflicts {audit.data?.quality_gates.active_release_conflicts ?? "—"}</StatusBadge>
          </div>
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricTile label={copy.oldCoverage} value={audit.data ? `${(audit.data.totals.old_coverage * 100).toFixed(1)}%` : "—"} tone="neutral" />
          <MetricTile label={copy.safeExcluded} value={audit.data?.totals.semantic_safety_exclusion ?? "—"} tone="success" />
          <MetricTile label={copy.migrationGaps} value={audit.data?.totals.exact_migration_gap ?? "—"} tone={audit.data?.totals.exact_migration_gap ? "danger" : "success"} />
          <MetricTile label={copy.targetChanges} value={audit.data?.totals.changed_target ?? "—"} tone={audit.data?.totals.changed_target ? "danger" : "success"} />
        </div>
        {audit.data?.top_gaps.length ? <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs uppercase text-tremor-content-subtle"><tr><th className="py-2">Country</th><th>Source category</th><th>Old target</th><th>Relation</th><th className="text-right">Rows</th></tr></thead><tbody className="divide-y divide-tremor-border dark:divide-dark-tremor-border">{audit.data.top_gaps.slice(0, 8).map((gap) => <tr key={`${gap.series_code}-${gap.root_cause}`}><td className="py-2"><StatusBadge>{gap.country_code}</StatusBadge></td><td>{gap.source_label}</td><td className="font-mono">{gap.old_target}</td><td><StatusBadge tone={gap.root_cause === "semantic_safety_exclusion" ? "success" : "warning"}>{gap.mapping_relation}</StatusBadge></td><td className="text-right">{gap.observations.toLocaleString()}</td></tr>)}</tbody></table></div> : null}
      </section>

      <section className="app-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">{copy.review}</h2>
          <div className="flex gap-2">
            <select className={button} value={country} onChange={(event) => setCountry(event.target.value)}>
              <option value="">{copy.all}</option>
              {summary.data?.countries.map((item) => <option key={item.country_code} value={item.country_code}>{item.country_code} ({item.categories})</option>)}
            </select>
            <select className={button} value={aiStatus} onChange={(event) => setAiStatus(event.target.value)}>
              <option value="">{copy.allStates}</option><option value="pending">pending</option><option value="completed">completed</option><option value="failed">failed</option><option value="not_required">not_required</option>
            </select>
          </div>
        </div>
        <div className="mt-4 space-y-3">
          {!categories.data?.length ? <p className="text-sm text-tremor-content-subtle">{categories.isLoading ? "Loading…" : copy.noRows}</p> : categories.data.map((category) => (
            <article key={category.id} className="rounded-tremor-default border border-tremor-border p-4 dark:border-dark-tremor-border">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><div className="flex flex-wrap gap-2"><StatusBadge tone="info">{category.country_code}</StatusBadge><StatusBadge>{category.source_id}</StatusBadge><StatusBadge status={category.ai_status}>{category.ai_status}</StatusBadge></div><h3 className="mt-2 font-semibold">{category.canonical_source_label}</h3><p className="mt-1 text-xs text-tremor-content-subtle">{category.source_code}</p></div>
                {category.ai_status !== "not_required" && !category.candidates.length ? <button className={button} disabled={busy} onClick={() => suggest.mutate(category.id)}><RotateCw className="h-4 w-4" />{copy.suggest}</button> : null}
              </div>
              {category.candidates.map((candidate) => (
                <div key={candidate.id} className="mt-3 rounded-tremor-default bg-tremor-background-subtle p-3 dark:bg-dark-tremor-background-subtle">
                  <div className="flex flex-wrap items-center gap-2"><StatusBadge tone="primary">#{candidate.target_code || candidate.proposed_name_en || "unmapped"}</StatusBadge><StatusBadge>{candidate.mapping_relation}</StatusBadge><StatusBadge>{candidate.comparability}</StatusBadge><StatusBadge tone={candidate.confidence_score >= .8 ? "success" : "warning"}>{Math.round(candidate.confidence_score * 100)}%</StatusBadge></div>
                  {candidate.reasoning ? <p className="mt-2 text-sm leading-6">{candidate.reasoning}</p> : null}
                  <div className="mt-3 flex gap-2"><button className={button} disabled={busy || candidate.candidate_kind === "new_concept"} onClick={() => accept.mutate(candidate.id)}><Check className="h-4 w-4" />{copy.accept}</button><button className={button} disabled={busy} onClick={() => reject.mutate(candidate.id)}><X className="h-4 w-4" />{copy.reject}</button></div>
                </div>
              ))}
            </article>
          ))}
        </div>
      </section>

      <section className="app-panel p-4">
        <div className="flex items-center justify-between"><h2 className="text-base font-semibold">{copy.releases}</h2><StatusBadge tone="info"><Mail className="mr-1 h-3 w-3" />{copy.email}: {summary.data?.automation.email_provider ?? "—"}</StatusBadge></div>
        <div className="mt-4 divide-y divide-tremor-border dark:divide-dark-tremor-border">
          {releases.data?.map((release) => <div key={release.id} className="flex flex-wrap items-center justify-between gap-3 py-3"><div><span className="font-mono text-sm font-semibold">{release.release_code}</span><div className="mt-1 flex gap-2"><StatusBadge status={release.status}>{release.status}</StatusBadge><span className="text-xs text-tremor-content-subtle">{release.metadata?.assertion_count ?? 0} assertions · {release.checksum.slice(0, 12)}</span></div></div>{release.status === "draft" ? <button className={button} disabled={activate.isPending} onClick={() => activate.mutate(release.id)}>{copy.activate}</button> : null}</div>)}
        </div>
      </section>
    </div>
  );
}
