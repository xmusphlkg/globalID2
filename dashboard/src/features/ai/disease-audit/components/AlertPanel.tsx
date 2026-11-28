"use client";

import { AlertTriangle } from "lucide-react";

interface AlertPanelProps {
  title: string;
  message: string;
  details?: string | null;
  tone: "warning" | "danger";
}

export function AlertPanel({ title, message, details, tone }: AlertPanelProps) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200"
      : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200";

  return (
    <section className={`rounded-tremor-default border p-4 ${toneClass}`}>
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-1 whitespace-pre-wrap text-sm">{message}</p>
          {details ? (
            <pre className="mt-2 max-h-48 overflow-auto rounded-tremor-default bg-white/80 px-3 py-2 text-xs dark:bg-black/10">
              {details}
            </pre>
          ) : null}
        </div>
      </div>
    </section>
  );
}
