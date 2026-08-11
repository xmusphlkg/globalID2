"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, ShieldOff } from "lucide-react";

import { EmptyState, StatusBadge, WorkspacePage } from "@/shared/ui";
import { apiFetch } from "@/lib/api";

type EventCandidate = {
  id: string;
  source: string;
  title: string;
  source_url: string;
  published_at?: string | null;
  disease_name?: string | null;
  geographies?: Array<{ name?: string }>;
  confidence: string;
};

export default function EventsView() {
  const [items, setItems] = useState<EventCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    setError(null);
    try { setItems(await apiFetch<EventCandidate[]>("/overview/events")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to load candidates"); }
  };
  useEffect(() => { void load(); }, []);
  const decide = async (id: string, action: "publish" | "suppress") => {
    setBusy(true);
    try {
      await apiFetch(`/overview/events/${id}`, { method: "PATCH", body: JSON.stringify({ action, actor: "dashboard" }) });
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save decision"); }
    finally { setBusy(false); }
  };
  const rebuild = async () => {
    setBusy(true);
    try { await apiFetch("/overview/events/rebuild", { method: "POST" }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to refresh event signals"); }
    finally { setBusy(false); }
  };

  return (
    <WorkspacePage
      eyebrow="Overview"
      title="Events & Signals"
      description="Review ambiguous official notices before they appear on the public situation page."
      actions={<button disabled={busy} onClick={() => void rebuild()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-3 text-sm font-semibold text-[#374151] hover:bg-[#F7F7F5] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />Refresh snapshot</button>}
    >
      <div className="rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">External event ingestion never auto-publishes ambiguous disease or geography mappings.</div>
      {error ? <p className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</p> : null}
      <section className="app-panel overflow-hidden">
        {items.length ? items.map((item) => (
          <article key={item.id} className="flex flex-col gap-3 border-b border-[#E5E5E2] p-4 last:border-b-0 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <div className="mb-1.5 flex flex-wrap items-center gap-2"><StatusBadge tone="warning">Candidate</StatusBadge><StatusBadge tone="info">{item.source}</StatusBadge><span className="text-xs text-[#6B7280]">{item.published_at || "Undated"} · {item.confidence}</span></div>
              <a href={item.source_url} target="_blank" rel="noreferrer" className="font-semibold text-[#1D1D1F] hover:text-[#C2410C] hover:underline">{item.title}</a>
              <div className="mt-1 text-sm text-[#6B7280]">{item.disease_name || "Disease mapping required"}{item.geographies?.length ? ` · ${item.geographies.map((geography) => geography.name).join(", ")}` : " · Geography mapping required"}</div>
            </div>
            <div className="flex shrink-0 gap-2"><button disabled={busy} onClick={() => void decide(item.id, "publish")} className="inline-flex h-9 items-center gap-1.5 rounded-md bg-emerald-700 px-3 text-sm font-semibold text-white disabled:opacity-50"><CheckCircle2 className="h-4 w-4" />Publish</button><button disabled={busy} onClick={() => void decide(item.id, "suppress")} className="inline-flex h-9 items-center gap-1.5 rounded-md border border-[#D9D9D6] px-3 text-sm font-semibold text-[#374151] disabled:opacity-50"><ShieldOff className="h-4 w-4" />Suppress</button></div>
          </article>
        )) : <EmptyState title="No candidates require review" description="New ambiguous notices will appear here." className="min-h-64" />}
      </section>
    </WorkspacePage>
  );
}
