"use client";

import { type ComponentPropsWithoutRef, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ChevronDown, FileText, Gauge, Layers3, Search, Send, TimerReset } from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { type ReportDetail, type ReportListItem, type ReportSection, useReportDetail, useReports } from "@/features/reports/api";
import { ReportFigureList } from "@/features/reports/report-figures";
import { t } from "@/lib/i18n";
import { formatDate } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const statusLabelKey = {
  pending: "report_status_pending",
  generating: "report_status_generating",
  completed: "report_status_completed",
  failed: "report_status_failed",
  reviewing: "report_status_reviewing",
  approved: "report_status_approved",
  published: "report_status_published",
} as const;

type FlowGroupId = "queue" | "review" | "published";

type FlowGroup = {
  id: FlowGroupId;
  titleKey: "flow_queue" | "flow_review" | "flow_published";
  statuses: string[];
};

const FLOW_GROUPS: FlowGroup[] = [
  { id: "queue", titleKey: "flow_queue", statuses: ["pending", "generating", "failed"] },
  { id: "review", titleKey: "flow_review", statuses: ["reviewing"] },
  { id: "published", titleKey: "flow_published", statuses: ["completed", "approved", "published"] },
];

function statusLabel(lang: "en" | "zh", status: string) {
  const key = statusLabelKey[status as keyof typeof statusLabelKey];
  return key ? t(lang, key) : status;
}

function percent(score: number | null | undefined) {
  if (score == null) return "--";
  return `${Math.round(score * 100)}%`;
}

function extractSectionDisease(sectionTitle: string | null | undefined, fallback: string) {
  const title = (sectionTitle || "").trim();
  if (!title) return fallback;

  const separators = [" - ", " – ", " — ", ":", "："];
  for (const separator of separators) {
    const index = title.indexOf(separator);
    if (index > 0) {
      const candidate = title.slice(0, index).trim();
      if (candidate) return candidate;
    }
  }

  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNullableString(value: unknown): string | null {
  const text = typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
  return text || null;
}

function formatRiskName(item: Record<string, unknown>, lang: "en" | "zh") {
  return asString(lang === "zh" ? item.name_zh : item.name_en) || asString(item.name_en) || asString(item.disease_id) || "-";
}

function sourceIdentity(source: Record<string, unknown>, index: number) {
  const id = asNullableString(source.source_id) ?? asNullableString(source.id);
  if (id) return `id:${id}`;
  const url = asNullableString(source.resolved_url) ?? asNullableString(source.url);
  if (url) return `url:${url}`;
  const title = asNullableString(source.title) ?? asNullableString(source.source_name);
  if (title) return `title:${title.toLowerCase()}`;
  return `index:${index}`;
}

function referenceIndex(source: Record<string, unknown>, fallbackIndex: number) {
  const value = Number(source.citation_index);
  return Number.isFinite(value) && value > 0 ? value : fallbackIndex;
}

function referenceUrl(source: Record<string, unknown>) {
  return asNullableString(source.resolved_url) ?? asNullableString(source.url);
}

function referenceLinkLabel(source: Record<string, unknown>) {
  const lookup = [
    source.source_type,
    source.source_name,
    source.url,
    source.resolved_url,
  ].map((value) => asString(value).toLowerCase()).join(" ");
  if (lookup.includes("pubmed")) return "PubMed";
  if (lookup.includes("doi.org") || asNullableString(source.doi)) return "DOI";
  return "Available from";
}

function formatReferenceCitation(source: Record<string, unknown>, fallbackIndex: number) {
  const title = asNullableString(source.title) ?? `Reference ${fallbackIndex}`;
  const sourceName = asNullableString(source.source_name) ?? asNullableString(source.source_type);
  const date = asNullableString(source.publication_date) ?? asNullableString(source.published_at) ?? asNullableString(source.fetched_at);
  const pmid = referenceUrl(source)?.match(/pubmed\.ncbi\.nlm\.nih\.gov\/(\d+)/i)?.[1];
  return [
    sourceName && sourceName.toLowerCase() !== title.toLowerCase() ? `${sourceName}.` : null,
    /[.!?。！？]$/.test(title) ? title : `${title}.`,
    date ? `${date}.` : null,
    pmid ? `PMID: ${pmid}.` : null,
  ].filter(Boolean).join(" ");
}

function collectReportReferences(detail: ReportDetail, sectionTexts: string[]) {
  const metadata = asRecord(detail.metadata);
  const rawSources: Record<string, unknown>[] = [];

  asArray(metadata.references).map(asRecord).forEach((source) => rawSources.push(source));
  asArray(metadata.disease_cards).map(asRecord).forEach((card) => {
    const context = asRecord(card.knowledge_context);
    asArray(context.source_attribution).map(asRecord).forEach((source) => rawSources.push(source));
  });

  const markers = new Set<number>();
  sectionTexts.forEach((text) => {
    for (const match of text.matchAll(/\[(\d+)\]/g)) markers.add(Number(match[1]));
  });

  const seen = new Set<string>();
  const deduped = rawSources.filter((source, index) => {
    if (Object.keys(source).length === 0) return false;
    const key = sourceIdentity(source, index);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const normalized = deduped.map((source, index) => ({
    ...source,
    citation_index: referenceIndex(source, index + 1),
  }));
  normalized.sort((a, b) => referenceIndex(a, Number.MAX_SAFE_INTEGER) - referenceIndex(b, Number.MAX_SAFE_INTEGER));
  return markers.size > 0
    ? normalized.filter((source, index) => markers.has(referenceIndex(source, index + 1)) || asArray(metadata.references).length > 0)
    : normalized;
}

function linkCitationMarkers(content: string, references: Record<string, unknown>[]) {
  if (!content || references.length === 0) return content;
  const valid = new Set(references.map((source, index) => referenceIndex(source, index + 1)));
  return content.replace(/\[(\d+)\](?!\()/g, (match, value: string) => {
    const index = Number(value);
    return valid.has(index) ? `[[${index}]](#report-ref-${index})` : match;
  });
}

type MarkdownAnchorProps = ComponentPropsWithoutRef<"a"> & { node?: unknown };

function CitationAnchor({ node: _node, href, children, ...props }: MarkdownAnchorProps) {
  if (href?.startsWith("#report-ref-")) {
    return (
      <sup className="citation-ref">
        <a href={href} className="citation-link" {...props}>
          {children}
        </a>
      </sup>
    );
  }

  return (
    <a href={href} {...props}>
      {children}
    </a>
  );
}

function assignFigureNumbers(sections: ReportSection[], allFigures: Record<string, unknown>[]) {
  let nextFigureNumber = 1;
  return sections.map((section) => {
    const charts = sectionFigures(section, allFigures);
    const numberFigures = (placement: "before_content" | "after_content") =>
      charts
        .filter((figure) => (asString(figure.position) || "after_content") === placement)
        .map((figure) => ({
          ...figure,
          display_number: nextFigureNumber++,
        }));

    return {
      section,
      beforeFigures: numberFigures("before_content"),
      afterFigures: numberFigures("after_content"),
    };
  });
}

function isAuditReportSection(section: ReportSection) {
  return section.section_type === "methodology" || section.section_type === "evidence_appendix";
}

function ReportReferences({ references, lang }: { references: Record<string, unknown>[]; lang: "en" | "zh" }) {
  if (references.length === 0) return null;

  return (
    <section className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {lang === "zh" ? "参考文献" : "References"}
      </p>
      <ol className="mt-3 space-y-3">
        {references.map((source, index) => {
          const citationIndex = referenceIndex(source, index + 1);
          const url = referenceUrl(source);
          return (
            <li key={`${citationIndex}-${url || asString(source.title) || index}`} id={`report-ref-${citationIndex}`}>
              <div className="flex items-start gap-3 text-sm leading-6 text-tremor-content dark:text-dark-tremor-content">
                <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-tremor-small border border-tremor-border bg-tremor-background-subtle font-mono text-[10px] font-semibold text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                  {citationIndex}
                </span>
                <span className="min-w-0">
                  <span className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                    {formatReferenceCitation(source, citationIndex)}
                  </span>
                  {url ? (
                    <span className="mt-1 block break-all text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {referenceLinkLabel(source)}:{" "}
                      <a href={url} target="_blank" rel="noreferrer" className="text-tremor-brand underline-offset-2 hover:underline dark:text-dark-tremor-brand">
                        {url}
                      </a>
                    </span>
                  ) : null}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function sectionFigures(section: ReportSection, allFigures: Record<string, unknown>[]) {
  const directCharts = asArray(section.charts).map(asRecord);
  if (directCharts.length > 0) return curateSectionFigures(section, directCharts);

  const metadata = asRecord(section.metadata);
  const metadataFigures = asArray(metadata.figure_specs).map(asRecord);
  if (metadataFigures.length > 0) return curateSectionFigures(section, metadataFigures);

  const sectionType = asString(section.section_type);
  if (!sectionType) return [];
  return curateSectionFigures(section, allFigures.filter((figure) => asString(figure.section_type) === sectionType));
}

function sectionDisplayType(section: ReportSection) {
  const metadata = asRecord(section.metadata);
  return asString(metadata.display_type) || asString(section.section_type) || "section";
}

function firstFigureOfType(figures: Record<string, unknown>[], types: string[]) {
  for (const type of types) {
    const found = figures.find((figure) => asString(figure.figure_type) === type);
    if (found) return found;
  }
  return null;
}

function sectionNeedsDataQualityFigure(section: ReportSection) {
  const metadata = asRecord(section.metadata);
  const quality = asRecord(metadata.data_quality);
  const score = asNumber(quality.score);
  if (score !== null && score < 0.9) return true;
  const missing = asRecord(quality.missing_rates);
  return Object.values(missing).some((value) => Number(value) >= 0.2);
}

function curateSectionFigures(section: ReportSection, figures: Record<string, unknown>[]) {
  const renderable = figures.filter((figure) => asString(figure.renderer) === "echarts" || !asString(figure.renderer));
  const sectionType = asString(section.section_type) || asString(renderable[0]?.section_type);

  if (sectionType === "trend_anomaly_analysis") {
    const primary = firstFigureOfType(renderable, ["anomaly_marker_curve", "seasonal_baseline_band", "epidemic_curve"]);
    const secondary = firstFigureOfType(
      renderable.filter((figure) => figure !== primary),
      ["epidemic_curve", "recent_window_heatmap"],
    );
    return [primary, secondary].filter((figure): figure is Record<string, unknown> => Boolean(figure));
  }

  if (sectionType === "priority_signals") {
    return [firstFigureOfType(renderable, ["risk_matrix", "risk_ranking_bar", "signal_context_panel"])].filter(
      (figure): figure is Record<string, unknown> => Boolean(figure),
    );
  }

  if (sectionType === "disease_profiles") {
    return [firstFigureOfType(renderable, ["cases_incidence_panel", "epidemic_curve"])].filter(
      (figure): figure is Record<string, unknown> => Boolean(figure),
    );
  }

  if (sectionType === "data_quality_limitations" && !sectionNeedsDataQualityFigure(section)) {
    return [];
  }

  return renderable.slice(0, 1);
}

function QualityCell({ score }: { score: number | null | undefined }) {
  if (score == null) {
    return <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">--</span>;
  }

  const value = Math.round(score * 100);
  const tone = value >= 80 ? "bg-emerald-500" : value >= 50 ? "bg-amber-500" : "bg-rose-500";

  return (
    <div className="flex min-w-[132px] items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-tremor-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
        <div className={`h-full rounded-tremor-full ${tone}`} style={{ width: `${value}%` }} />
      </div>
      <span className="w-10 text-right text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {value}%
      </span>
    </div>
  );
}

function ReportTitleCell({ report }: { report: ReportListItem }) {
  return (
    <div className="min-w-[260px] max-w-[520px]">
      <p className="line-clamp-2 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {report.title}
      </p>
      <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {report.primary_disease || report.disease_names?.join(", ") || report.country_name || "-"}
      </p>
    </div>
  );
}

function AnalyticalV3Summary({ detail, lang }: { detail: ReportDetail; lang: "en" | "zh" }) {
  const metadata = asRecord(detail.metadata);
  if (metadata.report_layout !== "analytical_v3") return null;

  const dataQuality = asRecord(detail.data_quality ?? metadata.data_quality);
  const summaryMetrics = asRecord(metadata.summary_metrics);
  const riskRanking = asArray(metadata.risk_ranking).map(asRecord).slice(0, 8);
  const diseaseCards = asArray(metadata.disease_cards).map(asRecord).slice(0, 6);
  const dataQualityScore = asNumber(dataQuality.score);
  const leadSignal = riskRanking[0];

  return (
    <section className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "主要信号" : "Lead Signal"}
          </p>
          <p className="mt-1 truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {leadSignal ? formatRiskName(leadSignal, lang) : "-"}
          </p>
        </div>
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "数据置信度" : "Data Confidence"}
          </p>
          <p className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {percent(dataQualityScore)}
          </p>
        </div>
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "研判信号" : "Signals Reviewed"}
          </p>
          <p className="mt-1 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {String(summaryMetrics.disease_count ?? diseaseCards.length ?? "-")}
          </p>
        </div>
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh" ? "高优先级" : "High Priority"}
          </p>
          <p className="mt-1 truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {String(summaryMetrics.high_risk_diseases ?? "0")}
          </p>
        </div>
      </div>

      <div className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <StatusBadge tone="info">
            {lang === "zh" ? "病例" : "Cases"} {String(summaryMetrics.total_cases ?? "-")}
          </StatusBadge>
          <StatusBadge tone="neutral">
            {lang === "zh" ? "死亡" : "Deaths"} {String(summaryMetrics.total_deaths ?? "-")}
          </StatusBadge>
          <StatusBadge tone="warning">
            {lang === "zh" ? "高风险" : "High Risk"} {String(summaryMetrics.high_risk_diseases ?? "0")}
          </StatusBadge>
        </div>
        <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {lang === "zh" ? "风险排序" : "Risk Ranking"}
        </h3>
        <div className="mt-2 overflow-hidden rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
          <table className="min-w-full divide-y divide-tremor-border text-sm dark:divide-dark-tremor-border">
            <thead className="bg-tremor-background-subtle dark:bg-dark-tremor-background-subtle">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-tremor-content-subtle">#</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-tremor-content-subtle">{lang === "zh" ? "疾病" : "Disease"}</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-tremor-content-subtle">{lang === "zh" ? "风险" : "Risk"}</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-tremor-content-subtle">{lang === "zh" ? "最近病例" : "Latest"}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
              {riskRanking.map((item, index) => (
                <tr key={`${asString(item.disease_id)}-${index}`}>
                  <td className="px-3 py-2 text-tremor-content dark:text-dark-tremor-content">{index + 1}</td>
                  <td className="px-3 py-2 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{formatRiskName(item, lang)}</td>
                  <td className="px-3 py-2">
                    <StatusBadge tone={asString(item.risk_level) === "high" || asString(item.risk_level) === "critical" ? "danger" : "neutral"}>
                      {asString(item.risk_level) || "-"}
                    </StatusBadge>
                  </td>
                  <td className="px-3 py-2 text-right text-tremor-content dark:text-dark-tremor-content">{String(item.latest_cases ?? "-")}</td>
                </tr>
              ))}
              {riskRanking.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-sm text-tremor-content-subtle">
                    {lang === "zh" ? "暂无风险排序" : "No risk ranking available"}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {diseaseCards.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {diseaseCards.map((card, index) => {
            const metrics = asRecord(card.metrics);
            const risk = asRecord(card.risk);
            return (
              <div key={`${asString(card.disease_id)}-${index}`} className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{formatRiskName(card, lang)}</p>
                  <StatusBadge tone={asString(risk.level) === "high" || asString(risk.level) === "critical" ? "danger" : "neutral"}>{asString(risk.level) || "-"}</StatusBadge>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-tremor-content dark:text-dark-tremor-content">
                  <span>{lang === "zh" ? "最近病例" : "Latest cases"}: {String(metrics.latest_cases ?? "-")}</span>
                  <span>{lang === "zh" ? "累计病例" : "Total cases"}: {String(metrics.total_cases ?? "-")}</span>
                  <span>{lang === "zh" ? "变化" : "Change"}: {String(metrics.change_pct ?? "N/A")}%</span>
                  <span>{lang === "zh" ? "风险分" : "Risk score"}: {String(risk.score ?? "-")}</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

function AnalyticalV3ReportBody({ detail, lang }: { detail: ReportDetail; lang: "en" | "zh" }) {
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

function ReportDetailPanel({
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
          {formatDate(detail.period_start)} - {formatDate(detail.period_end)}
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

export default function ReportsPage() {
  const { lang, countryId } = useAppStore();
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [flowFilter, setFlowFilter] = useState<FlowGroupId | "all">("all");
  const [search, setSearch] = useState("");

  const { data: reports, isLoading } = useReports(countryId);
  const { data: detail, isFetching: detailLoading } = useReportDetail(selectedUuid);
  const list = reports ?? [];

  useEffect(() => {
    if (selectedUuid && !list.some((item) => item.report_uuid === selectedUuid)) {
      setSelectedUuid(null);
    }
  }, [list, selectedUuid]);

  const flowCounts = useMemo(() => {
    return {
      all: list.length,
      queue: list.filter((item) => FLOW_GROUPS[0].statuses.includes(item.status)).length,
      review: list.filter((item) => FLOW_GROUPS[1].statuses.includes(item.status)).length,
      published: list.filter((item) => FLOW_GROUPS[2].statuses.includes(item.status)).length,
    };
  }, [list]);

  const filteredReports = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const activeStatuses =
      flowFilter === "all"
        ? null
        : FLOW_GROUPS.find((group) => group.id === flowFilter)?.statuses ?? [];

    return list.filter((report) => {
      if (activeStatuses && !activeStatuses.includes(report.status)) return false;
      if (!normalizedSearch) return true;

      const haystack = [
        report.title,
        report.report_type,
        report.status,
        report.country_name,
        report.primary_disease,
        ...(report.disease_names ?? []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return haystack.includes(normalizedSearch);
    });
  }, [flowFilter, list, search]);

  const selectedReport = useMemo(
    () => list.find((item) => item.report_uuid === selectedUuid) ?? null,
    [list, selectedUuid],
  );

  const avgQuality = useMemo(() => {
    const scores = list
      .map((item) => item.quality_score)
      .filter((score): score is number => score != null);

    if (scores.length === 0) return null;
    return scores.reduce((sum, score) => sum + score, 0) / scores.length;
  }, [list]);

  const hasFilters = Boolean(search || flowFilter !== "all");

  const columns = useMemo<DataTableColumn<ReportListItem>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (report) => <StatusBadge status={report.status}>{statusLabel(lang, report.status)}</StatusBadge>,
      },
      {
        key: "report",
        header: t(lang, "report_title"),
        render: (report) => <ReportTitleCell report={report} />,
      },
      {
        key: "type",
        header: t(lang, "report_type"),
        render: (report) => (
          <span className="whitespace-nowrap font-mono text-xs text-tremor-content dark:text-dark-tremor-content">
            {report.report_type}
          </span>
        ),
      },
      {
        key: "quality",
        header: t(lang, "quality_score"),
        render: (report) => <QualityCell score={report.quality_score} />,
      },
      {
        key: "sections",
        header: t(lang, "sections"),
        render: (report) => (
          <span className="whitespace-nowrap text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {report.section_count}
          </span>
        ),
      },
      {
        key: "period",
        header: lang === "zh" ? "周期" : "Period",
        render: (report) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDate(report.period_start)} - {formatDate(report.period_end)}
          </span>
        ),
      },
      {
        key: "created",
        header: lang === "zh" ? "创建时间" : "Created",
        render: (report) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDate(report.created_at)}
          </span>
        ),
      },
    ],
    [lang],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_results")}
        title={t(lang, "reports")}
        description={t(lang, "publication_flow")}
        meta={
          <>
            <StatusBadge tone={flowCounts.queue > 0 ? "warning" : "neutral"}>
              {t(lang, "flow_queue")} {flowCounts.queue}
            </StatusBadge>
            <StatusBadge tone="info">
              {t(lang, "flow_review")} {flowCounts.review}
            </StatusBadge>
            <StatusBadge tone="success">
              {t(lang, "flow_published")} {flowCounts.published}
            </StatusBadge>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={t(lang, "report_overview_total")}
          value={isLoading ? "-" : flowCounts.all}
          icon={<Layers3 className="h-4 w-4" />}
          tone="neutral"
          hint={lang === "zh" ? "当前国家报告" : "Reports for current country"}
        />
        <MetricTile
          label={t(lang, "report_overview_filtered")}
          value={isLoading ? "-" : filteredReports.length}
          icon={<Send className="h-4 w-4" />}
          tone="primary"
          hint={flowFilter === "all" ? t(lang, "flow_all") : t(lang, FLOW_GROUPS.find((group) => group.id === flowFilter)?.titleKey ?? "flow_all")}
        />
        <MetricTile
          label={t(lang, "report_overview_published")}
          value={isLoading ? "-" : flowCounts.published}
          icon={<FileText className="h-4 w-4" />}
          tone="success"
          hint={lang === "zh" ? "可用于对外发布" : "Ready for downstream use"}
        />
        <MetricTile
          label={t(lang, "report_overview_quality")}
          value={isLoading ? "-" : percent(avgQuality)}
          icon={<Gauge className="h-4 w-4" />}
          tone="info"
          hint={lang === "zh" ? "按已有质量分计算" : "Based on scored reports"}
        />
      </div>

      <FilterToolbar>
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            type="search"
            placeholder={lang === "zh" ? "搜索标题、疾病、类型" : "Search title, disease, or type"}
            className="h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background py-2 pl-9 pr-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={`inline-flex h-10 items-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition ${
              flowFilter === "all"
                ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
                : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
            }`}
            onClick={() => setFlowFilter("all")}
          >
            {t(lang, "flow_all")}
            <span className="rounded-tremor-default bg-black/10 px-2 py-0.5 text-xs dark:bg-white/10">{flowCounts.all}</span>
          </button>
          {FLOW_GROUPS.map((group) => (
            <button
              key={group.id}
              type="button"
              className={`inline-flex h-10 items-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition ${
                flowFilter === group.id
                  ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted dark:border-dark-tremor-brand dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
                  : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              }`}
              onClick={() => setFlowFilter(group.id)}
            >
              {t(lang, group.titleKey)}
              <span className="rounded-tremor-default bg-black/10 px-2 py-0.5 text-xs dark:bg-white/10">
                {flowCounts[group.id]}
              </span>
            </button>
          ))}
        </div>

        {hasFilters ? (
          <button
            type="button"
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle"
            onClick={() => {
              setSearch("");
              setFlowFilter("all");
            }}
          >
            <TimerReset className="h-4 w-4" />
            {lang === "zh" ? "重置" : "Reset"}
          </button>
        ) : null}
      </FilterToolbar>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((item) => (
            <div
              key={item}
              className="h-16 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background"
            />
          ))}
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={filteredReports}
          getRowKey={(report) => report.report_uuid}
          selectedRowKey={selectedUuid}
          onRowClick={(report) => setSelectedUuid(report.report_uuid)}
          emptyState={
            <EmptyState
              icon={<FileText className="h-10 w-10" />}
              title={t(lang, "flow_empty")}
              description={
                hasFilters
                  ? lang === "zh"
                    ? "当前筛选条件下没有报告。"
                    : "No reports match the current filters."
                  : lang === "zh"
                    ? "当前国家还没有生成报告。"
                    : "No reports have been generated for the current country."
              }
            />
          }
        />
      )}

      <DetailDrawer
        open={Boolean(selectedUuid)}
        title={selectedReport?.title ?? (lang === "zh" ? "报告详情" : "Report Detail")}
        subtitle={
          selectedReport ? (
            <span className="flex min-w-0 items-center gap-2">
              <StatusBadge status={selectedReport.status}>{statusLabel(lang, selectedReport.status)}</StatusBadge>
              <span className="truncate">{selectedReport.report_type}</span>
              <span>{formatDate(selectedReport.created_at)}</span>
            </span>
          ) : null
        }
        onClose={() => setSelectedUuid(null)}
        className="sm:w-[820px] sm:max-w-[820px]"
      >
        <ReportDetailPanel detail={detail} lang={lang} loading={detailLoading} />
      </DetailDrawer>
    </div>
  );
}
