import Link from "next/link";
import { AlertTriangle, ArrowRight, BookOpen, CheckSquare2, Eye, ListChecks, Loader2, RefreshCcw, Search, ShieldCheck, Square } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { Badge } from "@/components/ui/tremor";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { ActionButton, Panel, briefStatusColor, formatDateTime, inputClass, statusColor } from "./shared";
import { SOURCE_GROUPS, type KnowledgeDisplayFilter, type KnowledgePageState, type KnowledgeStatusFilter, type RefreshGenerator, type RefreshPriority } from "../hooks/use-knowledge-page";

export function CataloguePanel({ lang, state }: { lang: "en" | "zh"; state: KnowledgePageState }) {
  const {
    isLoading, isFetching, search, setSearch, statusFilter, setStatusFilter, displayFilter, setDisplayFilter,
    selectedSet, selectedDiseaseId, refreshSources, forceRefresh, setForceRefresh, generator, setGenerator,
    priority, setPriority, refreshError, refreshResult, refreshPending, visibleEntries, selectedCount,
    visibleSelectedCount, visibleAllSelected, toggleSourceGroup, toggleDiseaseSelection, toggleVisibleSelection,
    clearSelection, handleBatchRefresh, queryKnowledgeBrief,
  } = state;
  return (
        <div className="min-w-0 space-y-5">
          <Panel>
            <div className="space-y-4">
              <FilterToolbar className="border-0 bg-transparent p-0 dark:bg-transparent">
                <div className="relative min-w-[240px] flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder={t(lang, "knowledge_search_placeholder")}
                    className={cn(inputClass, "pl-9")}
                  />
                </div>
                <select
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value as KnowledgeStatusFilter)}
                  className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  aria-label={lang === "zh" ? "知识状态筛选" : "Knowledge status filter"}
                >
                  <option value="all">{lang === "zh" ? "全部状态" : "All statuses"}</option>
                  <option value="published">{lang === "zh" ? "已发布" : "Published"}</option>
                  <option value="automating">{lang === "zh" ? "自动补全中" : "Automating"}</option>
                  <option value="requires_review">{lang === "zh" ? "待审核" : "Needs review"}</option>
                  <option value="blocked">{lang === "zh" ? "证据阻断" : "Blocked"}</option>
                </select>
                <select
                  value={displayFilter}
                  onChange={(event) => setDisplayFilter(event.target.value as KnowledgeDisplayFilter)}
                  className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  aria-label={lang === "zh" ? "画像完整度筛选" : "Profile completeness filter"}
                >
                  <option value="all">{lang === "zh" ? "全部画像" : "All profiles"}</option>
                  <option value="full">{lang === "zh" ? "完整" : "Full"}</option>
                  <option value="partial">{lang === "zh" ? "部分" : "Partial"}</option>
                  <option value="blocked">{lang === "zh" ? "阻断" : "Blocked"}</option>
                </select>
                <select
                  value={generator}
                  onChange={(event) => setGenerator(event.target.value as RefreshGenerator)}
                  className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  aria-label={lang === "zh" ? "生成方式" : "Generator"}
                >
                  <option value="ai">AI</option>
                  <option value="auto">Auto</option>
                </select>
                <select
                  value={priority}
                  onChange={(event) => setPriority(event.target.value as RefreshPriority)}
                  className="h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                  aria-label={lang === "zh" ? "任务优先级" : "Priority"}
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
              </FilterToolbar>

              <div className="flex flex-wrap items-center gap-2">
                <ActionButton onClick={toggleVisibleSelection} icon={<CheckSquare2 className="h-4 w-4" />}>
                  {t(lang, "knowledge_select_all_visible")}
                </ActionButton>
                <ActionButton onClick={clearSelection} icon={<Square className="h-4 w-4" />}>
                  {lang === "zh" ? "清空选择" : "Clear selection"}
                </ActionButton>
                <ActionButton
                  tone="primary"
                  onClick={handleBatchRefresh}
                  disabled={selectedCount === 0 || refreshPending}
                  icon={refreshPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                >
                  {t(lang, "knowledge_refresh_selected")}
                </ActionButton>
                <label className="inline-flex cursor-pointer items-center gap-2 rounded-tremor-default border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle">
                  <input
                    type="checkbox"
                    checked={forceRefresh}
                    onChange={(e) => setForceRefresh(e.target.checked)}
                    className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                  />
                  {t(lang, "knowledge_force_refresh")}
                </label>
                <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh"
                    ? `已选 ${selectedCount} 项，当前可见 ${visibleEntries.length} 项，已选可见 ${visibleSelectedCount} 项`
                    : `Selected ${selectedCount}, visible ${visibleEntries.length}, selected in view ${visibleSelectedCount}`}
                </span>
              </div>

              <details className="rounded-tremor-default border border-dashed border-tremor-border bg-tremor-background-muted/30 dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted/20">
                <summary className="cursor-pointer list-none px-3 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <ListChecks className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
                      <p className="text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "可信刷新源" : "Trusted refresh sources"}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {refreshSources.map((source) => (
                        <Badge key={source} color={SOURCE_GROUPS.find((item) => item.value === source)?.color ?? "slate"} size="xs">
                          {source}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </summary>
                <div className="border-t border-dashed border-tremor-border px-3 py-3 dark:border-dark-tremor-border">
                  <div className="flex flex-wrap gap-2">
                    {SOURCE_GROUPS.map((source) => {
                      const checked = refreshSources.includes(source.value);
                      return (
                        <label
                          key={source.value}
                          className="flex cursor-pointer items-center gap-2 rounded-full border border-tremor-border bg-white px-3 py-2 text-xs text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleSourceGroup(source.value)}
                            className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                          />
                          <span className="font-medium">{source.label}</span>
                          <Badge color={source.color} size="xs">
                            {source.value}
                          </Badge>
                        </label>
                      );
                    })}
                  </div>
                  <p className="mt-2 text-xs leading-6 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {lang === "zh"
                      ? "先用搜索发现层聚合 CDC、NIH、WHO、BMJ、MSD 等可信结果，再由 WHO、Wikidata、Wikipedia、PubMed 和 MSD 适配器补充结构化来源。"
                      : "The search discovery layer first gathers trusted CDC, NIH, WHO, BMJ, MSD, and Wikipedia results, then WHO, Wikidata, Wikipedia, PubMed, and MSD adapters add structured sources."}
                  </p>
                </div>
              </details>

              {refreshError ? (
                <div className="rounded-tremor-default border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/25 dark:text-rose-300">
                  {refreshError}
                </div>
              ) : null}

              {refreshResult ? (
                <div className="rounded-tremor-default border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900 dark:border-emerald-900/40 dark:bg-emerald-950/25 dark:text-emerald-200">
                  <div className="flex items-center gap-2 font-semibold">
                    <ShieldCheck className="h-4 w-4" />
                    {t(lang, "knowledge_refresh_result")}
                  </div>
                  <div className="mt-2 text-xs leading-6">
                    {lang === "zh"
                      ? `已创建 ${refreshResult.created_tasks.length} 个任务，跳过 ${refreshResult.skipped.length} 个疾病。队列中的任务会并行执行。`
                      : `Created ${refreshResult.created_tasks.length} task(s) and skipped ${refreshResult.skipped.length} disease(s). The queued tasks will execute in parallel.`}
                  </div>
                  {refreshResult.created_tasks.length > 0 ? (
                    <div className="mt-3 space-y-2">
                      {refreshResult.created_tasks.map((task) => (
                        <div
                          key={task.task_uuid}
                          className="flex flex-wrap items-center gap-2 rounded-tremor-default border border-emerald-200 bg-white px-3 py-2 text-xs dark:border-emerald-900/40 dark:bg-black/10"
                        >
                          <Badge color="emerald">{task.status}</Badge>
                          <span className="font-medium text-emerald-900 dark:text-emerald-200">
                            {task.task_name}
                          </span>
                          <span className="font-mono text-emerald-800 dark:text-emerald-300">
                            {task.task_uuid}
                          </span>
                          <Link
                            href={`/production/ai?task=${encodeURIComponent(task.task_uuid)}&task_type=update_disease_knowledge`}
                            className="inline-flex items-center gap-1 rounded-tremor-default border border-emerald-200 px-2 py-1 font-medium text-emerald-800 transition hover:bg-emerald-100 dark:border-emerald-900/40 dark:text-emerald-300 dark:hover:bg-emerald-950/30"
                          >
                            {t(lang, "knowledge_view_task_logs")}
                            <ArrowRight className="h-3.5 w-3.5" />
                          </Link>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {refreshResult.skipped.length > 0 ? (
                    <div className="mt-3 rounded-tremor-default border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/25 dark:text-amber-200">
                      <div className="font-medium">{lang === "zh" ? "跳过项" : "Skipped items"}</div>
                      <div className="mt-2 space-y-1.5">
                        {refreshResult.skipped.map((item) => (
                          <div key={item.disease_id} className="flex flex-wrap gap-x-2 gap-y-1">
                            <span className="font-mono">{item.disease_id}</span>
                            <span>•</span>
                            <span>{item.reason}</span>
                            {item.existing_task_uuid ? (
                              <>
                                <span>•</span>
                                <span className="font-mono">{item.existing_task_uuid}</span>
                              </>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </Panel>

          <Panel>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {lang === "zh" ? "疾病目录" : "Disease catalogue"}
                </h2>
                <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {lang === "zh"
                    ? "点击“查询简介”读取该疾病的 AI 知识简介、来源追踪和刷新记录。"
                    : "Use Query brief to load AI briefs, source traces, and refresh records for a disease."}
                </p>
              </div>
              <Badge color={isFetching ? "amber" : "slate"}>
                {isFetching ? (lang === "zh" ? "刷新中" : "Refreshing") : `${visibleEntries.length}`}
              </Badge>
            </div>

            <div className="mt-4 overflow-hidden rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
              <div className="max-h-[70vh] min-h-[360px] overflow-auto lg:max-h-[calc(100vh-24rem)]">
                <table className="w-full min-w-[980px] divide-y divide-tremor-border dark:divide-dark-tremor-border">
                  <thead className="sticky top-0 z-10 bg-tremor-background-subtle shadow-sm dark:bg-dark-tremor-background-subtle">
                    <tr>
                      <th className="w-10 px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        <input
                          type="checkbox"
                          aria-label={lang === "zh" ? "选择当前可见疾病" : "Select all visible diseases"}
                          checked={visibleAllSelected}
                          onChange={toggleVisibleSelection}
                          className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                        />
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "病种" : "Disease"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "简介" : "Profile"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "状态" : "Status"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "语言" : "Languages"}
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                        {lang === "zh" ? "来源" : "Sources"}
                      </th>
                        <th className="px-3 py-2 text-left text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {lang === "zh" ? "更新时间" : "Updated"}
                        </th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {lang === "zh" ? "操作" : "Actions"}
                        </th>
                      </tr>
                  </thead>
                  <tbody className="divide-y divide-tremor-border bg-white dark:divide-dark-tremor-border dark:bg-dark-tremor-background">
                    {isLoading ? (
                      [1, 2, 3, 4, 5].map((index) => (
                        <tr key={index}>
                            <td colSpan={8} className="px-3 py-4">
                            <div className="h-10 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />
                          </td>
                        </tr>
                      ))
                    ) : visibleEntries.length > 0 ? (
                      visibleEntries.map((item) => {
                        const checked = selectedSet.has(item.disease_id);
                        const active = selectedDiseaseId === item.disease_id;
                        const publishedLanguages = item.published_languages.length > 0 ? item.published_languages : [];

                        return (
                          <tr
                            key={item.disease_id}
                            className={active ? "bg-blue-50/60 dark:bg-blue-950/20" : "hover:bg-tremor-background-subtle/60 dark:hover:bg-dark-tremor-background-subtle/60"}
                          >
                            <td className="px-3 py-3 align-top">
                              <input
                                type="checkbox"
                                aria-label={
                                  lang === "zh"
                                    ? `选择疾病 ${item.name_zh ?? item.name_en ?? item.disease_id}`
                                    : `Select disease ${item.name_en ?? item.name_zh ?? item.disease_id}`
                                }
                                checked={checked}
                                onChange={() => toggleDiseaseSelection(item.disease_id)}
                                className="h-4 w-4 rounded border-tremor-border text-blue-600 focus:ring-blue-500"
                              />
                            </td>
                            <td className="px-3 py-3 align-top">
                                <button
                                  type="button"
                                  onClick={() => queryKnowledgeBrief(item.disease_id)}
                                  className="text-left"
                              >
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="font-mono text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                    {item.disease_id}
                                  </span>
                                  <Badge color={statusColor(item.knowledge_status)}>{item.knowledge_status}</Badge>
                                </div>
                                <p className="mt-1 text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
                                  {item.name_en ?? item.name_zh ?? item.disease_id}
                                </p>
                                {item.name_en && item.name_zh ? (
                                  <p className="mt-0.5 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                    {item.name_zh} / {item.name_en}
                                  </p>
                                ) : null}
                              </button>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <p className="max-w-xl whitespace-pre-line text-sm leading-6 text-tremor-content dark:text-dark-tremor-content line-clamp-3">
                                {item.description || (lang === "zh" ? "暂无目录描述" : "No catalogue description")}
                              </p>
                              <div className="mt-2 flex flex-wrap gap-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                {item.icd_10 ? <span className="rounded-full border border-tremor-border px-2 py-0.5">ICD-10 {item.icd_10}</span> : null}
                                {item.icd_11 ? <span className="rounded-full border border-tremor-border px-2 py-0.5">ICD-11 {item.icd_11}</span> : null}
                                {item.category ? <span className="rounded-full border border-tremor-border px-2 py-0.5">{item.category}</span> : null}
                              </div>
                            </td>
                            <td className="px-3 py-3 align-top">
                              {(() => {
                                const requiredGaps = item.required_gap_sections ?? [];
                                const reasons = Object.values(item.repair_reasons_by_language ?? {}).flat();
                                const revalidationOnly = reasons.length > 0 && reasons.every((reason) => reason === "evidence_revalidation");
                                return (
                                  <>
                              <Badge color={statusColor(item.knowledge_status)}>
                                {item.knowledge_status}
                              </Badge>
                              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                <Badge color={item.knowledge_display_mode === "full" ? "emerald" : item.knowledge_display_mode === "partial" ? "amber" : "slate"} size="xs">
                                  {item.knowledge_display_mode}
                                </Badge>
                                <span className="text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                  {lang === "zh" ? "字段完整度" : "Field coverage"} {Math.round(item.knowledge_completeness * 100)}%
                                </span>
                              </div>
                              {requiredGaps.length > 0 ? (
                                <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300">
                                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                  <span>{lang === "zh" ? "必填缺口" : "Required gaps"}: {requiredGaps.join(" · ")}</span>
                                </div>
                              ) : revalidationOnly ? (
                                <div className="mt-2 text-xs text-blue-700 dark:text-blue-300">
                                  {lang === "zh" ? "证据策略复核中" : "Evidence policy revalidation"}
                                </div>
                              ) : null}
                              <div className="mt-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                {item.brief_statuses && Object.keys(item.brief_statuses).length > 0 ? (
                                  <div className="space-y-1">
                                    {Object.entries(item.brief_statuses).map(([language, briefStatus]) => (
                                      <div key={language} className="flex items-center gap-2">
                                        <span className="font-medium">{language.toUpperCase()}</span>
                                        <Badge color={briefStatusColor(briefStatus)} size="xs">
                                          {briefStatus}
                                        </Badge>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <span>{lang === "zh" ? "暂无简介" : "No brief yet"}</span>
                                )}
                              </div>
                                  </>
                                );
                              })()}
                            </td>
                            <td className="px-3 py-3 align-top">
                              <div className="flex flex-wrap gap-1.5">
                                {publishedLanguages.length > 0 ? (
                                  publishedLanguages.map((language) => (
                                    <Badge key={language} color="emerald" size="xs">
                                      {language.toUpperCase()}
                                    </Badge>
                                  ))
                                  ) : (
                                    <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                      —
                                    </span>
                                  )}
                              </div>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <Badge color={item.source_count > 3 ? "emerald" : item.source_count > 0 ? "amber" : "slate"}>
                                {item.source_count}
                              </Badge>
                            </td>
                              <td className="px-3 py-3 align-top text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                                {formatDateTime(item.knowledge_updated_at)}
                              </td>
                              <td className="px-3 py-3 align-top text-right">
                                <ActionButton
                                  onClick={() => queryKnowledgeBrief(item.disease_id)}
                                  icon={<Eye className="h-4 w-4" />}
                                  className="h-8 px-2.5 text-xs"
                                >
                                  {lang === "zh" ? "查询简介" : "Query brief"}
                                </ActionButton>
                              </td>
                            </tr>
                        );
                      })
                    ) : (
                      <tr>
                          <td colSpan={8} className="px-3 py-12">
                            <EmptyState
                              icon={<BookOpen className="h-10 w-10" />}
                              title={t(lang, "knowledge_empty")}
                            />
                          </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
            </Panel>
          </div>
  );
}
