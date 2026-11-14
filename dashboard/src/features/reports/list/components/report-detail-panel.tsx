import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { ChevronDown, FileText } from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";
import { type ReportDetail } from "@/features/reports/api";
import { ReportFigureList } from "@/features/reports/report-figures";

import { ReportV4Preview } from "@/features/reports/v4-preview";
import { t } from "@/lib/i18n";

import { statusLabel, percent, periodDate, extractSectionDisease, asRecord, asArray } from "../presentation";

import { AnalyticalV3Summary } from "./analytical-summary";
import { AnalyticalV3ReportBody } from "./analytical-body";
export function ReportDetailPanel({
  detail,
  lang,
  loading,
}: {
  detail?: ReportDetail;
  lang: "en" | "zh";
  loading: boolean;
}) {
  const sectionsByDisease = useMemo(() => {
    if (!detail) return [];

    const grouped = new Map<string, ReportDetail["sections"]>();
    detail.sections.forEach((section) => {
      const diseaseName = extractSectionDisease(section.title, t(lang, "report_disease_unknown"));
      const current = grouped.get(diseaseName) ?? [];
      current.push(section);
      grouped.set(diseaseName, current);
    });

    return Array.from(grouped.entries())
      .map(([disease, sections]) => ({
        disease,
        sections: [...sections].sort((a, b) => a.section_order - b.section_order),
      }))
      .sort((a, b) => {
        const aOrder = a.sections[0]?.section_order ?? Number.MAX_SAFE_INTEGER;
        const bOrder = b.sections[0]?.section_order ?? Number.MAX_SAFE_INTEGER;
        if (aOrder !== bOrder) return aOrder - bOrder;
        return a.disease.localeCompare(b.disease);
      });
  }, [detail, lang]);

  if (loading && !detail) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((item) => (
          <div
            key={item}
            className="h-24 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted"
          />
        ))}
      </div>
    );
  }

  if (!detail) {
    return (
      <EmptyState
        icon={<FileText className="h-10 w-10" />}
        title={t(lang, "report_select_hint")}
        className="py-12"
      />
    );
  }

  const metadata = asRecord(detail.metadata);
  const figureData = asRecord(metadata.figure_data);
  const isAnalyticalV3 = metadata.report_layout === "analytical_v3";
  if (metadata.report_layout === "report_v4" && metadata.report_document_v4) {
    return <ReportV4Preview detail={detail} lang={lang} />;
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {t(lang, "report_meta_quality")}
          </p>
          <p className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {percent(detail.quality_score)}
          </p>
        </div>
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {t(lang, "report_meta_generation_time")}
          </p>
          <p className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {detail.generation_time != null ? `${detail.generation_time.toFixed(1)}s` : "--"}
          </p>
        </div>
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {t(lang, "sections")}
          </p>
          <p className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {detail.sections.length}
          </p>
        </div>
      </div>

      <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={detail.status}>{statusLabel(lang, detail.status)}</StatusBadge>
          <StatusBadge>{detail.report_type}</StatusBadge>
          {detail.ai_model_used ? <StatusBadge tone="info">{detail.ai_model_used}</StatusBadge> : null}
        </div>
        <p className="mt-3 text-sm text-tremor-content dark:text-dark-tremor-content">
          {periodDate(detail.period_start)} - {periodDate(detail.period_end)}
        </p>
      </div>

      {detail.summary ? (
        <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 text-sm leading-6 text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
          {detail.summary}
        </section>
      ) : null}

      {(detail.key_findings?.length ?? 0) > 0 ? (
        <section className="rounded-tremor-default border border-emerald-200 bg-emerald-50/70 p-4 dark:border-emerald-900/30 dark:bg-emerald-950/20">
          <h3 className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">
            {t(lang, "report_key_findings")}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-emerald-900 dark:text-emerald-100">
            {detail.key_findings.map((finding, index) => (
              <li key={`${finding}-${index}`}>{finding}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {(detail.recommendations?.length ?? 0) > 0 ? (
        <section className="rounded-tremor-default border border-sky-200 bg-sky-50/70 p-4 dark:border-sky-900/30 dark:bg-sky-950/20">
          <h3 className="text-sm font-semibold text-sky-800 dark:text-sky-200">
            {t(lang, "report_recommendations")}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-sky-900 dark:text-sky-100">
            {detail.recommendations.map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {detail.error_message ? (
        <section className="rounded-tremor-default border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-900/30 dark:bg-rose-950/30 dark:text-rose-300">
          {detail.error_message}
        </section>
      ) : null}

      <AnalyticalV3Summary detail={detail} lang={lang} />
      {isAnalyticalV3 ? <AnalyticalV3ReportBody detail={detail} lang={lang} /> : null}

      {!isAnalyticalV3 ? (
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {t(lang, "report_disease_summary")}
          </h3>
          <StatusBadge>{detail.sections.length}</StatusBadge>
        </div>

        {detail.sections.length > 0 ? (
          sectionsByDisease.map((group) => (
            <div
              key={group.disease}
              className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle/60 p-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle/50"
            >
              <div className="mb-3 flex items-center justify-between gap-2 px-1">
                <p className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {group.disease}
                </p>
                <StatusBadge>{group.sections.length}</StatusBadge>
              </div>

              <div className="space-y-3">
                {group.sections.map((section) => (
                  <details
                    key={section.id}
                    className="group overflow-hidden rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background"
                  >
                    <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-tremor-content-strong transition-colors hover:bg-tremor-background-subtle dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle [&::-webkit-details-marker]:hidden">
                      <ChevronDown className="h-4 w-4 shrink-0 text-tremor-content-subtle transition-transform group-open:rotate-180 dark:text-dark-tremor-content-subtle" />
                      <span className="truncate">
                        {section.section_order}. {section.title || section.section_type || "Section"}
                      </span>
                    </summary>

                    <div className="border-t border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                      <ReportFigureList
                        figures={asArray(section.charts).map(asRecord)}
                        figureData={figureData}
                        lang={lang}
                        placement="before_content"
                      />
                      <div className="prose max-w-none dark:prose-invert">
                        <ReactMarkdown>{section.content ?? ""}</ReactMarkdown>
                      </div>
                      <ReportFigureList
                        figures={asArray(section.charts).map(asRecord)}
                        figureData={figureData}
                        lang={lang}
                        placement="after_content"
                      />
                    </div>

                    {section.ai_model || section.generation_time != null ? (
                      <div className="border-t border-tremor-border bg-tremor-background-subtle px-4 py-2 text-xs text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                        {section.ai_model ? `Model: ${section.ai_model}` : "Model: --"}
                        {section.generation_time != null ? ` · ${section.generation_time.toFixed(1)}s` : ""}
                      </div>
                    ) : null}
                  </details>
                ))}
              </div>
            </div>
          ))
        ) : (
          <EmptyState title={t(lang, "report_no_sections")} className="rounded-tremor-default border border-dashed border-tremor-border py-10 dark:border-dark-tremor-border" />
        )}
      </section>
      ) : null}
    </div>
  );
}
