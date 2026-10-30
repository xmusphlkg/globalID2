"use client";
import { useState } from "react";
import type { SituationSourceAdapter } from "@/lib/hooks/useSources";
import type { components } from "@/generated/api";
import { WorkspacePage } from "@/shared/ui";
import { CollectionActionResult, SourceFeedback, SourceLinks, SourceStatus, sourceDate, useCollectionAction, useSourceQuery, useSourceText } from "./shared";

export function SituationSourcesView() {
 const { lang, text } = useSourceText();
 // OpenAPI exposes array<dict> here; reuse the existing adapter contract until
 // the API publishes a named source/history schema.
 const sources = useSourceQuery<SituationSourceAdapter[]>("/situation/sources");
 const action = useCollectionAction(); const [search, setSearch] = useState(""); const [failedOnly, setFailedOnly] = useState(false);
 const rows = [...(sources.data ?? [])].filter((source) => (!failedOnly || source.status === "failed") && `${source.source_id} ${source.label_en} ${source.label_zh}`.toLowerCase().includes(search.toLowerCase())).sort((a, b) => Number(b.status === "failed") - Number(a.status === "failed"));
 const submit = () => { const body: Pick<components["schemas"]["SituationRunCreate"], "line" | "mode"> = { line: "situation", mode: "metadata_only" }; void action.submit(body); };
 return <WorkspacePage eyebrow={text("collection")} title={text("source_situation")} description={text("source_situation_desc")} actions={<SourceLinks type="refresh_situation_sources" kind="situation" />}>
  <section className="cc-panel flex flex-wrap items-center justify-between gap-4"><div><h2 className="font-semibold">{text("source_collect_situation")}</h2><p className="cc-muted mt-1">{text("source_collect_hint")}</p><p className="cc-muted mt-1">{text("source_scope_notice")}</p></div><button className="cc-button cc-button-primary" disabled={action.busy || sources.isLoading || !sources.data?.length} onClick={submit}>{text(action.busy ? "source_submitting" : "source_collect_situation")}</button></section>
  <CollectionActionResult action={action} /><SourceFeedback query={sources} />
  <div className="cc-panel flex flex-wrap items-end gap-3"><label className="flex-1 text-sm">{text("source_search")}<input className="cc-input mt-1 block w-full" value={search} onChange={(event) => setSearch(event.target.value)} /></label><label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" checked={failedOnly} onChange={(event) => setFailedOnly(event.target.checked)} />{text("source_failed_only")}</label></div>
  {sources.data && !rows.length && <p className="cc-panel cc-muted">{text("source_empty")}</p>}
  <div className="grid items-start gap-4 xl:grid-cols-2">{rows.map((source) => <section key={source.source_id} className="cc-panel space-y-3">
   <div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold">{(lang === "zh" ? source.label_zh : source.label_en) || source.source_id}</h2><p className="cc-muted">{source.agency_en} · {source.cadence?.[lang]}</p></div><SourceStatus status={source.status || "not_checked"} /></div>
   <dl className="grid grid-cols-2 gap-3 text-sm"><div><dt className="cc-muted">{text("source_stored")}</dt><dd className="mt-1 font-semibold">{source.stored_notices}</dd></div><div><dt className="cc-muted">{text("source_checked")}</dt><dd>{sourceDate(source.last_checked_at, lang)}</dd></div><div><dt className="cc-muted">{text("source_success")}</dt><dd>{sourceDate(source.last_success_at, lang)}</dd></div></dl>
   {source.last_error && <p className="break-words text-sm text-rose-800">{source.last_error}</p>}
   {(source.homepage || source.endpoint) && <a className="inline-block text-sm text-orange-800 underline" href={source.homepage || source.endpoint || ""} target="_blank" rel="noreferrer">{text("source_official")}</a>}
   <details><summary className="cursor-pointer text-sm font-semibold">{text("source_history")} · {source.history?.length ?? 0}</summary><div className="mt-3 space-y-3">{source.history?.map((run) => <article className="space-y-2 border-t border-stone-200 pt-3 text-xs" key={`${run.run_id}:${run.started_at}`}><div className="flex flex-wrap justify-between gap-2"><time>{sourceDate(run.started_at, lang)}</time><SourceStatus status={run.status} /></div><p>{text("source_fetched")}: {run.fetched_count} · {text("source_new")}: {run.new_count} · {text("source_updated")}: {run.updated_count}</p><p className="break-all font-mono">{run.run_id}</p>{run.error && <p className="break-words text-rose-800">{run.error}</p>}</article>)}{!source.history?.length && <p className="cc-muted">{text("source_empty_history")}</p>}</div></details>
  </section>)}</div>
 </WorkspacePage>;
}
