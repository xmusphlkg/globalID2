"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Bell,
  CheckCircle2,
  Languages,
  LoaderCircle,
  Play,
  RefreshCw,
  Send,
} from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  type NotificationCampaign,
  type NotificationDelivery,
  useCreateNotificationCampaign,
  useNotificationCampaignDetail,
  useNotificationCampaigns,
  useStartNotificationSend,
  useSubscriptionOptions,
} from "@/lib/hooks/useSubscriptions";
import { formatNumber } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const defaultLocales = [
  { value: "en", label_en: "English", label_zh: "英文" },
  { value: "zh", label_en: "Chinese", label_zh: "中文" },
  { value: "ja", label_en: "Japanese", label_zh: "日文" },
  { value: "ko", label_en: "Korean", label_zh: "韩文" },
  { value: "es", label_en: "Spanish", label_zh: "西班牙文" },
  { value: "fr", label_en: "French", label_zh: "法文" },
  { value: "de", label_en: "German", label_zh: "德文" },
  { value: "pt", label_en: "Portuguese", label_zh: "葡萄牙文" },
];

const buttonClass =
  "inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-wait disabled:opacity-60 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle";

const primaryButtonClass =
  "inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-brand bg-tremor-brand px-3 text-sm font-medium text-tremor-brand-inverted transition hover:bg-teal-700 disabled:cursor-wait disabled:opacity-60";

const inputClass =
  "h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

const textareaClass =
  "min-h-[360px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-3 font-mono text-sm leading-6 text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

function formatDateTime(value?: string | null, lang: "en" | "zh" = "en") {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function errorMessage(error: unknown) {
  if (!error) return "";
  if (error instanceof Error) return error.message;
  return String(error);
}

function isActiveStatus(status?: string) {
  return status === "queued" || status === "sending";
}

function statusTone(status: string) {
  if (status === "sent") return "success" as const;
  if (status === "failed") return "danger" as const;
  if (status === "partial_failed") return "warning" as const;
  if (status === "queued" || status === "sending") return "info" as const;
  return "neutral" as const;
}

function ProgressBar({ campaign }: { campaign: NotificationCampaign }) {
  const percent = campaign.progress?.percent ?? 0;
  return (
    <div className="min-w-[160px]">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        <span>{percent}%</span>
        <span>
          {formatNumber(campaign.progress?.completed ?? 0)}/{formatNumber(campaign.progress?.total ?? 0)}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
        <div className="h-full bg-tremor-brand transition-all" style={{ width: `${Math.min(100, percent)}%` }} />
      </div>
    </div>
  );
}

export default function SubscriptionNotificationsPage() {
  const { lang } = useAppStore();
  const isZh = lang === "zh";
  const [subject, setSubject] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [sourceLocale, setSourceLocale] = useState(isZh ? "zh" : "en");
  const [targetLocales, setTargetLocales] = useState<string[]>([]);
  const [listCodes, setListCodes] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailLocale, setDetailLocale] = useState("en");

  const optionsQuery = useSubscriptionOptions();
  const campaignsQuery = useNotificationCampaigns(25, 0);
  const detailQuery = useNotificationCampaignDetail(selectedId, 100);
  const createNotification = useCreateNotificationCampaign();
  const startSend = useStartNotificationSend();

  const localeOptions = optionsQuery.data?.locales?.length ? optionsQuery.data.locales : defaultLocales;
  const listOptions = optionsQuery.data?.lists ?? [];
  const campaigns = campaignsQuery.data?.campaigns ?? [];
  const selectedCampaign = detailQuery.data?.campaign ?? campaigns.find((item) => item.id === selectedId) ?? null;
  const selectedContents = selectedCampaign?.contents ?? {};
  const detailContent = selectedContents[detailLocale] ?? selectedContents[selectedCampaign?.default_locale || "en"] ?? Object.values(selectedContents)[0];
  const activeCampaigns = campaigns.filter((campaign) => isActiveStatus(campaign.status));

  useEffect(() => {
    if (targetLocales.length === 0 && localeOptions.length > 0) {
      setTargetLocales(localeOptions.map((item) => item.value));
    }
  }, [localeOptions, targetLocales.length]);

  useEffect(() => {
    if (!selectedId && campaigns[0]?.id) {
      setSelectedId(campaigns[0].id);
    }
  }, [campaigns, selectedId]);

  useEffect(() => {
    if (!selectedCampaign) return;
    const locales = Object.keys(selectedCampaign.contents || {});
    setDetailLocale(locales.includes(selectedCampaign.default_locale) ? selectedCampaign.default_locale : locales[0] || "en");
  }, [selectedCampaign?.id, selectedCampaign?.default_locale]);

  const columns = useMemo<DataTableColumn<NotificationCampaign>[]>(
    () => [
      {
        key: "subject",
        header: isZh ? "通知" : "Notification",
        render: (row) => (
          <div className="min-w-[260px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {row.subject}
            </p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {formatDateTime(row.created_at, lang)}
            </p>
          </div>
        ),
      },
      {
        key: "status",
        header: isZh ? "状态" : "Status",
        render: (row) => <StatusBadge tone={statusTone(row.status)}>{row.status}</StatusBadge>,
      },
      {
        key: "progress",
        header: isZh ? "进度" : "Progress",
        render: (row) => <ProgressBar campaign={row} />,
      },
      {
        key: "audience",
        header: isZh ? "受众" : "Audience",
        render: (row) => (
          <span className="whitespace-nowrap text-xs text-tremor-content dark:text-dark-tremor-content">
            {formatNumber(row.audience_count)} / {(row.target_locales || []).join(", ")}
          </span>
        ),
      },
    ],
    [isZh, lang],
  );

  const deliveryColumns = useMemo<DataTableColumn<NotificationDelivery>[]>(
    () => [
      {
        key: "email",
        header: isZh ? "收件人" : "Recipient",
        render: (row) => (
          <div className="min-w-[180px]">
            <p className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {row.email_masked}
            </p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {row.locale} / {row.list_code}
            </p>
          </div>
        ),
      },
      {
        key: "status",
        header: isZh ? "状态" : "Status",
        render: (row) => <StatusBadge status={row.status}>{row.status}</StatusBadge>,
      },
      {
        key: "attempts",
        header: isZh ? "尝试" : "Attempts",
        render: (row) => <span className="text-xs">{row.attempts}</span>,
      },
      {
        key: "time",
        header: isZh ? "时间" : "Time",
        render: (row) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDateTime(row.sent_at || row.failed_at || row.queued_at, lang)}
          </span>
        ),
      },
    ],
    [isZh, lang],
  );

  const pageError =
    errorMessage(optionsQuery.error) ||
    errorMessage(campaignsQuery.error) ||
    errorMessage(detailQuery.error) ||
    errorMessage(createNotification.error) ||
    errorMessage(startSend.error);

  const canSubmit = markdown.trim().length > 0 && targetLocales.length > 0 && !createNotification.isPending;

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={isZh ? "Admin broadcast" : "Admin broadcast"}
        title={isZh ? "通知发送" : "Notifications"}
        description={
          isZh
            ? "用 Markdown 编写一次通知，自动翻译后按用户订阅语言发送。"
            : "Write one Markdown notice, translate it automatically, and send by subscriber locale."
        }
        actions={
          <button
            className={buttonClass}
            onClick={() => {
              void campaignsQuery.refetch();
              void detailQuery.refetch();
            }}
          >
            <RefreshCw className="h-4 w-4" />
            {isZh ? "刷新" : "Refresh"}
          </button>
        }
        meta={
          <>
            <StatusBadge tone={activeCampaigns.length > 0 ? "info" : "success"}>
              {activeCampaigns.length > 0
                ? `${activeCampaigns.length} ${isZh ? "个发送中" : "sending"}`
                : isZh ? "空闲" : "Idle"}
            </StatusBadge>
            <StatusBadge tone="neutral">
              {formatNumber(campaignsQuery.data?.pagination.total ?? 0)} {isZh ? "条历史" : "history"}
            </StatusBadge>
          </>
        }
      />

      {pageError ? (
        <div className="rounded-tremor-default border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          {pageError}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={isZh ? "历史通知" : "Campaigns"}
          value={formatNumber(campaignsQuery.data?.pagination.total ?? 0)}
          icon={<Bell className="h-4 w-4" />}
          tone="primary"
        />
        <MetricTile
          label={isZh ? "发送中" : "Sending"}
          value={formatNumber(activeCampaigns.length)}
          icon={<LoaderCircle className="h-4 w-4" />}
          tone={activeCampaigns.length > 0 ? "info" : "neutral"}
        />
        <MetricTile
          label={isZh ? "最近成功" : "Last sent"}
          value={formatNumber(campaigns[0]?.progress?.sent ?? 0)}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="success"
          hint={campaigns[0]?.subject || "-"}
        />
        <MetricTile
          label={isZh ? "目标语言" : "Target locales"}
          value={formatNumber(targetLocales.length)}
          icon={<Languages className="h-4 w-4" />}
          tone="info"
        />
      </div>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.72fr)]">
        <form
          className="space-y-4 rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background"
          onSubmit={(event) => {
            event.preventDefault();
            if (!canSubmit) return;
            createNotification.mutate(
              {
                subject: subject.trim() || undefined,
                markdown,
                source_locale: sourceLocale,
                target_locales: targetLocales,
                list_codes: listCodes,
                start_sending: true,
                batch_size: 20,
              },
              {
                onSuccess: (data) => {
                  if (data.campaign?.id) setSelectedId(data.campaign.id);
                  setMarkdown("");
                  setSubject("");
                },
              },
            );
          }}
        >
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_160px]">
            <input
              className={inputClass}
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              placeholder={isZh ? "邮件标题，可留空使用第一个 H1" : "Subject, or leave empty to use first H1"}
            />
            <select className={inputClass} value={sourceLocale} onChange={(event) => setSourceLocale(event.target.value)}>
              {localeOptions.map((item) => (
                <option key={item.value} value={item.value}>
                  {isZh ? item.label_zh : item.label_en}
                </option>
              ))}
            </select>
          </div>

          <textarea
            className={`${textareaClass} w-full`}
            value={markdown}
            onChange={(event) => setMarkdown(event.target.value)}
            placeholder={
              isZh
                ? "# 本次更新标题\n\n- 写入要通知用户的内容\n- 可以使用 Markdown 链接、列表和小标题"
                : "# Update title\n\n- Write the notice body here\n- Markdown links, lists, and headings are supported"
            }
          />

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <div className="mb-2 text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {isZh ? "语言" : "Locales"}
              </div>
              <div className="flex flex-wrap gap-2">
                {localeOptions.map((item) => {
                  const checked = targetLocales.includes(item.value);
                  return (
                    <label
                      key={item.value}
                      className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border px-3 text-sm text-tremor-content dark:border-dark-tremor-border dark:text-dark-tremor-content"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          setTargetLocales((current) =>
                            event.target.checked
                              ? Array.from(new Set([...current, item.value]))
                              : current.filter((locale) => locale !== item.value),
                          );
                        }}
                      />
                      <span>{isZh ? item.label_zh : item.label_en}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div>
              <div className="mb-2 text-xs font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {isZh ? "订阅列表" : "Lists"}
              </div>
              <div className="flex flex-wrap gap-2">
                {listOptions.map((item) => {
                  const checked = listCodes.includes(item.code);
                  return (
                    <label
                      key={item.code}
                      className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border px-3 text-sm text-tremor-content dark:border-dark-tremor-border dark:text-dark-tremor-content"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          setListCodes((current) =>
                            event.target.checked
                              ? Array.from(new Set([...current, item.code]))
                              : current.filter((code) => code !== item.code),
                          );
                        }}
                      />
                      <span>{isZh ? item.name_zh : item.name_en}</span>
                    </label>
                  );
                })}
                <StatusBadge tone={listCodes.length === 0 ? "success" : "neutral"}>
                  {listCodes.length === 0 ? (isZh ? "全部活跃用户" : "All active users") : `${listCodes.length} selected`}
                </StatusBadge>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            <button className={primaryButtonClass} type="submit" disabled={!canSubmit}>
              {createNotification.isPending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              {isZh ? "AI 翻译并发送" : "Translate & Send"}
            </button>
          </div>
        </form>

        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {isZh ? "Markdown 预览" : "Markdown Preview"}
            </h2>
            <StatusBadge>{sourceLocale}</StatusBadge>
          </div>
          <div className="min-h-[360px] rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-4 text-sm leading-7 text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content">
            {markdown.trim() ? (
              <ReactMarkdown>{markdown}</ReactMarkdown>
            ) : (
              <EmptyState title={isZh ? "输入 Markdown 后预览" : "Preview appears after Markdown is entered"} />
            )}
          </div>
        </div>
      </section>

      <DataTable
        columns={columns}
        rows={campaigns}
        getRowKey={(row) => row.id}
        selectedRowKey={selectedId}
        onRowClick={(row) => setSelectedId(row.id)}
        emptyState={
          <EmptyState
            title={
              campaignsQuery.isLoading
                ? isZh ? "加载中..." : "Loading..."
                : isZh ? "暂无通知历史" : "No notification history"
            }
          />
        }
      />

      {selectedCampaign ? (
        <section className="grid gap-4 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)]">
          <div className="rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {selectedCampaign.subject}
                </h2>
                <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {formatDateTime(selectedCampaign.created_at, lang)}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusBadge tone={statusTone(selectedCampaign.status)}>{selectedCampaign.status}</StatusBadge>
                {isActiveStatus(selectedCampaign.status) ? (
                  <button
                    className={buttonClass}
                    disabled={startSend.isPending}
                    onClick={() => startSend.mutate({ campaignId: selectedCampaign.id, batchSize: 20 })}
                  >
                    <Play className="h-4 w-4" />
                    {isZh ? "继续发送" : "Resume"}
                  </button>
                ) : null}
              </div>
            </div>
            <ProgressBar campaign={selectedCampaign} />
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              <MetricTile label={isZh ? "已发送" : "Sent"} value={formatNumber(selectedCampaign.progress.sent)} tone="success" />
              <MetricTile label={isZh ? "队列中" : "Queued"} value={formatNumber(selectedCampaign.progress.queued)} tone="info" />
              <MetricTile label={isZh ? "失败" : "Failed"} value={formatNumber(selectedCampaign.progress.failed)} tone="danger" />
            </div>

            <div className="mt-4">
              <select className={inputClass} value={detailLocale} onChange={(event) => setDetailLocale(event.target.value)}>
                {Object.keys(selectedContents).map((locale) => (
                  <option key={locale} value={locale}>
                    {locale}
                  </option>
                ))}
              </select>
              <div className="mt-3 rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-4 text-sm leading-7 text-tremor-content dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content">
                {detailContent ? (
                  <>
                    <h3 className="mb-3 text-lg font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {detailContent.subject}
                    </h3>
                    <ReactMarkdown>{detailContent.markdown}</ReactMarkdown>
                  </>
                ) : (
                  <EmptyState title={isZh ? "暂无内容" : "No content"} />
                )}
              </div>
            </div>
          </div>

          <div>
            <DataTable
              columns={deliveryColumns}
              rows={selectedCampaign.deliveries ?? []}
              getRowKey={(row) => row.id}
              emptyState={<EmptyState title={detailQuery.isLoading ? (isZh ? "加载中..." : "Loading...") : isZh ? "暂无投递记录" : "No deliveries"} />}
            />
          </div>
        </section>
      ) : null}
    </div>
  );
}
