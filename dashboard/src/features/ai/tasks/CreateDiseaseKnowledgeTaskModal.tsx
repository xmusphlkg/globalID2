"use client";

import { BookOpen, CheckCircle2, CheckSquare2, Loader2, RefreshCcw, Search, X } from "lucide-react";
import { Badge, Text, Title, type Color } from "@/components/ui/tremor";
import { t } from "@/lib/i18n";
import { useDiseaseKnowledgeTaskForm, type Language } from "./hooks";

const sourceOptions = [
  { value: "who", label: "WHO" },
  { value: "wikidata", label: "Wikidata" },
  { value: "wikipedia", label: "Wikipedia" },
  { value: "msd", label: "MSD metadata" },
] as const;
const inputCls =
  "w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis";
const labelCls =
  "mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis";

export function CreateDiseaseKnowledgeTaskModal({
  open,
  lang,
  onClose,
}: {
  open: boolean;
  lang: Language;
  onClose: () => void;
}) {
  const form = useDiseaseKnowledgeTaskForm({ lang, onClose });
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 backdrop-blur-sm">
      <div className="relative w-full max-w-6xl rounded-tremor-default bg-tremor-background p-6 shadow-xl dark:bg-dark-tremor-background">
        <button onClick={onClose} className="absolute right-4 top-4 text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong">
          <X className="h-5 w-5" />
        </button>

        <div className="mb-5 flex items-start gap-3">
          <div className="rounded-tremor-default border border-violet-200 bg-violet-50 p-2 text-violet-600 dark:border-violet-900/40 dark:bg-violet-950/30 dark:text-violet-300">
            <BookOpen className="h-5 w-5" />
          </div>
          <div>
            <Title className="text-xl">{lang === "zh" ? "新建疾病知识更新任务" : "New disease knowledge update task"}</Title>
            <Text className="mt-1">
              {lang === "zh" ? "选择一个或多个疾病，批量刷新知识库，并在任务列表里查看每个疾病的日志。" : "Select one or more diseases, queue them in batch, and inspect each disease task log from the task list."}
            </Text>
          </div>
        </div>

        {form.isSuccess && form.successResult ? (
          <div className="flex flex-col items-center gap-3 py-8 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-9 w-9" />
            <div className="text-center">
              <div className="text-sm font-medium">
                {lang === "zh" ? `已创建 ${form.successResult.created_tasks.length} 个疾病知识任务` : `Created ${form.successResult.created_tasks.length} disease knowledge task(s)`}
              </div>
              <div className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "任务已进入队列，worker 会并行处理。" : "Tasks are queued and the worker will process them in parallel."}
              </div>
            </div>
            <div className="w-full max-w-xl space-y-3">
              <div className="max-h-32 overflow-auto rounded-tremor-default border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">
                {form.successResult.created_tasks.map((task) => <div key={task.task_uuid} className="break-all font-mono">{task.task_uuid}</div>)}
              </div>
              {form.successResult.skipped.length > 0 && (
                <div className="rounded-tremor-default border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
                  <div className="font-medium">{lang === "zh" ? "跳过项" : "Skipped items"}</div>
                  <div className="mt-2 space-y-1.5">
                    {form.successResult.skipped.map((item) => (
                      <div key={item.disease_id} className="flex flex-wrap gap-x-2 gap-y-1">
                        <span className="font-mono">{item.disease_id}</span><span>•</span><span>{item.reason}</span>
                        {item.existing_task_uuid && <><span>•</span><span className="font-mono">{item.existing_task_uuid}</span></>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <form onSubmit={form.handleSubmit} className="space-y-5">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.95fr)]">
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative flex-1 min-w-[220px]">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
                    <input type="search" value={form.search} onChange={(e) => form.setSearch(e.target.value)} className={`${inputCls} pl-9`} placeholder={lang === "zh" ? "搜索疾病名称、编码或分类" : "Search disease name, code, or category"} />
                  </div>
                  <button type="button" onClick={form.selectVisible} className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle">
                    <CheckSquare2 className="h-4 w-4" />{lang === "zh" ? "选择当前结果" : "Select filtered"}
                  </button>
                  <button type="button" onClick={form.clearSelection} className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-2 text-xs font-medium text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle">
                    <RefreshCcw className="h-4 w-4" />{lang === "zh" ? "清空" : "Clear"}
                  </button>
                </div>

                <div className="rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
                  <div className="flex items-center justify-between gap-2 border-b border-tremor-border px-3 py-2 text-xs text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">
                    <span>{lang === "zh" ? `已选 ${form.selectedCount} / ${form.catalogue?.length ?? 0}，当前筛选命中 ${form.filtered.length}` : `Selected ${form.selectedCount} / ${form.catalogue?.length ?? 0}, filtered ${form.filtered.length}`}</span>
                    <span>{lang === "zh" ? `筛选已选 ${form.visibleSelectedCount}` : `Selected in view ${form.visibleSelectedCount}`}</span>
                  </div>
                  <div className="max-h-[31rem] overflow-auto">
                    {form.isLoading ? (
                      <div className="space-y-3 p-3">{[1, 2, 3, 4].map((row) => <div key={row} className="h-16 animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />)}</div>
                    ) : form.filtered.length === 0 ? (
                      <div className="px-4 py-10 text-center text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "没有匹配的疾病。" : "No matching diseases."}</div>
                    ) : (
                      <div className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
                        {form.filtered.map((item) => {
                          const checked = form.selectedSet.has(item.disease_id);
                          const statusColor = item.knowledge_status === "published" ? "emerald" : item.knowledge_status === "requires_review" ? "amber" : "slate";
                          return (
                            <label key={item.disease_id} className={`flex cursor-pointer items-start gap-3 px-4 py-3 transition-colors hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-subtle ${checked ? "bg-violet-50/60 dark:bg-violet-950/20" : ""}`}>
                              <input type="checkbox" checked={checked} onChange={() => form.toggleDisease(item.disease_id)} className="mt-1 h-4 w-4 rounded border-tremor-border text-violet-600 focus:ring-violet-500" />
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <div className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{item.name_en ?? item.disease_id}</div>
                                  <Badge color={statusColor as Color}>{item.knowledge_status}</Badge>
                                  <Badge color="slate">{item.disease_id}</Badge>
                                  {item.category && <Badge color="blue">{item.category}</Badge>}
                                  <Badge color="violet">{item.source_count} sources</Badge>
                                </div>
                                <div className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{item.name_zh ? `${item.name_zh} · ` : ""}{item.description ?? ""}</div>
                                <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                                  {item.published_languages.map((language) => <span key={language} className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">{language.toUpperCase()}</span>)}
                                  {item.knowledge_updated_at && <span className="rounded-full border border-tremor-border px-2 py-0.5 text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">{item.knowledge_updated_at}</span>}
                                </div>
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="space-y-4">
                <div>
                  <label className={labelCls}>{lang === "zh" ? "批量任务名称（可选）" : "Batch task name (optional)"}</label>
                  <input type="text" value={form.taskName} onChange={(e) => form.setTaskName(e.target.value)} className={inputCls} placeholder={lang === "zh" ? "例如：刷新重点疾病知识库" : "e.g. Refresh key disease knowledge"} />
                </div>
                <div>
                  <label className={labelCls}>{lang === "zh" ? "任务说明（可选）" : "Description (optional)"}</label>
                  <textarea value={form.description} onChange={(e) => form.setDescription(e.target.value)} rows={4} className={inputCls} placeholder={lang === "zh" ? "说明这批任务的范围或目标" : "Describe the purpose or scope of this batch"} />
                </div>
                <div>
                  <label className={labelCls}>{lang === "zh" ? "生成器" : "Generator"}</label>
                  <select value={form.generator} onChange={(e) => form.setGenerator(e.target.value as typeof form.generator)} className={inputCls}><option value="auto">auto</option><option value="ai">ai</option></select>
                </div>
                <div>
                  <label className={labelCls}>{t(lang, "priority")}</label>
                  <select value={form.priority} onChange={(e) => form.setPriority(e.target.value as typeof form.priority)} className={inputCls}><option value="low">low</option><option value="normal">normal</option><option value="high">high</option><option value="urgent">urgent</option></select>
                </div>
                <div className="space-y-2 rounded-tremor-default border border-tremor-border p-3 dark:border-dark-tremor-border">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "来源组" : "Source groups"}</div>
                    <Badge color={form.selectedSources.length > 0 ? "emerald" : "slate"}>{form.selectedSources.length}</Badge>
                  </div>
                  <div className="space-y-2">
                    {sourceOptions.map((option) => (
                      <label key={option.value} className="flex cursor-pointer items-center gap-2 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                        <input type="checkbox" checked={form.selectedSources.includes(option.value)} onChange={() => form.toggleSource(option.value)} className="h-4 w-4 rounded border-tremor-border text-violet-600 focus:ring-violet-500" />{option.label}
                      </label>
                    ))}
                  </div>
                  <Text className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{lang === "zh" ? "WHO / Wikidata / Wikipedia 会优先用于公开 brief，MSD 只保存元数据和复核标记。" : "WHO / Wikidata / Wikipedia are prioritized for public briefs; MSD stays metadata-only and review-gated."}</Text>
                </div>
                <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                  <input type="checkbox" checked={form.forceRefresh} onChange={(e) => form.setForceRefresh(e.target.checked)} className="h-4 w-4 rounded border-tremor-border text-violet-600 focus:ring-violet-500" />
                  {lang === "zh" ? "强制刷新来源并重建 brief" : "Force refresh sources and rebuild briefs"}
                </label>
                {form.error && <div className="rounded-tremor-default border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">{form.error}</div>}
                <div className="rounded-tremor-default border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700 dark:border-slate-800 dark:bg-slate-950/30 dark:text-slate-300">
                  <div className="font-medium">{lang === "zh" ? "并行更新说明" : "Parallel update note"}</div>
                  <div className="mt-1">{lang === "zh" ? "任务创建后会进入统一队列，worker 会按并行度同时处理多个疾病任务。" : "Once queued, the worker processes multiple disease tasks concurrently according to its configured parallelism."}</div>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap justify-end gap-3 pt-1">
              <button type="button" onClick={onClose} className="rounded-tremor-default border border-tremor-border px-4 py-2 text-sm text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle">{lang === "zh" ? "取消" : "Cancel"}</button>
              <button type="submit" disabled={form.isPending || form.selectedIds.length === 0} className="flex items-center gap-2 rounded-tremor-default bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-60">
                {form.isPending && <Loader2 className="h-4 w-4 animate-spin" />}{lang === "zh" ? "排队更新" : "Queue Updates"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
