"use client";

import { StatusBadge } from "@/components/ui/StatusBadge";
import type { DiseaseAuditRecommendation } from "@/features/ai/api";
import {
  type DiseaseAuditLang,
  decisionLabel,
  decisionTone,
  diseaseAuditCopy,
} from "../helpers";

interface RecommendationListProps {
  items?: DiseaseAuditRecommendation[];
  lang: DiseaseAuditLang;
}

export function RecommendationList({ items, lang }: RecommendationListProps) {
  if (!items?.length) {
    return (
      <p className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {diseaseAuditCopy[lang].noFindings}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {items.map((item, index) => (
        <div key={`${item.decision}-${index}`} className="app-panel p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge tone={decisionTone(item.decision)}>
              {decisionLabel(item.decision, lang)}
            </StatusBadge>
            {item.confidence ? <StatusBadge>{item.confidence}</StatusBadge> : null}
            {item.canonical_id ? (
              <StatusBadge tone="info">canonical {item.canonical_id}</StatusBadge>
            ) : null}
            {item.merge_ids?.map((id) => (
              <StatusBadge key={id} tone="success">
                merge {id}
              </StatusBadge>
            ))}
          </div>
          <p className="mt-3 text-sm font-medium leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {item.finding}
          </p>
          {item.proposed_standard_name_en || item.proposed_standard_name_zh ? (
            <p className="mt-2 text-sm text-tremor-content dark:text-dark-tremor-content">
              Proposed: {item.proposed_standard_name_en || "-"} /{" "}
              {item.proposed_standard_name_zh || "-"}
            </p>
          ) : null}
          <p className="mt-2 text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
            {lang === "zh"
              ? item.rationale_zh || item.rationale_en
              : item.rationale_en || item.rationale_zh}
          </p>
        </div>
      ))}
    </div>
  );
}
