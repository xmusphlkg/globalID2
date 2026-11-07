"use client";

import ReactMarkdown from "react-markdown";
import { type ReportDetail } from "@/features/reports/api";
import { ReportFigureList } from "./report-figures";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { figureText } from "../../../../shared/report-figure-options";

type Row = Record<string, unknown>;
const record = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const array = (value: unknown): Row[] => Array.isArray(value) ? value.map(record) : [];

export function ReportV4Preview({ detail, lang }: { detail: ReportDetail; lang: "zh" | "en" }) {
  const metadata = record(detail.metadata);
  const document = record(metadata.report_document_v4);
  const review = record(document.peer_review);
  const gate = record(metadata.quality_gate);
  const metrics = record(document.metrics);
  const sections = array(document.sections).sort((left, right) => Number(left.order) - Number(right.order));
  const zh = lang === "zh";
  return <div className="space-y-5">
    <h2 className="text-lg font-semibold">{figureText(document.title, lang)}</h2>
    <p className="cc-muted text-sm">{detail.period_start.slice(0, 10)} — {detail.period_end.slice(0, 10)}</p>
    <div className="flex flex-wrap gap-2"><StatusBadge status={detail.status}>{detail.status}</StatusBadge><StatusBadge tone={review.status === "published" ? "success" : "warning"}>{review.status === "published" ? (zh ? "AI 同行评审通过" : "AI peer review passed") : (zh ? "旧版报告，无同行评审记录" : "Legacy report; no peer-review record")}</StatusBadge><StatusBadge tone={gate.passed ? "success" : "warning"}>{gate.passed ? (zh ? "报告质量校验通过" : "Quality gate passed") : (zh ? "待校验" : "Validation pending")}</StatusBadge></div>
    <p className="cc-muted text-xs">{zh ? "评审通过表示内容已就绪；对外上线状态请查看发布任务。" : "Review acceptance means the content is ready. Check the publication task for live availability."}</p>
    <dl className="grid grid-cols-3 gap-3 text-sm">{[[zh ? "记录" : "Records", metrics.record_count], [zh ? "疾病" : "Diseases", metrics.disease_count], [zh ? "图表" : "Figures", array(document.figures).length]].map(([label, value]) => <div key={String(label)} className="rounded border border-[var(--cc-border)] p-3"><dt className="cc-muted">{String(label)}</dt><dd className="mt-1 font-semibold">{value == null ? "—" : String(value)}</dd></div>)}</dl>
    <div className="report-markdown"><ReactMarkdown>{figureText(document.summary, lang)}</ReactMarkdown></div>
    {review.status === "published" && <details className="rounded border border-[var(--cc-border)] p-3"><summary className="cursor-pointer text-sm">{zh ? "评审与选图记录" : "Review and figure-selection record"}</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(review, null, 2)}</pre></details>}
    {sections.map(section => <article key={String(section.id)} className="rounded border border-[var(--cc-border)] p-4">
      <h3 className="mb-3 font-semibold">{String(section.order)}. {figureText(section.title, lang)}</h3>
      <div className="report-markdown"><ReactMarkdown>{figureText(section.body, lang)}</ReactMarkdown></div>
      <ReportFigureList figures={array(section.figures).filter(figure => figure.section_type === section.type)} figureData={record(document.figure_data)} lang={lang} placement="after_content" />
    </article>)}
  </div>;
}
