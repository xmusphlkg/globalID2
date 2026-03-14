"use client";

import type { ReactNode } from "react";
import { cn, formatNumber } from "@/lib/utils";
import { Card } from "@tremor/react";

interface KPICardProps {
  title: string;
  value: string | number;
  icon?: ReactNode;
  accent?: "primary" | "success" | "warning" | "error" | "info";
  className?: string;
}

const accentMap = {
  primary: "bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-500",
  success: "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-500",
  warning: "bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-500",
  error: "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-500",
  info: "bg-sky-100 text-sky-600 dark:bg-sky-900/30 dark:text-sky-500",
};

export function KPICard({ title, value, icon, accent = "primary", className }: KPICardProps) {
  const display = typeof value === "number" ? formatNumber(value) : value;
  
  return (
    <Card className={cn("flex items-center gap-4 py-4 px-5", className)}>
      {icon && (
        <div
          className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-tremor-default", accentMap[accent])}
        >
          {icon}
        </div>
      )}
      <div className="min-w-0">
        <p className="text-tremor-default font-medium text-tremor-content dark:text-dark-tremor-content">
          {title}
        </p>
        <p className="mt-1 text-tremor-metric font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong truncate">
          {display}
        </p>
      </div>
    </Card>
  );
}
