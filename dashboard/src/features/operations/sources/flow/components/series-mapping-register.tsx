import { useMemo, useState } from "react";

import { type OntologySeries } from "@/features/operations/sources/api";

import { formatSeriesMetadata } from "../series-format";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { buildSeriesMappingRisks } from "../mapping-risks";

export function SeriesMappingRegister({
  rows,
  lang,
}: {
  rows: OntologySeries[];
  lang: "en" | "zh";
}) {
  const [search, setSearch] = useState("");
  const risks = useMemo(() => buildSeriesMappingRisks(rows), [rows]);
  const targetCount = useMemo(
    () => new Set(rows.map((row) => row.concept_id || row.target?.id).filter(Boolean)).size,
    [rows],
  );
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const filteredRows = useMemo(
    () => rows.filter((row) => {
      if (!normalizedSearch) return true;
      return [
        row.id,
        row.source_id,
        row.concept_id ?? "",
        ...(row.local_codes ?? []),
        ...(row.local_labels ?? []),
        row.target?.labels?.en ?? "",
        row.target?.labels?.zh ?? "",
        row.measure,
        row.mapping_relation,
        row.comparability,
      ]
        .join(" ")
        .toLocaleLowerCase()
        .includes(normalizedSearch);
    }),
    [normalizedSearch, rows],
  );

  return (
    <section className="app-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {lang === "zh" ? "来源序列与疾病映射" : "Source-series disease mappings"}
          </h2>
          <p className="mt-1 max-w-3xl text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {lang === "zh"
              ? "以 series registry 为全量口径；历史序列不会因 is_active=false 被隐藏。映射关系与可比性是两个独立结论。"
              : "The series registry is the complete source of truth; historical series remain visible even when is_active=false. Mapping relation and comparability are separate assertions."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge tone="info">{rows.length} {lang === "zh" ? "个序列" : "series"}</StatusBadge>
          <StatusBadge>{targetCount} {lang === "zh" ? "个疾病目标" : "disease targets"}</StatusBadge>
          <StatusBadge tone={risks.length > 0 ? "warning" : "success"}>
            {risks.length} {lang === "zh" ? "组语义复核" : "semantic review groups"}
          </StatusBadge>
        </div>
      </div>

      {risks.length > 0 ? (
        <div className="mt-4 space-y-2">
          <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">
            {lang === "zh"
              ? "同一目标、来源、粒度下存在多个非同义标签；代表序列选择不能被理解为合并。"
              : "Multiple non-synonymous labels share a target, source, and grain; representative selection must not be interpreted as a merge."}
          </p>
          <div className="grid gap-2 lg:grid-cols-2">
            {risks.map((risk) => (
              <div key={risk.key} className="rounded-tremor-default border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-900/50 dark:bg-amber-950/20">
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge tone="warning">{risk.conceptId}</StatusBadge>
                  <StatusBadge>{risk.frequency}</StatusBadge>
                  <StatusBadge tone={risk.measure === "registered_diagnoses" ? "warning" : "info"}>
                    {risk.measure === "registered_diagnoses"
                      ? (lang === "zh" ? "登记诊断量（非病例通知）" : "registered diagnoses (not case notifications)")
                      : formatSeriesMetadata(risk.measure)}
                  </StatusBadge>
                </div>
                <p className="mt-2 text-xs font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {risk.labels.join(" · ")}
                </p>
                <p className="mt-1 font-mono text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {risk.rows.map((row) => row.id).join(" · ")}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <details className="mt-4 rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {lang === "zh" ? `核查全部 ${rows.length} 个来源序列` : `Inspect all ${rows.length} source series`}
        </summary>
        <div className="border-t border-tremor-border p-3 dark:border-dark-tremor-border">
          <input
            aria-label={lang === "zh" ? "搜索来源序列" : "Search source series"}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={lang === "zh" ? "搜索 D-code、来源标签、metric…" : "Search D-code, source label, metric…"}
            className="mb-3 h-9 w-full max-w-md rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          />
          <div className="max-h-[560px] overflow-auto">
            <table className="w-full min-w-[980px] border-collapse text-left text-xs">
              <thead className="sticky top-0 bg-tremor-background dark:bg-dark-tremor-background">
                <tr className="border-b border-tremor-border dark:border-dark-tremor-border">
                  <th className="px-2 py-2">{lang === "zh" ? "序列 / 来源" : "Series / source"}</th>
                  <th className="px-2 py-2">{lang === "zh" ? "来源标签" : "Local label"}</th>
                  <th className="px-2 py-2">{lang === "zh" ? "映射目标" : "Target"}</th>
                  <th className="px-2 py-2">{lang === "zh" ? "指标 / 粒度" : "Metric / grain"}</th>
                  <th className="px-2 py-2">{lang === "zh" ? "映射 / 可比性" : "Mapping / comparability"}</th>
                  <th className="px-2 py-2">{lang === "zh" ? "可用性" : "Availability"}</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => (
                  <tr key={row.id} className="border-b border-tremor-border/70 align-top dark:border-dark-tremor-border/70">
                    <td className="px-2 py-2">
                      <div className="font-mono text-[11px] text-tremor-content-strong dark:text-dark-tremor-content-strong">{row.id}</div>
                      <div className="mt-1 font-mono text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{row.source_id}</div>
                    </td>
                    <td className="max-w-[260px] px-2 py-2 text-tremor-content dark:text-dark-tremor-content">{(row.local_labels ?? []).join(" · ") || "-"}</td>
                    <td className="px-2 py-2">
                      <div className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{row.concept_id || row.target?.id || "-"}</div>
                      <div className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? row.target?.labels?.zh || row.target?.labels?.en : row.target?.labels?.en || row.target?.labels?.zh}
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      <StatusBadge tone={row.measure === "registered_diagnoses" ? "warning" : "info"}>
                        {row.measure === "registered_diagnoses"
                          ? (lang === "zh" ? "登记诊断量（非病例通知）" : "registered diagnoses (not case notifications)")
                          : formatSeriesMetadata(row.measure)}
                      </StatusBadge>
                      <div className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{row.frequency} · {formatSeriesMetadata(row.reporting_basis)}</div>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge tone={row.mapping_relation === "exact" ? "success" : row.mapping_relation ? "warning" : "neutral"}>{formatSeriesMetadata(row.mapping_relation)}</StatusBadge>
                        <StatusBadge tone={row.comparability === "direct" ? "success" : "neutral"}>{formatSeriesMetadata(row.comparability)}</StatusBadge>
                      </div>
                      <div className="mt-1 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{formatSeriesMetadata(row.aggregation_policy)}</div>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge tone={row.status === "active" ? "success" : "neutral"}>{row.status}</StatusBadge>
                        {Array.from(new Set((row.availability ?? []).map((item) => item.status).filter(Boolean))).map((status) => (
                          <StatusBadge key={status} tone={status === "available" ? "success" : "warning"}>{status}</StatusBadge>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    </section>
  );
}

// ── Create crawl task modal ──────────────────────────────────────────────────
