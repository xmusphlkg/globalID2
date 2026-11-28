"use client";

import {
  AlertTriangle,
  BrainCircuit,
  FileSearch,
  GitMerge,
  PlusCircle,
  ShieldCheck,
} from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { MetricTile } from "@/components/ui/MetricTile";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { DiseaseDuplicateAuditResult } from "@/features/ai/api";
import type { DiseaseAuditCopy, DiseaseAuditLang } from "../helpers";
import { FindingList } from "./FindingList";
import { RecommendationList } from "./RecommendationList";

interface AuditResultsSectionProps {
  result: DiseaseDuplicateAuditResult | undefined;
  lang: DiseaseAuditLang;
  ui: DiseaseAuditCopy;
}

export function AuditResultsSection({ result, lang, ui }: AuditResultsSectionProps) {
  if (!result) {
    return (
      <EmptyState
        icon={<ShieldCheck className="h-12 w-12" />}
        title={ui.noData}
        className="rounded-tremor-default border border-dashed border-tremor-border bg-tremor-background py-16 dark:border-dark-tremor-border dark:bg-dark-tremor-background"
      />
    );
  }

  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={ui.highDuplicates}
          value={result.summary.high_confidence_standard_duplicates}
          tone="danger"
          icon={<AlertTriangle className="h-4 w-4" />}
        />
        <MetricTile
          label={ui.mappingCandidates}
          value={result.summary.mapping_term_review_candidates}
          tone="warning"
          icon={<FileSearch className="h-4 w-4" />}
        />
        <MetricTile
          label={ui.newCandidates}
          value={result.summary.new_disease_candidates}
          tone="info"
          icon={<PlusCircle className="h-4 w-4" />}
        />
        <MetricTile
          label={ui.similarCandidates}
          value={result.summary.similar_name_review_candidates}
          tone="neutral"
          icon={<ShieldCheck className="h-4 w-4" />}
        />
      </div>

      {result.ai_review ? (
        <section className="space-y-5 rounded-tremor-default border border-emerald-200 bg-emerald-50/50 p-4 dark:border-emerald-900/40 dark:bg-emerald-950/20">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <BrainCircuit className="h-5 w-5 text-emerald-700 dark:text-emerald-300" />
                <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {ui.aiReview}
                </h2>
              </div>
              <p className="mt-1 text-sm text-tremor-content dark:text-dark-tremor-content">
                {ui.modelUsed}: {result.ai_review.model_route?.provider_key || "-"} /{" "}
                {result.ai_review.model_route?.model_name || "-"}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusBadge tone="success">
                <GitMerge className="mr-1 h-3 w-3" />
                {ui.merge}: {result.ai_review.summary?.merge ?? 0}
              </StatusBadge>
              <StatusBadge tone="info">
                <PlusCircle className="mr-1 h-3 w-3" />
                {ui.add}: {result.ai_review.summary?.add_standard_disease ?? 0}
              </StatusBadge>
              <StatusBadge>
                {ui.keep}: {result.ai_review.summary?.keep_separate ?? 0}
              </StatusBadge>
              <StatusBadge tone="warning">
                {ui.review}: {result.ai_review.summary?.needs_human_review ?? 0}
              </StatusBadge>
            </div>
          </div>
          <RecommendationList items={result.ai_review.recommendations} lang={lang} />
        </section>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <FindingList
          title={ui.highDuplicates}
          items={result.high_confidence_standard_duplicates}
          empty={ui.noFindings}
        />
        <FindingList
          title={ui.newCandidates}
          items={result.new_disease_candidates}
          empty={ui.noFindings}
        />
        <FindingList
          title={ui.mappingCandidates}
          items={result.mapping_term_review_candidates}
          empty={ui.noFindings}
        />
        <FindingList
          title={ui.similarCandidates}
          items={result.similar_name_review_candidates}
          empty={ui.noFindings}
        />
      </div>

      <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {ui.generatedAt}: {result.generated_at}
      </p>
    </>
  );
}
