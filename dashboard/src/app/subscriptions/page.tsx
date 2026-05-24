"use client";

import { useMemo, useState } from "react";
import { Clock, Database, Mail, RefreshCw, Search, ShieldCheck, Users, Wrench } from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  type SubscriptionRecord,
  useRunSubscriptionMaintenance,
  useSubscriptionConfig,
  useSubscriptionOptions,
  useSubscriptionRecords,
  useSubscriptionStats,
  useSyncSubscriptionOptions,
} from "@/lib/hooks/useSubscriptions";
import { formatNumber } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const statusOptions = ["", "pending", "active", "unsubscribed", "expired", "paused"];

function countTotal(counts?: Record<string, number>) {
  return Object.values(counts || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function formatDateTime(value?: string | null, lang: "en" | "zh" = "en") {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function filtersSummary(row: SubscriptionRecord) {
  const parts = Object.entries(row.filters || {})
    .filter(([, values]) => values.length > 0)
    .map(([key, values]) => `${key}: ${values.length}`);
  return parts.length ? parts.join(" / ") : "-";
}

function errorMessage(error: unknown) {
  if (!error) return "";
  if (error instanceof Error) return error.message;
  return String(error);
}

const buttonClass =
  "inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-wait disabled:opacity-60 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle";

const inputClass =
  "h-10 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

export default function SubscriptionsPage() {
  const { lang } = useAppStore();
  const isZh = lang === "zh";
  const [status, setStatus] = useState("");
  const [listCode, setListCode] = useState("");
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(50);

  const configQuery = useSubscriptionConfig();
  const statsQuery = useSubscriptionStats();
  const optionsQuery = useSubscriptionOptions();
  const recordFilters = useMemo(
    () => ({ status, listCode, q: search, limit, offset: 0 }),
    [status, listCode, search, limit],
  );
  const recordsQuery = useSubscriptionRecords(recordFilters);
  const maintenance = useRunSubscriptionMaintenance();
  const syncOptions = useSyncSubscriptionOptions();

  const stats = statsQuery.data;
  const options = optionsQuery.data;
  const records = recordsQuery.data?.subscriptions ?? [];
  const totalRecords = recordsQuery.data?.pagination.total ?? 0;
  const configured = Boolean(configQuery.data?.configured);

  const columns = useMemo<DataTableColumn<SubscriptionRecord>[]>(
    () => [
      {
        key: "email",
        header: isZh ? "邮箱" : "Email",
        render: (row) => (
          <div className="min-w-[220px]">
            <p className="truncate font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {row.email}
            </p>
            <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {row.source || "-"}
            </p>
          </div>
        ),
      },
      {
        key: "status",
        header: isZh ? "订阅状态" : "Status",
        render: (row) => (
          <div className="flex flex-wrap gap-1.5">
            <StatusBadge status={row.status}>{row.status}</StatusBadge>
            <StatusBadge status={row.contact_status}>{row.contact_status}</StatusBadge>
          </div>
        ),
      },
      {
        key: "list",
        header: isZh ? "列表" : "List",
        render: (row) => (
          <div className="min-w-[150px]">
            <p className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {isZh ? row.list_name_zh : row.list_name}
            </p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {row.list_code} / {row.frequency}
            </p>
          </div>
        ),
      },
      {
        key: "filters",
        header: isZh ? "筛选" : "Filters",
        render: (row) => (
          <span className="whitespace-nowrap text-xs text-tremor-content dark:text-dark-tremor-content">
            {filtersSummary(row)}
          </span>
        ),
      },
      {
        key: "locale",
        header: isZh ? "语言/时区" : "Locale",
        render: (row) => (
          <span className="whitespace-nowrap text-xs text-tremor-content dark:text-dark-tremor-content">
            {row.locale} / {row.timezone || "UTC"}
          </span>
        ),
      },
      {
        key: "created",
        header: isZh ? "创建时间" : "Created",
        render: (row) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDateTime(row.created_at, lang)}
          </span>
        ),
      },
    ],
    [isZh, lang],
  );

  const pageError =
    errorMessage(configQuery.error) ||
    errorMessage(statsQuery.error) ||
    errorMessage(optionsQuery.error) ||
    errorMessage(recordsQuery.error) ||
    errorMessage(maintenance.error) ||
    errorMessage(syncOptions.error);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={isZh ? "Cloudflare D1" : "Cloudflare D1"}
        title={isZh ? "订阅管理" : "Subscription Management"}
        description={
          isZh
            ? "查看订阅状态、邮件确认状态和 D1 选项同步情况。"
            : "Monitor subscription states, email confirmation status, and D1 option sync."
        }
        actions={
          <>
            <button
              className={buttonClass}
              onClick={() => {
                void configQuery.refetch();
                void statsQuery.refetch();
                void optionsQuery.refetch();
                void recordsQuery.refetch();
              }}
            >
              <RefreshCw className="h-4 w-4" />
              {isZh ? "刷新" : "Refresh"}
            </button>
            <button
              className={buttonClass}
              disabled={syncOptions.isPending}
              onClick={() => syncOptions.mutate()}
            >
              <Database className="h-4 w-4" />
              {isZh ? "同步选项" : "Sync Options"}
            </button>
            <button
              className={buttonClass}
              disabled={maintenance.isPending}
              onClick={() => maintenance.mutate()}
            >
              <Wrench className="h-4 w-4" />
              {isZh ? "清理 pending" : "Run Maintenance"}
            </button>
          </>
        }
        meta={
          <>
            <StatusBadge tone={configured ? "success" : "warning"}>
              {configured ? (isZh ? "Worker 已配置" : "Worker configured") : isZh ? "未配置" : "Not configured"}
            </StatusBadge>
            <StatusBadge>{configQuery.data?.d1_database_name || "D1"}</StatusBadge>
            <StatusBadge>{configQuery.data?.sync_options_on_release || "auto"}</StatusBadge>
          </>
        }
      />

      {pageError ? (
        <div className="rounded-tremor-default border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          {pageError}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricTile
          label={isZh ? "正式订阅" : "Active subscriptions"}
          value={formatNumber(stats?.subscriptions?.active ?? 0)}
          icon={<Users className="h-4 w-4" />}
          tone="success"
          hint={`${formatNumber(countTotal(stats?.subscriptions))} ${isZh ? "总订阅" : "total"}`}
        />
        <MetricTile
          label={isZh ? "待确认" : "Pending"}
          value={formatNumber(stats?.subscriptions?.pending ?? 0)}
          icon={<Clock className="h-4 w-4" />}
          tone="warning"
          hint={`${stats?.pending_expiry_days ?? "-"} ${isZh ? "天过期" : "days expiry"}`}
        />
        <MetricTile
          label={isZh ? "可用联系人" : "Active contacts"}
          value={formatNumber(stats?.contacts?.active ?? 0)}
          icon={<Mail className="h-4 w-4" />}
          tone="primary"
          hint={`${formatNumber(countTotal(stats?.contacts))} ${isZh ? "总联系人" : "contacts"}`}
        />
        <MetricTile
          label={isZh ? "近7天邮件" : "7-day email"}
          value={formatNumber(countTotal(stats?.deliveries_last_7_days))}
          icon={<ShieldCheck className="h-4 w-4" />}
          tone="info"
          hint={`${isZh ? "成功" : "sent"} ${formatNumber(stats?.deliveries_last_7_days?.sent ?? 0)}`}
        />
        <MetricTile
          label={isZh ? "过期待清理" : "Stale pending"}
          value={formatNumber(stats?.stale_pending_subscriptions ?? 0)}
          icon={<Wrench className="h-4 w-4" />}
          tone={(stats?.stale_pending_subscriptions ?? 0) > 0 ? "warning" : "neutral"}
          hint={isZh ? "maintenance 会标记 expired" : "maintenance marks expired"}
        />
      </div>

      <section className="rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {isZh ? "订阅选项来源" : "Subscription Options"}
            </h2>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {options
                ? `${options.lists.length} lists / ${options.filters.country.length} countries / ${options.filters.disease.length} diseases`
                : "-"}
            </p>
          </div>
          {syncOptions.data ? <StatusBadge tone="success">{isZh ? "已同步" : "Synced"}</StatusBadge> : null}
          {maintenance.data ? <StatusBadge tone="success">{isZh ? "维护完成" : "Maintenance complete"}</StatusBadge> : null}
        </div>
      </section>

      <FilterToolbar>
        <select className={inputClass} value={status} onChange={(event) => setStatus(event.target.value)}>
          {statusOptions.map((item) => (
            <option key={item || "all"} value={item}>
              {item || (isZh ? "全部状态" : "All statuses")}
            </option>
          ))}
        </select>

        <select className={inputClass} value={listCode} onChange={(event) => setListCode(event.target.value)}>
          <option value="">{isZh ? "全部列表" : "All lists"}</option>
          {options?.lists.map((item) => (
            <option key={item.code} value={item.code}>
              {isZh ? item.name_zh : item.name_en}
            </option>
          ))}
        </select>

        <label className="relative min-w-[240px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tremor-content-subtle" />
          <input
            className={`${inputClass} w-full pl-9`}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={isZh ? "搜索邮箱" : "Search email"}
          />
        </label>

        <select className={inputClass} value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
          {[25, 50, 100, 250].map((value) => (
            <option key={value} value={value}>
              {value} rows
            </option>
          ))}
        </select>

        <StatusBadge tone="neutral">
          {formatNumber(totalRecords)} {isZh ? "条" : "records"}
        </StatusBadge>
      </FilterToolbar>

      <DataTable
        columns={columns}
        rows={records}
        getRowKey={(row) => row.subscription_id}
        emptyState={
          <EmptyState
            title={
              recordsQuery.isLoading
                ? isZh ? "加载中..." : "Loading..."
                : isZh ? "暂无订阅记录" : "No subscription records"
            }
          />
        }
      />
    </div>
  );
}
