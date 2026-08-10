"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, ShieldOff } from "lucide-react";

import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { apiFetch } from "@/lib/api";

type EventCandidate = {
  id: number; source: string; title: string; source_url: string; published_at?: string | null;
  disease_name?: string | null; geographies?: Array<{ name?: string }>; confidence: string;
};

export default function SituationReviewPage() {
  const [items, setItems] = useState<EventCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    setError(null);
    try { setItems(await apiFetch<EventCandidate[]>("/situation/candidates")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to load candidates"); }
  };
  useEffect(() => { void load(); }, []);
  const decide = async (id: number, action: "publish" | "suppress") => {
    setBusy(true);
    try {
      await apiFetch(`/situation/events/${id}`, { method: "PATCH", body: JSON.stringify({ action, actor: "dashboard" }) });
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save decision"); }
    finally { setBusy(false); }
  };
  const rebuild = async () => {
    setBusy(true);
    try { await apiFetch("/situation/rebuild", { method: "POST" }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to refresh Situation Room"); }
    finally { setBusy(false); }
  };
  return <main className="space-y-6 p-6 lg:p-8">
    <PageHeader title="Situation Room Review" description="Review ambiguous official event notices before they appear on the public situation page." />
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <div className="text-sm text-tremor-content">External event ingestion never auto-publishes ambiguous disease or geography mappings.</div>
      <button disabled={busy} onClick={() => void rebuild()} className="inline-flex items-center gap-2 rounded-tremor-default border border-tremor-border px-3 py-2 text-sm font-semibold disabled:opacity-50"><RefreshCw className="h-4 w-4" />Refresh snapshot</button>
    </div>
    {error && <p className="rounded-tremor-default bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    <section className="overflow-hidden rounded-tremor-default border border-tremor-border dark:border-dark-tremor-border">
      {items.length ? items.map(item => <article key={item.id} className="flex flex-col gap-3 border-b border-tremor-border p-4 last:border-b-0 dark:border-dark-tremor-border lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0"><div className="mb-1 flex flex-wrap items-center gap-2"><StatusBadge tone="warning">candidate</StatusBadge><StatusBadge tone="info">{item.source}</StatusBadge><span className="text-xs text-tremor-content-subtle">{item.published_at || "Undated"} · {item.confidence}</span></div><a href={item.source_url} target="_blank" rel="noreferrer" className="font-semibold text-tremor-content-strong hover:underline">{item.title}</a><div className="mt-1 text-sm text-tremor-content-subtle">{item.disease_name || "Disease mapping required"}{item.geographies?.length ? ` · ${item.geographies.map(g => g.name).join(", ")}` : " · Geography mapping required"}</div></div>
        <div className="flex shrink-0 gap-2"><button disabled={busy} onClick={() => void decide(item.id, "publish")} className="inline-flex items-center gap-1 rounded-tremor-default bg-emerald-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"><CheckCircle2 className="h-4 w-4" />Publish</button><button disabled={busy} onClick={() => void decide(item.id, "suppress")} className="inline-flex items-center gap-1 rounded-tremor-default border border-tremor-border px-3 py-2 text-sm font-semibold disabled:opacity-50"><ShieldOff className="h-4 w-4" />Suppress</button></div>
      </article>) : <div className="p-8 text-center text-sm text-tremor-content-subtle">No event candidates require review.</div>}
    </section>
  </main>;
}
