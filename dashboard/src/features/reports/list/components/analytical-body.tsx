import { useMemo } from "react";
import ReactMarkdown from "react-markdown";

import { EmptyState } from "@/components/ui/EmptyState";

import { StatusBadge } from "@/components/ui/StatusBadge";
import { type ReportDetail } from "@/features/reports/api";
import { ReportFigureList } from "@/features/reports/report-figures";

import { t } from "@/lib/i18n";

import { asRecord, asArray } from "../presentation";
import { collectReportReferences, linkCitationMarkers } from "../citations";
import { assignFigureNumbers, isAuditReportSection, sectionDisplayType } from "../figure-selection";
import { CitationAnchor } from "./citation-anchor";
import { ReportReferences } from "./report-references";
export function AnalyticalV3ReportBody({ detail, lang }: { detail: ReportDetail; lang: "en" | "zh" }) {
  const metadata = asRecord(detail.metadata);
  const figureData = asRecord(metadata.figure_data);
  const allFigures = asArray(metadata.figures).map(asRecord);
  const sections = useMemo(
    () => [...detail.sections]
      .sort((a, b) => a.section_order - b.section_order)
      .filter((section) => !isAuditReportSection(section)),
    [detail.sections],
  );
  const references = useMemo(
    () => collectReportReferences(detail, sections.map((section) => section.content ?? "")),
    [detail, sections],
  );
  const preparedSections = useMemo(() => assignFigureNumbers(sections, allFigures), [sections, allFigures]);

  if (sections.length === 0) {
    return (
      <EmptyState
        title={t(lang, "report_no_sections")}
        className="rounded-tremor-default border border-dashed border-tremor-border py-10 dark:border-dark-tremor-border"
      />
    );
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {lang === "zh" ? "分析报告正文" : "Analytical Report Body"}
        </h3>
        <StatusBadge>{sections.length}</StatusBadge>
      </div>

      <div className="space-y-3">
        {preparedSections.map(({ section, beforeFigures, afterFigures }) => {
          const chartCount = beforeFigures.length + afterFigures.length;
          const body = (
            <>
              <ReportFigureList figures={beforeFigures} figureData={figureData} lang={lang} />
              <div className="report-markdown">
                <ReactMarkdown components={{ a: CitationAnchor }}>
                  {linkCitationMarkers(section.content ?? "", references)}
                </ReactMarkdown>
              </div>
              <ReportFigureList figures={afterFigures} figureData={figureData} lang={lang} />
            </>
          );

          return (
            <article
              key={section.id}
              className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background"
            >
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {sectionDisplayType(section)}
                  </p>
                  <h3 className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {section.section_order}. {section.title || section.section_type || "Section"}
                  </h3>
                </div>
                <StatusBadge>{chartCount ? `${chartCount} fig` : lang === "zh" ? "文字" : "Text"}</StatusBadge>
              </div>
              {body}
            </article>
          );
        })}
      </div>
      <ReportReferences references={references} lang={lang} />
    </section>
  );
}
