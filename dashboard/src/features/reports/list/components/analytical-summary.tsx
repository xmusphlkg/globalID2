

import { StatusBadge } from "@/components/ui/StatusBadge";
import { type ReportDetail } from "@/features/reports/api";

import { percent, asRecord, asArray, asNumber, asString, formatRiskName } from "../presentation";

export function AnalyticalV3Summary({ detail, lang }: { detail: ReportDetail; lang: "en" | "zh" }) {
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
