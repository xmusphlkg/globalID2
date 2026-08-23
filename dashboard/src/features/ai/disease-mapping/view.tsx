"use client";

import { useState } from "react";
import { Bot, Check, Database, Mail, Play, RotateCw, ShieldCheck, X } from "lucide-react";

import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  useActivateMappingRelease,
  useCreateMappingRelease,
  useMappingCategories,
  useMappingCoverage,
  useMappingReleases,
  useMappingSummary,
  useReviewCandidate,
  useRunMappingAutomation,
  useSuggestCategory,
  useSyncReviewedMappings,
} from "@/features/ai/disease-mapping/api";
import { getCountryDisplayName, useCountries } from "@/lib/hooks/useCountries";
import { useAppStore } from "@/stores/app-store";

const text = {
  en: {
    eyebrow: "Data governance", title: "Disease mappings",
    subtitle: "Review source categories, approve mappings, publish releases, and monitor current mapping decisions.",
    run: "Run automation", sync: "Sync reviewed mappings", release: "Create release", all: "All countries", allStates: "All AI states",
    categories: "Source categories", pending: "Automation backlog", failed: "internal processing errors", providerUnavailable: "model unavailable (retrying)", providerPaused: "provider paused", approved: "Approved mappings", coverage: "Decision coverage", holding: "holding",
    review: "Review queue", releases: "Mapping releases", suggest: "Suggest", accept: "Accept & publish", reject: "Reject", activate: "Activate",
    noRows: "No source categories match these filters.", email: "Email", active: "Active",
    countryCoverage: "Current decisions by country / region", coverageNote: "Observation counts cover the current source-series Registry. The series column makes that scope explicit; decision coverage includes both canonical projections and reviewed source-only decisions.",
    country: "Country / region", observations: "Registry observations", series: "Observed / registered series", decided: "Decided", canonical: "Canonical", sourceOnly: "Source-only", undecided: "Undecided",
    errorReason: "Automation detail",
  },
  zh: {
    eyebrow: "数据治理", title: "疾病映射",
    subtitle: "审核源疾病项、批准映射、发布版本，并监控当前映射决定覆盖情况。",
    run: "运行自动化", sync: "同步映射清单", release: "创建发布版本", all: "全部国家", allStates: "全部 AI 状态",
    categories: "源疾病项", pending: "自动化待处理", failed: "内部处理异常", providerUnavailable: "模型暂不可用（会重试）", providerPaused: "模型路由已暂停", approved: "已审核映射", coverage: "映射决定覆盖率", holding: "隔离区",
    review: "映射审核队列", releases: "映射发布版本", suggest: "生成建议", accept: "接受并发布", reject: "拒绝", activate: "激活",
    noRows: "当前筛选条件下没有源疾病项。", email: "邮件", active: "已激活",
    countryCoverage: "当前各国家或地区映射决定", coverageNote: "观测数只统计已经进入来源系列 Registry 的数据；“已观测/已注册系列”会明确展示统计范围。映射决定覆盖率同时包含规范投影和已审核的仅保留来源决定。",
    country: "国家 / 地区", observations: "Registry 观测数", series: "已观测 / 已注册系列", decided: "已决定", canonical: "规范投影", sourceOnly: "仅保留来源", undecided: "未决定",
    errorReason: "自动化说明",
  },
};

const button = "inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong hover:bg-tremor-background-muted disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:bg-dark-tremor-background";

export default function DiseaseMappingView() {
  const lang = useAppStore((state) => state.lang);
  const copy = text[lang];
  const [country, setCountry] = useState("");
  const [aiStatus, setAiStatus] = useState("no_model");
  const summary = useMappingSummary();
  const coverage = useMappingCoverage();
  const countries = useCountries();
  const categories = useMappingCategories(country, aiStatus);
  const releases = useMappingReleases();
  const automation = useRunMappingAutomation();
  const syncMappings = useSyncReviewedMappings();
  const suggest = useSuggestCategory();
  const accept = useReviewCandidate("accept");
  const reject = useReviewCandidate("reject");
  const createRelease = useCreateMappingRelease();
  const activate = useActivateMappingRelease();
  const busy = automation.isPending || syncMappings.isPending || suggest.isPending || accept.isPending || reject.isPending;
  const releaseCode = () => `DMR-${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 12)}-GLOBAL`;
  const countryByCode = new Map((countries.data ?? []).map((item) => [item.code.toUpperCase(), item]));
  const countryName = (code: string) => {
    const item = countryByCode.get(code.toUpperCase());
    return item ? getCountryDisplayName(item, lang) : code;
  };
  const aiStatusLabel = (status: string) => ({
    pending: lang === "zh" ? "待生成建议" : "Awaiting suggestions",
    processing: lang === "zh" ? "正在处理" : "Processing",
    completed: lang === "zh" ? "待人工审核" : "Ready for review",
    no_model: copy.providerUnavailable,
    failed: copy.failed,
    not_required: lang === "zh" ? "已审核 / 无需 AI" : "Reviewed / AI not required",
  }[status] ?? status);
  const readableAutomationError = (error?: string | null) => {
    if (!error) return "";
    if (error.startsWith("All active AI model routes failed")) {
      return lang === "zh"
        ? "所有已启用模型路由均未成功响应；系统会按退避策略重试，疾病本身并未判定失败。"
        : "All enabled model routes were unavailable. The system will retry with backoff; the disease mapping itself was not judged to have failed.";
    }
    if (/quota|403/i.test(error)) {
      return lang === "zh" ? "模型额度或访问权限暂不可用，系统会重试。" : "Model quota or access is temporarily unavailable; the system will retry.";
    }
    return error.length > 360 ? `${error.slice(0, 360)}…` : error;
  };

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={copy.eyebrow}
        title={copy.title}
        description={copy.subtitle}
        meta={summary.data?.active_release ? <StatusBadge tone="success">{copy.active}: {summary.data.active_release.release_code}</StatusBadge> : <StatusBadge tone="warning">No active release</StatusBadge>}
        actions={<>
          <button className={button} disabled={syncMappings.isPending} onClick={() => syncMappings.mutate()}><RotateCw className="h-4 w-4" />{copy.sync}</button>
          <button className={button} disabled={automation.isPending} onClick={() => automation.mutate()}><Play className="h-4 w-4" />{copy.run}</button>
          <button className={button} disabled={createRelease.isPending} onClick={() => createRelease.mutate(releaseCode())}><ShieldCheck className="h-4 w-4" />{copy.release}</button>
        </>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label={copy.categories} value={summary.data?.category_total ?? "—"} icon={<Database className="h-5 w-5" />} tone="info" />
        <MetricTile label={copy.pending} value={summary.data ? (summary.data.ai_pending_total + summary.data.ai_provider_unavailable_total).toLocaleString() : "—"} hint={summary.data ? `${summary.data.ai_provider_unavailable_total.toLocaleString()} ${copy.providerUnavailable} · ${summary.data.ai_internal_failed_total.toLocaleString()} ${copy.failed}${summary.data.automation.ai_circuit_open ? ` · ${copy.providerPaused}` : ""}` : undefined} icon={<Bot className="h-5 w-5" />} tone="warning" />
        <MetricTile label={copy.approved} value={summary.data?.assertions?.approved ?? 0} icon={<Check className="h-5 w-5" />} tone="success" />
        <MetricTile label={copy.coverage} value={coverage.data ? `${(coverage.data.mapping_coverage * 100).toFixed(1)}%` : "—"} hint={coverage.data ? `${coverage.data.mapped_total.toLocaleString()} / ${coverage.data.observation_total.toLocaleString()} · ${coverage.data.source_only_total.toLocaleString()} ${copy.sourceOnly.toLocaleLowerCase()} · ${coverage.data.holding_observation_total.toLocaleString()} ${copy.holding}` : undefined} icon={<ShieldCheck className="h-5 w-5" />} tone="primary" />
      </div>

      <section className="app-panel p-4">
        <h2 className="text-base font-semibold">{copy.countryCoverage}</h2>
        <p className="mt-1 max-w-4xl text-sm leading-6 text-tremor-content-subtle">{copy.coverageNote}</p>
        <div className="mt-4 overflow-x-auto" role="region" aria-label={copy.countryCoverage} tabIndex={0}>
          <table className="w-full min-w-[1040px] text-left text-sm">
            <thead className="text-xs uppercase text-tremor-content-subtle">
              <tr><th className="py-2">{copy.country}</th><th className="text-right">{copy.observations}</th><th className="text-right">{copy.series}</th><th className="text-right">{copy.decided}</th><th className="text-right">{copy.canonical}</th><th className="text-right">{copy.sourceOnly}</th><th className="text-right">{copy.undecided}</th><th className="text-right">{copy.coverage}</th></tr>
            </thead>
            <tbody className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
              {coverage.data?.countries.map((item) => (
                <tr key={item.country_code}>
                  <td className="py-2"><div className="flex items-center gap-2"><span className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{countryName(item.country_code)}</span><StatusBadge>{item.country_code}</StatusBadge></div></td>
                  <td className="text-right tabular-nums">{item.observation_count.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{item.observed_series_count.toLocaleString()} / {item.registered_series_count.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{item.mapped_count.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{item.canonical_count.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{item.source_only_count.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{item.undecided_count.toLocaleString()}</td>
                  <td className="text-right"><StatusBadge tone={item.mapping_coverage === 1 ? "success" : item.mapping_coverage >= .99 ? "warning" : "danger"}>{(item.mapping_coverage * 100).toFixed(1)}%</StatusBadge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="app-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">{copy.review}</h2>
          <div className="flex gap-2">
            <select aria-label={lang === "zh" ? "国家筛选" : "Country filter"} className={button} value={country} onChange={(event) => setCountry(event.target.value)}>
              <option value="">{copy.all}</option>
              {summary.data?.countries.map((item) => <option key={item.country_code} value={item.country_code}>{countryName(item.country_code)} ({item.country_code}) · {item.categories}</option>)}
            </select>
            <select aria-label={lang === "zh" ? "AI 状态筛选" : "AI status filter"} className={button} value={aiStatus} onChange={(event) => setAiStatus(event.target.value)}>
              <option value="">{copy.allStates}</option><option value="pending">{aiStatusLabel("pending")}</option><option value="processing">{aiStatusLabel("processing")}</option><option value="completed">{aiStatusLabel("completed")}</option><option value="no_model">{aiStatusLabel("no_model")}</option><option value="failed">{aiStatusLabel("failed")}</option><option value="not_required">{aiStatusLabel("not_required")}</option>
            </select>
          </div>
        </div>
        <div className="mt-4 space-y-3">
          {!categories.data?.length ? <p className="text-sm text-tremor-content-subtle">{categories.isLoading ? "Loading…" : copy.noRows}</p> : categories.data.map((category) => (
            <article key={category.id} className="rounded-tremor-default border border-tremor-border p-4 dark:border-dark-tremor-border">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><div className="flex flex-wrap gap-2"><StatusBadge tone="info">{countryName(category.country_code)} · {category.country_code}</StatusBadge><StatusBadge>{category.source_id}</StatusBadge><StatusBadge status={category.ai_status}>{aiStatusLabel(category.ai_status)}</StatusBadge></div><h3 className="mt-2 font-semibold">{category.canonical_source_label}</h3><p className="mt-1 text-xs text-tremor-content-subtle">{category.source_code} · {category.definition_version}</p></div>
                {category.ai_status !== "not_required" && !category.candidates.length ? <button className={button} disabled={busy} onClick={() => suggest.mutate(category.category_key)}><RotateCw className="h-4 w-4" />{copy.suggest}</button> : null}
              </div>
              {category.automation_failure_kind ? <p className="mt-3 rounded-tremor-default bg-amber-50 p-3 text-sm leading-6 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200"><span className="font-semibold">{copy.errorReason}: </span>{readableAutomationError(category.ai_last_error)}</p> : null}
              {category.candidates.map((candidate) => (
                <div key={candidate.id} className="mt-3 rounded-tremor-default bg-tremor-background-subtle p-3 dark:bg-dark-tremor-background-subtle">
                  <div className="flex flex-wrap items-center gap-2"><StatusBadge tone="primary">#{candidate.target_code || candidate.proposed_name_en || "unmapped"}</StatusBadge><StatusBadge>{candidate.mapping_relation}</StatusBadge><StatusBadge>{candidate.comparability}</StatusBadge><StatusBadge tone={candidate.confidence_score >= .8 ? "success" : "warning"}>{Math.round(candidate.confidence_score * 100)}%</StatusBadge></div>
                  {candidate.reasoning ? <p className="mt-2 text-sm leading-6">{candidate.reasoning}</p> : null}
                  <div className="mt-3 flex gap-2"><button className={button} disabled={busy || candidate.candidate_kind === "new_concept"} onClick={() => accept.mutate(candidate.candidate_key)}><Check className="h-4 w-4" />{copy.accept}</button><button className={button} disabled={busy} onClick={() => reject.mutate(candidate.candidate_key)}><X className="h-4 w-4" />{copy.reject}</button></div>
                </div>
              ))}
            </article>
          ))}
        </div>
      </section>

      <section className="app-panel p-4">
        <div className="flex items-center justify-between"><h2 className="text-base font-semibold">{copy.releases}</h2><StatusBadge tone="info"><Mail className="mr-1 h-3 w-3" />{copy.email}: {summary.data?.automation.email_provider ?? "—"}</StatusBadge></div>
        <div className="mt-4 divide-y divide-tremor-border dark:divide-dark-tremor-border">
          {releases.data?.map((release) => <div key={release.release_code} className="flex flex-wrap items-center justify-between gap-3 py-3"><div><span className="font-mono text-sm font-semibold">{release.release_code}</span><div className="mt-1 flex gap-2"><StatusBadge status={release.status}>{release.status}</StatusBadge><span className="text-xs text-tremor-content-subtle">{release.metadata?.assertion_count ?? 0} assertions · {release.checksum.slice(0, 12)}</span></div></div>{release.status === "draft" ? <button className={button} disabled={activate.isPending} onClick={() => activate.mutate(release.release_code)}>{copy.activate}</button> : null}</div>)}
        </div>
      </section>
    </div>
  );
}
