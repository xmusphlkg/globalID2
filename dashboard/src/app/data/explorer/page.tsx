"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Database, Eye } from "lucide-react";

import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DetailDrawer } from "@/components/ui/DetailDrawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { apiFetch } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";

interface BrowseResult {
  table: string;
  total: number;
  limit: number;
  offset: number;
  data: Record<string, unknown>[];
}

interface BrowseRow {
  id: number;
  values: Record<string, unknown>;
}

export default function ExplorerPage() {
  const { lang } = useAppStore();
  const [table, setTable] = useState("diseases");
  const [page, setPage] = useState(0);
  const [hideEmptyColumns, setHideEmptyColumns] = useState(true);
  const [selectedRow, setSelectedRow] = useState<BrowseRow | null>(null);
  const limit = 100;

  const { data: tables } = useQuery<{ tables: string[] }>({
    queryKey: ["explorer", "tables"],
    queryFn: () => apiFetch("/explorer/tables"),
    staleTime: Infinity,
  });

  const { data: result, isLoading } = useQuery<BrowseResult>({
    queryKey: ["explorer", "browse", table, page],
    queryFn: () =>
      apiFetch(
        `/explorer/browse?table=${encodeURIComponent(table)}&limit=${limit}&offset=${page * limit}`,
      ),
    staleTime: 60 * 1000,
  });

  useEffect(() => setPage(0), [table]);
  useEffect(() => {
    setHideEmptyColumns(table === "countries");
  }, [table]);
  useEffect(() => {
    setSelectedRow(null);
  }, [page, table]);

  const rows = useMemo(
    () => (result?.data ?? []).map((values, index) => ({ id: page * limit + index + 1, values })),
    [page, result],
  );

  const rawColumns = result?.data?.[0] ? Object.keys(result.data[0]) : [];
  const visibleColumns = hideEmptyColumns
    ? rawColumns.filter((column) => hasAnyValue(result?.data ?? [], column))
    : rawColumns;
  const totalPages = result ? Math.ceil(result.total / limit) : 0;

  const tableColumns = useMemo<DataTableColumn<BrowseRow>[]>(
    () => [
      {
        key: "__row",
        header: "#",
        render: (row) => (
          <span className="font-mono text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {row.id}
          </span>
        ),
      },
      ...visibleColumns.slice(0, 10).map((column) => ({
        key: column,
        header: column,
        render: (row: BrowseRow) => (
          <span className="block max-w-[260px] truncate text-xs text-tremor-content dark:text-dark-tremor-content">
            {formatCell(row.values[column])}
          </span>
        ),
      })),
      {
        key: "__action",
        header: "",
        className: "text-right",
        render: () => (
          <span className="inline-flex h-8 items-center gap-2 rounded-tremor-default border border-tremor-border px-2.5 text-xs font-medium text-tremor-content-subtle dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle">
            <Eye className="h-3.5 w-3.5" />
            {lang === "zh" ? "查看" : "View"}
          </span>
        ),
      },
    ],
    [lang, visibleColumns],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={t(lang, "explorer")}
        description={lang === "zh" ? "浏览原始数据库表，快速检查字段和值。" : "Browse raw database tables and inspect records quickly."}
        meta={
          <>
            <StatusBadge tone="primary">{table}</StatusBadge>
            {result ? <StatusBadge>{result.total.toLocaleString()} rows</StatusBadge> : null}
            <StatusBadge>{visibleColumns.length} columns</StatusBadge>
          </>
        }
      />

      <FilterToolbar>
        <select
          className="h-10 min-w-[220px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          value={table}
          onChange={(event) => setTable(event.target.value)}
        >
          {tables?.tables.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <label className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong">
          <input
            type="checkbox"
            checked={hideEmptyColumns}
            onChange={(event) => setHideEmptyColumns(event.target.checked)}
            className="h-4 w-4 rounded border-tremor-border text-tremor-brand"
          />
          {lang === "zh" ? "隐藏空字段" : "Hide empty columns"}
        </label>
      </FilterToolbar>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map((item) => (
            <div
              key={item}
              className="h-12 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted"
            />
          ))}
        </div>
      ) : (
        <DataTable
          columns={tableColumns}
          rows={rows}
          getRowKey={(row) => row.id}
          selectedRowKey={selectedRow?.id}
          onRowClick={setSelectedRow}
          emptyState={<EmptyState icon={<Database className="h-10 w-10" />} title={t(lang, "no_data")} />}
        />
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
        <button
          className="inline-flex h-9 items-center gap-1 rounded-tremor-default border border-tremor-border px-3 text-sm text-tremor-content transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle"
          disabled={page === 0}
          onClick={() => setPage((currentPage) => currentPage - 1)}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          Prev
        </button>
        <span className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          Page {page + 1} of {totalPages || 1}
        </span>
        <button
          className="inline-flex h-9 items-center gap-1 rounded-tremor-default border border-tremor-border px-3 text-sm text-tremor-content transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle"
          disabled={page + 1 >= totalPages}
          onClick={() => setPage((currentPage) => currentPage + 1)}
        >
          Next
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>

      <DetailDrawer
        open={Boolean(selectedRow)}
        title={selectedRow ? `${table} #${selectedRow.id}` : table}
        subtitle={selectedRow ? `${visibleColumns.length} visible columns` : undefined}
        onClose={() => setSelectedRow(null)}
      >
        {selectedRow ? (
          <div className="divide-y divide-tremor-border overflow-hidden rounded-tremor-default border border-tremor-border dark:divide-dark-tremor-border dark:border-dark-tremor-border">
            {Object.entries(selectedRow.values).map(([key, value]) => (
              <div key={key} className="grid gap-2 px-3 py-3 sm:grid-cols-[180px_minmax(0,1fr)]">
                <div className="font-mono text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {key}
                </div>
                <pre className="whitespace-pre-wrap break-words text-xs text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {formatCellFull(value)}
                </pre>
              </div>
            ))}
          </div>
        ) : null}
      </DetailDrawer>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") {
    const text = JSON.stringify(value);
    if (text === "{}" || text === "[]") return "-";
    return text.slice(0, 80);
  }
  if (typeof value === "string" && value.trim() === "") return "-";
  return String(value);
}

function formatCellFull(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") {
    const text = JSON.stringify(value, null, 2);
    if (text === "{}" || text === "[]") return "-";
    return text;
  }
  if (typeof value === "string" && value.trim() === "") return "-";
  return String(value);
}

function hasAnyValue(rows: Record<string, unknown>[], column: string): boolean {
  for (const row of rows) {
    const value = row[column];
    if (value === null || value === undefined) continue;
    if (typeof value === "string" && value.trim() === "") continue;
    if (typeof value === "object") {
      const text = JSON.stringify(value);
      if (text === "{}" || text === "[]") continue;
      return true;
    }
    return true;
  }
  return false;
}
