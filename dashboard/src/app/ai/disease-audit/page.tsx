"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, FileSearch, GitMerge, PlusCircle, ShieldCheck } from "lucide-react";
import { Badge, Button, Card, Text, Title } from "@tremor/react";
import { useAppStore } from "@/stores/app-store";
import {
  type DiseaseAuditFinding,
  type DiseaseAuditRecommendation,
  useDiseaseDuplicateAuditStatus,
  useRunDiseaseDuplicateAudit,
} from "@/lib/hooks/useDiseaseDuplicateAudit";

const copy = {
  en: {
    eyebrow: "AI Programs",
    title: "Disease Ontology Audit",
    subtitle: "Use the model center to review duplicate disease concepts and newly observed unmapped disease terms.",
    runLocal: "Run local audit",
    runAI: "Run with AI",
    includeNew: "Scan current data for new disease candidates",
    maxCandidates: "Max AI candidates",
    modelRoutes: "Active model routes",
    noRoutes: "No active model-center routes available.",
    routeMissing: "Audit API route was not found. Restart the FastAPI backend so it loads the latest code.",
    highDuplicates: "High-confidence duplicates",
    mappingCandidates: "Mapping-term review",
    similarCandidates: "Similar-name review",
    newCandidates: "New disease candidates",
    aiReview: "AI recommendations",
    noData: "Run an audit to see findings.",
    noFindings: "No findings in this section.",
    generatedAt: "Generated",
    modelUsed: "Model used",
    loading: "Analyzing...",
    error: "Audit failed",
    summary: "Summary",
    merge: "Merge",
    keep: "Keep separate",
    add: "Add disease",
    review: "Human review",
  },
  zh: {
    eyebrow: "AI 程序",
    title: "疾病本体审计",
    subtitle: "通过模型中心复核疾病重复实体，并识别当前数据中新出现但未映射的传染病候选。",
    runLocal: "仅运行本地审计",
    runAI: "使用 AI 分析",
    includeNew: "扫描当前数据中的新增疾病候选",
    maxCandidates: "AI 候选上限",
    modelRoutes: "可用模型路由",
    noRoutes: "当前没有可用的模型中心路由。",
    routeMissing: "没有找到审计 API 路由。请重启 FastAPI 后端，让它加载最新代码。",
    highDuplicates: "高置信重复",
    mappingCandidates: "映射术语复核",
    similarCandidates: "相似名称复核",
    newCandidates: "新增疾病候选",
    aiReview: "AI 建议",
    noData: "运行一次审计后查看结果。",
    noFindings: "本区暂无发现。",
    generatedAt: "生成时间",
    modelUsed: "使用模型",
    loading: "分析中...",
    error: "审计失败",
    summary: "汇总",
    merge: "建议合并",
    keep: "建议保留",
    add: "建议新增",
    review: "人工复核",
  },
};

function decisionColor(decision?: string) {
  if (decision === "merge") return "emerald";
  if (decision === "add_standard_disease") return "blue";
  if (decision === "keep_separate") return "slate";
  return "amber";
}

function decisionLabel(decision: string | undefined, lang: "en" | "zh") {
  if (decision === "merge") return copy[lang].merge;
  if (decision === "add_standard_disease") return copy[lang].add;
  if (decision === "keep_separate") return copy[lang].keep;
  return copy[lang].review;
}

function FindingList({ title, items }: { title: string; items?: DiseaseAuditFinding[] }) {
  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <Title className="text-base">{title}</Title>
        <Badge color={items?.length ? "amber" : "emerald"}>{items?.length ?? 0}</Badge>
      </div>
      {!items?.length ? (
        <Text className="text-sm text-tremor-content-subtle">No findings in this section.</Text>
      ) : (
        <div className="max-h-[420px] space-y-3 overflow-y-auto pr-1">
          {items.map((item, index) => (
            <div key={`${item.category}-${index}`} className="rounded-2xl border border-tremor-border bg-tremor-background-subtle/60 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge color="slate">{item.category}</Badge>
                {item.candidate_ids?.map((id) => (
                  <Badge key={id} color="blue">{id}</Badge>
                ))}
                {item.country_code ? <Badge color="emerald">{item.country_code}</Badge> : null}
                {item.row_count ? <Badge color="amber">{item.row_count} rows</Badge> : null}
              </div>
              <p className="mt-2 text-sm leading-6 text-tremor-content-strong">{item.finding}</p>
              {item.raw_terms?.length ? (
                <p className="mt-1 text-xs text-tremor-content-subtle">Examples: {item.raw_terms.slice(0, 4).join(", ")}</p>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function RecommendationList({
  items,
  lang,
}: {
  items?: DiseaseAuditRecommendation[];
  lang: "en" | "zh";
}) {
  if (!items?.length) {
    return <Text className="text-sm text-tremor-content-subtle">{copy[lang].noFindings}</Text>;
  }

  return (
    <div className="space-y-3">
      {items.map((item, index) => (
        <div key={`${item.decision}-${index}`} className="rounded-2xl border border-tremor-border bg-white/80 p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge color={decisionColor(item.decision)}>{decisionLabel(item.decision, lang)}</Badge>
            {item.confidence ? <Badge color="slate">{item.confidence}</Badge> : null}
            {item.canonical_id ? <Badge color="blue">canonical {item.canonical_id}</Badge> : null}
            {item.merge_ids?.map((id) => <Badge key={id} color="emerald">merge {id}</Badge>)}
          </div>
          <p className="mt-3 text-sm font-medium leading-6 text-tremor-content-strong">{item.finding}</p>
          {(item.proposed_standard_name_en || item.proposed_standard_name_zh) ? (
            <p className="mt-2 text-sm text-tremor-content">
              Proposed: {item.proposed_standard_name_en || "-"} / {item.proposed_standard_name_zh || "-"}
            </p>
          ) : null}
          <p className="mt-2 text-sm leading-6 text-tremor-content">
            {lang === "zh" ? item.rationale_zh || item.rationale_en : item.rationale_en || item.rationale_zh}
          </p>
        </div>
      ))}
    </div>
  );
}

export default function DiseaseAuditPage() {
  const { lang } = useAppStore();
  const ui = copy[lang];
  const [includeNew, setIncludeNew] = useState(true);
  const [maxCandidates, setMaxCandidates] = useState(40);
  const { data: status, error: statusError } = useDiseaseDuplicateAuditStatus(includeNew);
  const runAudit = useRunDiseaseDuplicateAudit();
  const result = runAudit.data;

  const activeRoutes = useMemo(
    () => (status?.model_center.routes ?? []).filter((route) => route.available_for_routing),
    [status],
  );

  const run = (includeAI: boolean) => {
    runAudit.mutate({
      include_ai: includeAI,
      include_new_disease_candidates: includeNew,
      max_ai_candidates: maxCandidates,
    });
  };

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="overflow-hidden rounded-[2rem] border border-emerald-200 bg-[radial-gradient(circle_at_top_left,rgba(20,184,166,0.24),transparent_34%),linear-gradient(135deg,#f7fee7,#f0fdfa_45%,#ffffff)] p-6 shadow-sm">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <Badge color="emerald" className="w-fit">{ui.eyebrow}</Badge>
            <div>
              <Title className="text-3xl tracking-tight text-slate-950">{ui.title}</Title>
              <Text className="mt-2 max-w-3xl text-base text-slate-700">{ui.subtitle}</Text>
            </div>
            <div className="flex flex-wrap gap-2">
              {status ? <Badge color="emerald">Audit API ready</Badge> : null}
              {activeRoutes.length ? (
                activeRoutes.slice(0, 4).map((route) => (
                  <Badge key={route.model_key} color="blue">
                    {route.provider_key} / {route.model_name}
                  </Badge>
                ))
              ) : (
                <Badge color="rose">{ui.noRoutes}</Badge>
              )}
              {status?.model_center.route_count ? (
                <Badge color="slate">
                  {ui.modelRoutes}: {status.model_center.active_route_count}/{status.model_center.route_count}
                </Badge>
              ) : null}
            </div>
          </div>
          <div className="flex flex-col gap-3 rounded-3xl border border-white/70 bg-white/75 p-4 shadow-sm backdrop-blur md:min-w-[340px]">
            <label className="flex items-center gap-3 text-sm font-medium text-slate-800">
              <input
                type="checkbox"
                checked={includeNew}
                onChange={(event) => setIncludeNew(event.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-emerald-600"
              />
              {ui.includeNew}
            </label>
            <label className="space-y-1 text-sm font-medium text-slate-800">
              <span>{ui.maxCandidates}</span>
              <input
                type="number"
                min={1}
                max={100}
                value={maxCandidates}
                onChange={(event) => setMaxCandidates(Number(event.target.value) || 40)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-400"
              />
            </label>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Button variant="secondary" icon={FileSearch} onClick={() => run(false)} disabled={runAudit.isPending}>
                {ui.runLocal}
              </Button>
              <Button icon={BrainCircuit} onClick={() => run(true)} disabled={runAudit.isPending || !activeRoutes.length}>
                {runAudit.isPending ? ui.loading : ui.runAI}
              </Button>
            </div>
          </div>
        </div>
      </div>

      {statusError ? (
        <Card className="border-amber-200 bg-amber-50">
          <div className="flex gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 text-amber-600" />
            <div>
              <Title className="text-base text-amber-900">{ui.error}</Title>
              <Text className="mt-1 text-amber-800">
                {String(statusError.message || statusError).includes("Not Found")
                  ? ui.routeMissing
                  : String(statusError.message || statusError)}
              </Text>
            </div>
          </div>
        </Card>
      ) : null}

      {runAudit.error ? (
        <Card className="border-rose-200 bg-rose-50">
          <div className="flex gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 text-rose-600" />
            <div>
              <Title className="text-base text-rose-900">{ui.error}</Title>
              <Text className="mt-1 text-rose-800">
                {String(runAudit.error.message || runAudit.error).includes("Not Found")
                  ? ui.routeMissing
                  : String(runAudit.error.message || runAudit.error)}
              </Text>
            </div>
          </div>
        </Card>
      ) : null}

      {!result ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <ShieldCheck className="h-12 w-12 text-tremor-content-subtle" />
            <Title className="mt-3">{ui.noData}</Title>
          </div>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Card decoration="top" decorationColor="rose">
              <Text>{ui.highDuplicates}</Text>
              <p className="mt-2 text-3xl font-semibold">{result.summary.high_confidence_standard_duplicates}</p>
            </Card>
            <Card decoration="top" decorationColor="amber">
              <Text>{ui.mappingCandidates}</Text>
              <p className="mt-2 text-3xl font-semibold">{result.summary.mapping_term_review_candidates}</p>
            </Card>
            <Card decoration="top" decorationColor="blue">
              <Text>{ui.newCandidates}</Text>
              <p className="mt-2 text-3xl font-semibold">{result.summary.new_disease_candidates}</p>
            </Card>
            <Card decoration="top" decorationColor="slate">
              <Text>{ui.similarCandidates}</Text>
              <p className="mt-2 text-3xl font-semibold">{result.summary.similar_name_review_candidates}</p>
            </Card>
          </div>

          {result.ai_review ? (
            <Card className="space-y-5 border-emerald-200 bg-emerald-50/50">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <BrainCircuit className="h-5 w-5 text-emerald-700" />
                    <Title className="text-base">{ui.aiReview}</Title>
                  </div>
                  <Text className="mt-1">
                    {ui.modelUsed}: {result.ai_review.model_route?.provider_key || "-"} / {result.ai_review.model_route?.model_name || "-"}
                  </Text>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Badge color="emerald"><GitMerge className="mr-1 h-3 w-3" />{ui.merge}: {result.ai_review.summary?.merge ?? 0}</Badge>
                  <Badge color="blue"><PlusCircle className="mr-1 h-3 w-3" />{ui.add}: {result.ai_review.summary?.add_standard_disease ?? 0}</Badge>
                  <Badge color="slate">{ui.keep}: {result.ai_review.summary?.keep_separate ?? 0}</Badge>
                  <Badge color="amber">{ui.review}: {result.ai_review.summary?.needs_human_review ?? 0}</Badge>
                </div>
              </div>
              <RecommendationList items={result.ai_review.recommendations} lang={lang} />
            </Card>
          ) : null}

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <FindingList title={ui.highDuplicates} items={result.high_confidence_standard_duplicates} />
            <FindingList title={ui.newCandidates} items={result.new_disease_candidates} />
            <FindingList title={ui.mappingCandidates} items={result.mapping_term_review_candidates} />
            <FindingList title={ui.similarCandidates} items={result.similar_name_review_candidates} />
          </div>

          <Text className="text-xs text-tremor-content-subtle">{ui.generatedAt}: {result.generated_at}</Text>
        </>
      )}
    </div>
  );
}
