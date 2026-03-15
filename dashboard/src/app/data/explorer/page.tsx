"use client";

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { apiFetch } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { Database, ChevronLeft, ChevronRight } from "lucide-react";
import { Card, Title, Text, Badge, Flex } from "@tremor/react";

interface BrowseResult {
  table: string;
  total: number;
  limit: number;
  offset: number;
  data: Record<string, unknown>[];
}

export default function ExplorerPage() {
  const { lang } = useAppStore();
  const [table, setTable] = useState("diseases");
  const [page, setPage] = useState(0);
  const [hideEmptyColumns, setHideEmptyColumns] = useState(true);
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
    // Countries has many optional fields; default to a cleaner view.
    setHideEmptyColumns(table === "countries");
  }, [table]);

  const rawColumns = result?.data?.[0] ? Object.keys(result.data[0]) : [];
  const columns = hideEmptyColumns
    ? rawColumns.filter((col) => hasAnyValue(result?.data ?? [], col))
    : rawColumns;
  const totalPages = result ? Math.ceil(result.total / limit) : 0;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="blue" className="w-fit">{t(lang, "mod_database")}</Badge>
        <Title className="text-2xl">{t(lang, "explorer")}</Title>
        <Text>Browse raw database tables</Text>
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <select
            className="rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong shadow-tremor-input outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
            value={table}
            onChange={(e) => setTable(e.target.value)}
          >
            {tables?.tables.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          {result && (
            <Badge color="slate">
              {result.total.toLocaleString()} rows
            </Badge>
          )}
          <label className="inline-flex items-center gap-2 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            <input
              type="checkbox"
              checked={hideEmptyColumns}
              onChange={(e) => setHideEmptyColumns(e.target.checked)}
            />
            Hide empty columns
          </label>
        </div>
      </Card>

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3, 4, 5].map((i) => <div key={i} className="h-10 w-full animate-pulse rounded-tremor-default bg-tremor-background-muted dark:bg-dark-tremor-background-muted" />)}
        </div>
      ) : result && result.data.length > 0 ? (
        <>
          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-tremor-border dark:divide-dark-tremor-border">
                <thead>
                  <tr className="bg-tremor-background-subtle dark:bg-dark-tremor-background-subtle">
                    {columns.map((col) => (
                      <th key={col} className="whitespace-nowrap px-3 py-2.5 text-left text-xs font-semibold text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
                  {result.data.map((row, i) => (
                    <tr key={i} className="hover:bg-tremor-background-subtle/50 dark:hover:bg-dark-tremor-background-subtle/50">
                      {columns.map((col) => (
                        <td key={col} className="max-w-xs truncate px-3 py-2 text-xs text-tremor-content dark:text-dark-tremor-content">
                          {formatCell(row[col])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card>
            <Flex className="flex-wrap gap-3">
            <button
              className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-1.5 text-sm text-tremor-content transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Prev
            </button>
            <Text className="text-xs font-medium">
              Page {page + 1} of {totalPages || 1}
            </Text>
            <button
              className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-1.5 text-sm text-tremor-content transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle"
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
            </Flex>
          </Card>
        </>
      ) : (
          <Card>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Database className="h-12 w-12 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
            <Text className="mt-3">{t(lang, "no_data")}</Text>
          </div>
        </Card>
      )}
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") {
    const text = JSON.stringify(v);
    if (text === "{}" || text === "[]") return "—";
    return text.slice(0, 80);
  }
  if (typeof v === "string" && v.trim() === "") return "—";
  return String(v);
}

function hasAnyValue(rows: Record<string, unknown>[], col: string): boolean {
  for (const row of rows) {
    const v = row[col];
    if (v === null || v === undefined) continue;
    if (typeof v === "string" && v.trim() === "") continue;
    if (typeof v === "object") {
      const text = JSON.stringify(v);
      if (text === "{}" || text === "[]") continue;
      return true;
    }
    return true;
  }
  return false;
}
