import type { ReactNode } from "react";

export function FormField({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold text-[#374151]">{label}</span>
      {children}
      {error ? <span className="block text-xs text-rose-700">{error}</span> : hint ? <span className="block text-xs text-[#6B7280]">{hint}</span> : null}
    </label>
  );
}
