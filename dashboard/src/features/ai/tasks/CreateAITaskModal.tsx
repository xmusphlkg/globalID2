"use client";

import { CheckCircle2, Loader2, X } from "lucide-react";
import { Text, Title } from "@/components/ui/tremor";
import { t } from "@/lib/i18n";
import { useCreateAITaskForm, type Language } from "./hooks";

const inputCls =
  "w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis";
const labelCls =
  "mb-1 block text-xs font-medium text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis";

export function CreateAITaskModal({
  open,
  countryId,
  lang,
  onClose,
}: {
  open: boolean;
  countryId: number;
  lang: Language;
  onClose: () => void;
}) {
  const form = useCreateAITaskForm({ countryId, onClose });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-tremor-default bg-tremor-background p-6 shadow-xl dark:bg-dark-tremor-background">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
        >
          <X className="h-5 w-5" />
        </button>

        <Title className="mb-4">{lang === "zh" ? "新建 AI 任务" : "New AI Task"}</Title>

        {form.isSuccess ? (
          <div className="flex flex-col items-center gap-2 py-4 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-8 w-8" />
            <span className="text-sm font-medium">
              {lang === "zh" ? "任务创建成功并已开始执行" : "Task created and started"}
            </span>
            <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {lang === "zh" ? "可在下方任务列表实时追踪" : "Track progress in the task list below"}
            </span>
            {form.createdTaskUuid && (
              <div className="rounded-tremor-default border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">
                <div className="font-medium">{lang === "zh" ? "任务 UUID" : "Task UUID"}</div>
                <div className="mt-1 break-all font-mono">{form.createdTaskUuid}</div>
              </div>
            )}
          </div>
        ) : (
          <form onSubmit={form.handleSubmit} className="space-y-4">
            <div>
              <label className={labelCls}>{lang === "zh" ? "报告类型" : "Report Type"}</label>
              <select value={form.reportType} onChange={(e) => form.setReportType(e.target.value as typeof form.reportType)} className={inputCls}>
                <option value="daily">daily</option>
                <option value="weekly">weekly</option>
                <option value="monthly">monthly</option>
                <option value="special">special</option>
              </select>
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "报告语言" : "Report Language"}</label>
              <select value={form.reportLanguage} onChange={(e) => form.setReportLanguage(e.target.value as Language)} className={inputCls}>
                <option value="en">English</option>
                <option value="zh">中文</option>
              </select>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className={labelCls}>{lang === "zh" ? "报告布局" : "Report Layout"}</label>
                <select value={form.reportLayout} onChange={(e) => form.setReportLayout(e.target.value as typeof form.reportLayout)} className={inputCls}>
                  <option value="analytical_v3">analytical_v3</option>
                  <option value="structured">structured</option>
                  <option value="legacy">legacy</option>
                </select>
              </div>
              <div>
                <label className={labelCls}>{lang === "zh" ? "分析深度" : "Analysis Depth"}</label>
                <select value={form.analysisDepth} onChange={(e) => form.setAnalysisDepth(e.target.value as typeof form.analysisDepth)} className={inputCls}>
                  <option value="deep">deep</option>
                  <option value="deterministic">deterministic</option>
                </select>
              </div>
            </div>

            <div>
              <label className={labelCls}>{t(lang, "priority")}</label>
              <select value={form.priority} onChange={(e) => form.setPriority(e.target.value as typeof form.priority)} className={inputCls}>
                <option value="low">low</option>
                <option value="normal">normal</option>
                <option value="high">high</option>
                <option value="urgent">urgent</option>
              </select>
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "回溯天数" : "Lookback Days"}</label>
              <input type="number" min={1} max={3650} value={form.days} onChange={(e) => form.setDays(Math.max(1, Number(e.target.value) || 1))} className={inputCls} />
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "质量阈值" : "Quality Threshold"}</label>
              <input
                type="number" min={0} max={1} step={0.01} value={form.qualityThreshold}
                onChange={(e) => {
                  const next = Number(e.target.value);
                  form.setQualityThreshold(Number.isFinite(next) ? Math.max(0, Math.min(1, next)) : 0.85);
                }}
                className={inputCls}
              />
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "任务名称（可选）" : "Task Name (optional)"}</label>
              <input type="text" value={form.taskName} onChange={(e) => form.setTaskName(e.target.value)} placeholder={lang === "zh" ? "例如：生成中国月报" : "e.g. Generate CN monthly report"} className={inputCls} />
            </div>

            <div>
              <label className={labelCls}>{lang === "zh" ? "描述（可选）" : "Description (optional)"}</label>
              <textarea value={form.description} onChange={(e) => form.setDescription(e.target.value)} rows={3} className={inputCls} placeholder={lang === "zh" ? "输入任务说明" : "Describe this task"} />
            </div>

            <div className="space-y-2.5">
              <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                <input type="checkbox" checked={form.enableReview} onChange={(e) => form.setEnableReview(e.target.checked)} className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                {lang === "zh" ? "启用 AI 审核" : "Enable AI review"}
              </label>
              <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                <input type="checkbox" checked={form.sendEmail} onChange={(e) => form.setSendEmail(e.target.checked)} className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                {lang === "zh" ? "完成后发送邮件" : "Send email after completion"}
              </label>
              <Text className="pl-6 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {form.settings?.smtp.alerting_ready
                  ? (lang === "zh" ? "SMTP 和收件人已就绪，完成邮件会发送到设置中心维护的邮箱列表。" : "SMTP and recipients are ready, so completion mail will go to the addresses managed in Settings.")
                  : (lang === "zh" ? "SMTP 告警未配置，先去设置中心补齐凭据。" : "SMTP alerts are not configured yet. Open Settings to finish setup.")}
              </Text>
              <label className="flex cursor-pointer items-center gap-2.5 text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                <input type="checkbox" checked={form.reuseFromFailed} onChange={(e) => form.setReuseFromFailed(e.target.checked)} className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />
                {lang === "zh" ? "从失败任务中复用已生成内容" : "Reuse generated content from failed tasks"}
              </label>
              <Text className="pl-6 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {lang === "zh" ? "关闭后将强制重新生成，不继承失败/中断任务的中间结果。" : "Turn off to force a fresh run without resuming failed/interrupted partial output."}
              </Text>
            </div>

            {form.error && <div className="rounded-tremor-default border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-300">{form.error}</div>}

            <div className="flex justify-end gap-3 pt-1">
              <button type="button" onClick={onClose} className="rounded-tremor-default border border-tremor-border px-4 py-2 text-sm text-tremor-content-emphasis transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-emphasis dark:hover:bg-dark-tremor-background-subtle">
                {lang === "zh" ? "取消" : "Cancel"}
              </button>
              <button type="submit" disabled={form.isPending} className="flex items-center gap-2 rounded-tremor-default bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-60">
                {form.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {lang === "zh" ? "创建并执行" : "Create & Run"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
