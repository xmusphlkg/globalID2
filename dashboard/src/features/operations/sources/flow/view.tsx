"use client";

import { useMemo, useState } from "react";

import { SourceFeedback, SourceLinks } from "@/features/collection-sources/shared";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { type CountrySourceConfig, type DataSourceFlow, useOntologySeries, useSourceConfigs, useSourcesFlow } from "@/features/operations/sources/api";
import { useTaskEventStream } from "@/features/operations/tasks/api";

import { Text, Title } from "@/components/ui/tremor";

import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { GitBranch, Database, Download, Plus, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

import { FlowRow } from "./components/flow-row";
import { SeriesMappingRegister } from "./components/series-mapping-register";
import { CreateCrawlModal } from "./components/create-crawl-modal";
import { flowRowKey } from "./mapping-risks";

export default function SourcesFlowPage() {
  const { lang, countryId, countryName, countryCode } = useAppStore();
  const [scopeMode, setScopeMode] = useState<"selected" | "all">("selected");
  const effectiveCountryCode = scopeMode === "all" ? null : countryCode;
  const effectiveCountryName =
    scopeMode === "all"
      ? (lang === "zh" ? "全部国家" : "All countries")
      : countryName;

  const flowQuery = useSourcesFlow(effectiveCountryCode);
  const { data: flows, isLoading, error } = flowQuery;
  const sourceConfigQuery = useSourceConfigs(lang);
  const { data: sourceConfigs } = sourceConfigQuery;
  const { data: ontologySeries } = useOntologySeries(
    scopeMode === "selected" ? countryCode : null,
  );
  const sourceConfigByCountry = useMemo(() => {
    const map = new Map<string, CountrySourceConfig>();
    for (const config of sourceConfigs ?? []) {
      map.set(config.country_code.toUpperCase(), config);
    }
    return map;
  }, [sourceConfigs]);
  const selectedSourceConfig = sourceConfigByCountry.get((countryCode || "").toUpperCase()) ?? null;
  const countrySupported = scopeMode === "all" || Boolean(selectedSourceConfig?.supports_crawl);

  const [modalOpen, setModalOpen] = useState(false);

  useTaskEventStream({ extraQueryKeys: [["sources-flow"]] });

  const openModal = () => setModalOpen(true);
  const closeModal = () => setModalOpen(false);

  // summary counts
  const totalSources = flows?.length ?? 0;
  const totalSourceSeries = (flows ?? []).reduce(
    (total, flow) => total + (flow.source_series_count ?? 0),
    0,
  );
  const totalSourceObservations = (flows ?? []).reduce(
    (total, flow) => total + (flow.source_observation_count ?? 0),
    0,
  );
  const activeFlows = flows?.filter((f) =>
    f.stages.some((s) => s.status === "running"),
  ).length ?? 0;
  const failedFlows = flows?.filter((f) =>
    f.stages.some((s) => s.status === "failed"),
  ).length ?? 0;
  const completedFlows = flows?.filter((f) =>
    f.stages.some((s) => Boolean(s.status))
    && f.stages.every((s) => !s.status || s.status === "completed"),
  ).length ?? 0;
  const flowsByCountry = useMemo(() => {
    const groups = new Map<string, DataSourceFlow[]>();
    for (const flow of flows ?? []) {
      const key = flow.country_code || flow.country_name || "Unknown";
      const current = groups.get(key) ?? [];
      current.push(flow);
      groups.set(key, current);
    }
    return Array.from(groups.entries())
      .map(([key, items]) => ({
        key,
        countryName: items[0]?.country_name || key,
        countryCode: items[0]?.country_code || null,
        items: items.sort(
          (a, b) =>
            (b.source_observation_count ?? b.record_count)
            - (a.source_observation_count ?? a.record_count),
        ),
      }))
      .sort((a, b) => a.countryName.localeCompare(b.countryName));
  }, [flows]);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "collection")}
        title={t(lang, "source_ingestion")}
        description={t(lang, "source_ingestion_desc")}
        meta={
          <>
            <StatusBadge tone={scopeMode === "all" ? "info" : "primary"}>
              {scopeMode === "all" ? (lang === "zh" ? "全部国家" : "All countries") : effectiveCountryName}
            </StatusBadge>
            <StatusBadge tone={failedFlows > 0 ? "danger" : "success"}>
              {failedFlows > 0
                ? lang === "zh"
                  ? `${failedFlows} 个异常流程`
                  : `${failedFlows} failed flows`
                : lang === "zh"
                  ? "无异常流程"
                  : "No failed flows"}
            </StatusBadge>
          </>
        }
        actions={
          countryId && countrySupported ? (
            <button
              onClick={openModal}
              className="flex h-10 items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
            >
              <Plus className="h-4 w-4" />
              {t(lang, "source_ingestion_new")}
            </button>
          ) : null
        }
      />
      <div className="flex flex-wrap gap-2"><SourceLinks type="crawl_data" kind="ingestion" /></div>
      <SourceFeedback query={sourceConfigQuery} />

      <FilterToolbar>
        <div className="flex items-center gap-1 rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-1 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <button
            onClick={() => setScopeMode("selected")}
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${scopeMode === "selected" ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong" : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"}`}
          >
            {lang === "zh" ? "当前国家 / 地区" : "Selected country / region"}
          </button>
          <button
            onClick={() => setScopeMode("all")}
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${scopeMode === "all" ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong" : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"}`}
          >
            {lang === "zh" ? "全部国家" : "All countries"}
          </button>
        </div>
      </FilterToolbar>

      {scopeMode === "selected" && countryId && !countrySupported && !sourceConfigQuery.isLoading && !sourceConfigQuery.isError && (
        <div className="app-panel p-4">
          <Text>
            {lang === "zh"
              ? "当前国家的自动爬取工作流尚未设计，flow 仅展示已入库数据来源。"
              : "Automated crawl workflow is not designed for this country yet. Flow currently shows ingested source data only."}
          </Text>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <MetricTile
          label={lang === "zh" ? "来源序列" : "Source series"}
          value={totalSourceSeries}
          icon={<GitBranch className="h-4 w-4" />}
          tone="primary"
        />
        <MetricTile
          label={lang === "zh" ? "来源观测" : "Source observations"}
          value={totalSourceObservations}
          icon={<Database className="h-4 w-4" />}
          tone="info"
        />
        <MetricTile
          label={t(lang, "flow_kpi_sources")}
          value={totalSources}
          icon={<Download className="h-4 w-4" />}
          tone="info"
        />
        <MetricTile
          label={t(lang, "flow_kpi_active")}
          value={activeFlows}
          icon={<Loader2 className="h-4 w-4" />}
          tone="warning"
        />
        <MetricTile
          label={t(lang, "flow_kpi_completed")}
          value={completedFlows}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="success"
        />
        <MetricTile
          label={t(lang, "flow_kpi_failed")}
          value={failedFlows}
          icon={<AlertCircle className="h-4 w-4" />}
          tone={failedFlows > 0 ? "danger" : "success"}
        />
      </div>

      {/* Country guard */}
      {scopeMode === "selected" && !countryId ? (
        <div className="app-panel p-4">
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <GitBranch className="mb-4 h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Title>{t(lang, "flow_select_country")}</Title>
            <Text>{t(lang, "flow_select_country_hint")}</Text>
          </div>
        </div>
      ) : isLoading ? (
        <div role="status" aria-label={t(lang, "source_loading")} className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
          ))}
        </div>
      ) : error ? (
        <SourceFeedback query={flowQuery} />
      ) : !flows || flows.length === 0 ? (
        <div className="app-panel p-4">
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <GitBranch className="mb-4 h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Title>{t(lang, "no_data")}</Title>
            <Text>{t(lang, "flow_no_sources_hint")}</Text>
            <button
              onClick={openModal}
              disabled={!countrySupported || !countryId}
              className="mt-4 flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-medium text-tremor-brand-inverted transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
            >
              <Plus className="h-4 w-4" />
              {t(lang, "flow_create_first_task")}
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {scopeMode === "all" ? (
            flowsByCountry.map((group) => (
              <section key={group.key} className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <GitBranch className="h-5 w-5 text-tremor-brand dark:text-dark-tremor-brand" />
                  <Title>
                    {group.countryName} {group.countryCode ? `(${group.countryCode})` : ""}
                  </Title>
                  <Text className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {group.items.length} {t(lang, "flow_sources_count")}
                  </Text>
                </div>
                {group.items.map((flow) => (
                  <FlowRow
                    key={flowRowKey(flow)}
                    flow={flow}
                    lang={lang}
                  />
                ))}
              </section>
            ))
          ) : (
            <>
              <div className="flex items-center gap-2">
                <GitBranch className="h-5 w-5 text-tremor-brand dark:text-dark-tremor-brand" />
                <Title>
                  {effectiveCountryName} — {flows.length} {t(lang, "flow_sources_count")}
                </Title>
              </div>
              {flows.map((flow) => (
                <FlowRow
                  key={flowRowKey(flow)}
                  flow={flow}
                  lang={lang}
                />
              ))}
            </>
          )}
        </div>
      )}

      {scopeMode === "selected" && ontologySeries && ontologySeries.length > 0 ? (
        <SeriesMappingRegister rows={ontologySeries} lang={lang} />
      ) : null}

      {/* Modal */}
      {modalOpen && countryId && (
        <CreateCrawlModal
          open={true}
          countryId={countryId}
          countryName={countryName}
          countryCode={countryCode}
          sourceConfig={selectedSourceConfig}
          lang={lang}
          onClose={closeModal}
        />
      )}
    </div>
  );
}
