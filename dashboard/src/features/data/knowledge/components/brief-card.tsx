import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/tremor";
import type { DiseaseKnowledgeDetailBrief } from "@/features/ai/api";
import { briefStatusColor, formatDateTime } from "./shared";
import { BriefDisclosureField, BriefField, CitationText, citationReferencesFromAttribution } from "./citations";

export function BriefCard({
  brief,
  lang,
}: {
  brief: DiseaseKnowledgeDetailBrief;
  lang: "en" | "zh";
}) {
  const attribution = Array.isArray(brief.source_attribution) ? brief.source_attribution : [];
  const citationReferences = citationReferencesFromAttribution(attribution);
  const quality = brief.quality;
  const fieldAvailable = (field: string, value: string | null | undefined) =>
    quality?.fields?.[field]?.available ?? Boolean(value?.trim());

  const definition = brief.definition ?? brief.brief;
  const clinical = brief.clinical_features ?? brief.clinical_summary;
  const definitionText = definition?.trim();
  const briefText = brief.brief.trim();
  const primaryFields = [
    {
      label: lang === "zh" ? "定义" : "Definition",
      value: fieldAvailable("definition", definitionText) && definitionText !== briefText ? definitionText : null,
    },
    {
      label: lang === "zh" ? "临床特征" : "Clinical features",
      value: fieldAvailable("clinical_features", clinical) ? clinical : null,
    },
  ].filter((item) => item.value);
  const expandableFields = [
    { label: lang === "zh" ? "流行病学" : "Epidemiology", value: fieldAvailable("epidemiology", brief.epidemiology) ? brief.epidemiology : null },
    { label: lang === "zh" ? "传播途径" : "Transmission", value: fieldAvailable("transmission", brief.transmission) ? brief.transmission : null },
    { label: lang === "zh" ? "预防" : "Prevention", value: fieldAvailable("prevention", brief.prevention) ? brief.prevention : null },
    { label: lang === "zh" ? "监测备注" : "Surveillance note", value: fieldAvailable("surveillance_note", brief.surveillance_note) ? brief.surveillance_note : null },
    { label: lang === "zh" ? "重点人群" : "Risk groups", value: fieldAvailable("risk_groups", brief.risk_groups) ? brief.risk_groups : null },
    { label: lang === "zh" ? "免责声明" : "Disclaimer", value: brief.disclaimer },
  ].filter((item) => item.value);

  return (
    <section className="rounded-tremor-default border border-tremor-border bg-tremor-background/90 px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background/70">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {brief.language.toUpperCase()}
            </p>
            <Badge color={briefStatusColor(brief.status)}>{brief.status}</Badge>
            <Badge color={brief.source_confidence === "high" ? "emerald" : brief.source_confidence === "medium" ? "amber" : "slate"}>
              {brief.source_confidence}
            </Badge>
            {quality ? (
              <Badge color={quality.display_mode === "full" ? "emerald" : quality.display_mode === "partial" ? "amber" : "slate"}>
                {quality.display_mode} · {Math.round(quality.completeness * 100)}%
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {brief.model ? `${brief.model} · ` : ""}
            {formatDateTime(brief.updated_at)}
          </p>
        </div>
        {typeof brief.quality_score === "number" ? (
          <Badge color={brief.quality_score >= 0.8 ? "emerald" : brief.quality_score >= 0.6 ? "amber" : "slate"}>
            Q {brief.quality_score.toFixed(2)}
          </Badge>
        ) : null}
      </div>

      <div className="mt-3 space-y-3">
        {fieldAvailable("brief", brief.brief) ? (
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "Brief" : "Brief"}
          </p>
          <p className="mt-2 whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
            <CitationText text={brief.brief} references={citationReferences} />
          </p>
        </div>
        ) : (
          <div className="rounded-tremor-default border border-amber-300 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
            <p className="font-semibold">
              {lang === "zh" ? "摘要未通过公开质量门禁" : "Brief did not pass the public quality gate"}
            </p>
            <p className="mt-1 text-xs leading-5">
              {lang === "zh" ? "缺失说明不会再作为疾病知识展示；可在下方展开检查原始候选文本。" : "Absence explanations are no longer displayed as disease knowledge. Expand below to inspect the raw candidate text."}
            </p>
            <details className="mt-2">
              <summary className="cursor-pointer text-xs font-semibold uppercase">
                {lang === "zh" ? "查看原始候选" : "Inspect raw candidate"}
              </summary>
              <p className="mt-2 whitespace-pre-line text-xs leading-5">{brief.brief}</p>
            </details>
          </div>
        )}

        {quality?.issues?.length > 0 ? (
          <div className="rounded-tremor-default border border-dashed border-amber-300 px-3 py-2 dark:border-amber-800">
            <p className="text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-300">
              {lang === "zh" ? "质量门禁结果" : "Quality gate findings"}
            </p>
            <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-tremor-content dark:text-dark-tremor-content">
              {quality.issues.map((issue) => <li key={issue}>{issue}</li>)}
            </ul>
          </div>
        ) : null}

        {primaryFields.length > 0 ? (
          <div className="space-y-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            {primaryFields.map((item) => (
              <BriefField key={item.label} label={item.label} value={item.value} references={citationReferences} compact />
            ))}
          </div>
        ) : null}

        {expandableFields.length > 0 ? (
          <div className="space-y-2">
            {expandableFields.map((item) => (
              <BriefDisclosureField key={item.label} label={item.label} value={item.value} references={citationReferences} />
            ))}
          </div>
        ) : null}

        {(brief.source_ids && brief.source_ids.length > 0) || citationReferences.length > 0 ? (
          <details className="rounded-tremor-default border border-dashed border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
            <summary className="cursor-pointer list-none text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "来源标注" : "Source attribution"}
            </summary>
            {citationReferences.length > 0 ? (
              <div className="mt-2 space-y-2">
                {citationReferences.map((entry) => (
                  <div key={entry.key} className="flex items-start gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-2.5 py-2 text-xs text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content">
                    <span className="mt-0.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-[3px] border border-tremor-border px-1 font-mono text-[10px] font-semibold text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">
                      {entry.citationIndex}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium leading-5 text-tremor-content-strong dark:text-dark-tremor-content-strong" title={entry.tooltip}>
                        {entry.citationText}
                      </p>
                      {entry.url ? (
                        <p className="mt-1 break-all text-[11px] leading-5 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {entry.linkLabel}:{" "}
                          <a
                            href={entry.url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-blue-700 underline-offset-2 hover:underline dark:text-blue-300"
                          >
                            {entry.url}
                            <ExternalLink className="h-3 w-3 shrink-0" />
                          </a>
                        </p>
                      ) : null}
                      {entry.sourceId !== null ? (
                        <p className="mt-1 font-mono text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          source {entry.sourceId}
                        </p>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : brief.source_ids && brief.source_ids.length > 0 ? (
              <p className="mt-2 text-xs text-tremor-content dark:text-dark-tremor-content">
                {lang === "zh" ? "来源 ID" : "Source IDs"}: {brief.source_ids.join(", ")}
              </p>
            ) : null}
          </details>
        ) : null}
      </div>
    </section>
  );
}
