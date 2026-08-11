import Link from "next/link";
import { BookOpen, ListChecks, Loader2, RefreshCcw, ShieldCheck } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { Badge } from "@/components/ui/tremor";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { KnowledgePageState } from "../hooks/use-knowledge-page";
import { BriefCard } from "./brief-card";
import { SourceTraceItem } from "./source-trace-item";
import { ActionButton, DetailSkeleton, Panel, fieldValue, statusColor } from "./shared";

export function DetailPanel({ lang, state }: { lang: "en" | "zh"; state: KnowledgePageState }) {
  const {
    detail, detailLoading, detailPanelRef, selectedDiseaseId, selectedDisease, detailTab, setDetailTab,
    briefLanguage, setBriefLanguage, detailSources, availableBriefLanguages, selectedBrief, refreshPending,
    handleSingleRefresh, taskLogsHref, detailTabs,
  } = state;
  return (
          <div ref={detailPanelRef} className="min-w-0 space-y-5">
            <Panel className="overflow-hidden p-0 lg:sticky lg:top-5 lg:max-h-[calc(100vh-2.5rem)]">
              <div className="border-b border-tremor-border px-4 py-4 dark:border-dark-tremor-border">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {lang === "zh" ? "知识简介查询" : "Knowledge brief query"}
                    </h2>
                    <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                      {selectedDisease
                        ? `${selectedDisease.disease_id} · ${selectedDisease.name_en ?? selectedDisease.name_zh ?? selectedDisease.disease_id}`
                        : t(lang, "knowledge_no_selection")}
                    </p>
                  </div>
                  {selectedDiseaseId ? (
                    <Link
                      href={taskLogsHref}
                      className="inline-flex items-center gap-1 rounded-tremor-default border border-blue-300/70 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-300"
                    >
                      <ListChecks className="h-3.5 w-3.5" />
                      {t(lang, "knowledge_view_task_logs")}
                    </Link>
                  ) : null}
                </div>
              </div>

              {selectedDisease ? (
                <>
                  <div className="space-y-3 border-b border-tremor-border px-4 py-3 dark:border-dark-tremor-border">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge color={statusColor(selectedDisease.knowledge_status)}>
                          {selectedDisease.knowledge_status}
                        </Badge>
                        <Badge color="slate">{selectedDisease.disease_id}</Badge>
                        <Badge color="violet" size="xs">
                          {selectedDisease.knowledge_profile_type}
                        </Badge>
                        <Badge color="blue" size="xs">
                          {selectedDisease.source_count} {lang === "zh" ? "条来源" : "sources"}
                        </Badge>
                      </div>
                      <ActionButton
                        tone="primary"
                        onClick={handleSingleRefresh}
                        disabled={refreshPending}
                        icon={refreshPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                        className="h-8 px-2.5 text-xs"
                      >
                        {lang === "zh" ? "刷新" : "Refresh"}
                      </ActionButton>
                    </div>

                    <div role="tablist" className="grid grid-cols-3 rounded-tremor-default bg-tremor-background-muted p-1 dark:bg-dark-tremor-background-muted">
                      {detailTabs.map((tab) => {
                        const active = detailTab === tab.key;
                        return (
                          <button
                            key={tab.key}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            onClick={() => setDetailTab(tab.key)}
                            className={cn(
                              "inline-flex h-8 items-center justify-center gap-1.5 rounded-[4px] px-2 text-xs font-medium transition",
                              active
                                ? "bg-tremor-background text-tremor-content-strong shadow-sm dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                                : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong",
                            )}
                          >
                            {tab.label}
                            <span className="text-[10px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                              {tab.count}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="max-h-[72vh] overflow-y-auto px-4 py-4 lg:max-h-[calc(100vh-14rem)]">
                    {detailLoading ? (
                      <DetailSkeleton />
                    ) : detail ? (
                      <div className="space-y-4">
                        <div className={cn(
                          "rounded-tremor-default border px-3 py-3",
                          detail.evidence_quality?.sufficient
                            ? "border-emerald-300 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/20"
                            : "border-amber-300 bg-amber-50/70 dark:border-amber-900 dark:bg-amber-950/20",
                        )}>
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-xs font-semibold uppercase text-tremor-content-strong dark:text-dark-tremor-content-strong">
                              {lang === "zh" ? "生成前证据门禁" : "Pre-generation evidence gate"}
                            </p>
                            <Badge color={detail.evidence_quality?.sufficient ? "emerald" : "amber"} size="xs">
                              {detail.evidence_quality?.sufficient ? (lang === "zh" ? "可生成" : "ready") : (lang === "zh" ? "阻断" : "blocked")}
                            </Badge>
                          </div>
                          <p className="mt-2 text-xs leading-5 text-tremor-content dark:text-dark-tremor-content">
                            {lang === "zh" ? "正文来源" : "Grounded"} {detail.evidence_quality?.grounded_source_count ?? 0}
                            {" · "}{lang === "zh" ? "权威来源" : "Authorities"} {detail.evidence_quality?.authoritative_source_count ?? 0}
                            {" · "}{lang === "zh" ? "学术摘要" : "Scholarly"} {detail.evidence_quality?.scholarly_source_count ?? 0}
                          </p>
                          {detail.evidence_quality?.issues?.length ? (
                            <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
                              {detail.evidence_quality.issues.join(" · ")}
                            </p>
                          ) : null}
                          {detail.repair_sections?.length ? (
                            <p className="mt-1 text-xs text-tremor-content dark:text-dark-tremor-content">
                              {lang === "zh" ? "定向补全" : "Targeted repair"}: {detail.repair_sections.join(" · ")}
                            </p>
                          ) : null}
                        </div>

                        {detailTab === "briefs" ? (
                          <div className="space-y-3">
                            <div className="rounded-tremor-default border border-dashed border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
                              <div className="flex items-center gap-2">
                                <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                                <p className="text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  {lang === "zh" ? "目录简介" : "Catalogue brief"}
                                </p>
                              </div>
                              <p className="mt-2 line-clamp-5 whitespace-pre-line text-sm leading-6 text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                {selectedDisease.description || (lang === "zh" ? "暂无目录简介" : "No catalogue description")}
                              </p>
                            </div>

                            {availableBriefLanguages.length > 1 ? (
                              <div className="flex flex-wrap gap-2">
                                {availableBriefLanguages.map((language) => {
                                  const active = language === (selectedBrief?.language ?? briefLanguage);
                                  return (
                                    <button
                                      key={language}
                                      type="button"
                                      onClick={() => setBriefLanguage(language)}
                                      className={cn(
                                        "h-8 rounded-tremor-default border px-3 text-xs font-medium transition",
                                        active
                                          ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted"
                                          : "border-tremor-border bg-tremor-background text-tremor-content-emphasis hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle",
                                      )}
                                    >
                                      {language.toUpperCase()}
                                    </button>
                                  );
                                })}
                              </div>
                            ) : null}

                            {selectedBrief ? (
                              <BriefCard key={selectedBrief.language} brief={selectedBrief} lang={lang} />
                            ) : (
                              <div className="rounded-tremor-default border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                                <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
                                  {lang === "zh" ? "当前没有可显示的简介。" : "No brief available yet."}
                                </p>
                              </div>
                            )}
                          </div>
                        ) : null}

                        {detailTab === "sources" ? (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <BookOpen className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                                <p className="text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  {lang === "zh" ? "来源追踪" : "Source trace"}
                                </p>
                              </div>
                              <Badge color={detailSources.length > 0 ? "blue" : "slate"}>
                                {detailSources.length}
                              </Badge>
                            </div>
                            {detailSources.length > 0 ? (
                              detailSources.map((source) => (
                                <SourceTraceItem key={source.id} source={source} lang={lang} />
                              ))
                            ) : (
                              <div className="rounded-tremor-default border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                                <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
                                  {lang === "zh" ? "当前没有来源记录。" : "No source trace yet."}
                                </p>
                              </div>
                            )}
                          </div>
                        ) : null}

                        {detailTab === "meta" ? (
                          <div className="space-y-3">
                            {detail.summary ? (
                              <div className="grid grid-cols-2 gap-2 text-xs">
                                {[
                                  [lang === "zh" ? "简介数" : "Brief count", detail.summary.brief_count],
                                  [lang === "zh" ? "来源数" : "Source count", detail.summary.source_count],
                                  [lang === "zh" ? "已发布" : "Published", detail.summary.published_briefs],
                                  [lang === "zh" ? "待审核" : "Needs review", detail.summary.review_briefs],
                                ].map(([label, value]) => (
                                  <div key={String(label)} className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
                                    <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      {String(label)}
                                    </p>
                                    <p className="mt-1 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                      {fieldValue(value)}
                                    </p>
                                  </div>
                                ))}
                              </div>
                            ) : null}

                            <div className="rounded-tremor-default border border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
                              <div className="grid grid-cols-2 gap-3 text-xs">
                                {[
                                  [lang === "zh" ? "英文名" : "English name", selectedDisease.name_en],
                                  [lang === "zh" ? "中文名" : "Chinese name", selectedDisease.name_zh],
                                  ["ICD-10", selectedDisease.icd_10],
                                  ["ICD-11", selectedDisease.icd_11],
                                  [lang === "zh" ? "类别" : "Category", selectedDisease.category],
                                  [lang === "zh" ? "更新时间" : "Updated", selectedDisease.knowledge_updated_at],
                                ].map(([label, value]) => (
                                  <div key={String(label)} className="min-w-0">
                                    <p className="text-[10px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      {String(label)}
                                    </p>
                                    <p className="mt-1 break-words text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                      {fieldValue(value)}
                                    </p>
                                  </div>
                                ))}
                              </div>
                              <div className="mt-3 flex flex-wrap gap-2">
                                {selectedDisease.published_languages.length > 0 ? (
                                  selectedDisease.published_languages.map((language) => (
                                    <Badge key={language} color="emerald" size="xs">
                                      {language.toUpperCase()}
                                    </Badge>
                                  ))
                                ) : (
                                  <Badge color="slate" size="xs">
                                    {lang === "zh" ? "无已发布语言" : "No published language"}
                                  </Badge>
                                )}
                              </div>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      <div className="rounded-tremor-default border border-dashed border-tremor-border px-4 py-8 text-center dark:border-dark-tremor-border">
                        <p className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "knowledge_no_selection")}</p>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="px-4 py-10">
                  <EmptyState
                    icon={<BookOpen className="h-10 w-10" />}
                    title={t(lang, "knowledge_no_selection")}
                  />
                </div>
              )}
            </Panel>
          </div>
  );
}
