import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface DataTableColumn<T> {
  key: string;
  header: ReactNode;
  className?: string;
  headerClassName?: string;
  render: (row: T) => ReactNode;
}

interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  getRowKey: (row: T) => string | number;
  selectedRowKey?: string | number | null;
  onRowClick?: (row: T) => void;
  emptyState?: ReactNode;
  className?: string;
}

export function DataTable<T,>({
  columns,
  rows,
  getRowKey,
  selectedRowKey,
  onRowClick,
  emptyState,
  className,
}: DataTableProps<T>) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-tremor-default border border-tremor-border bg-tremor-background shadow-[0_1px_2px_rgba(23,33,31,0.04)] dark:border-dark-tremor-border dark:bg-dark-tremor-background",
        className,
      )}
    >
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-tremor-border text-sm dark:divide-dark-tremor-border">
          <thead className="bg-tremor-background-subtle/80 dark:bg-dark-tremor-background-subtle">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={cn(
                    "whitespace-nowrap px-4 py-3 text-left text-[11px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle",
                    column.headerClassName,
                  )}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-tremor-border dark:divide-dark-tremor-border">
            {rows.length > 0 ? (
              rows.map((row) => {
                const rowKey = getRowKey(row);
                const selected = selectedRowKey != null && String(selectedRowKey) === String(rowKey);

                return (
                  <tr
                    key={rowKey}
                    tabIndex={onRowClick ? 0 : undefined}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    onKeyDown={
                      onRowClick
                        ? (event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              onRowClick(row);
                            }
                          }
                        : undefined
                    }
                    className={cn(
                      "transition-colors",
                      onRowClick ? "cursor-pointer hover:bg-tremor-background-subtle/80 dark:hover:bg-dark-tremor-background-subtle/70" : null,
                      selected ? "bg-teal-50/80 shadow-[inset_3px_0_0_#0f6b62] dark:bg-teal-950/20" : null,
                    )}
                  >
                    {columns.map((column) => (
                      <td
                        key={column.key}
                        className={cn(
                          "px-4 py-3.5 align-middle text-tremor-content dark:text-dark-tremor-content",
                          column.className,
                        )}
                      >
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={columns.length} className="px-4 py-10">
                  {emptyState}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
