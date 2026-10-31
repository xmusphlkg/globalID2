"use client";
import Link from "next/link";
import { useState } from "react";
import type { components } from "@/generated/api";
import { WorkspacePage } from "@/shared/ui";
import { CollectionActionResult, SourceFeedback, SourceLinks, SourceStages, SourceStatus, sourceDate, useCollectionAction, useSourceQuery, useSourceText } from "./shared";

const PROVIDER_LABELS: Record<string, string> = { crossref: "Crossref", pubmed: "PubMed", "publisher-rss": "Publisher RSS", "springer-nature": "Springer Nature", elsevier: "Elsevier", "biorxiv-api": "bioRxiv", "who-iris-oai": "WHO IRIS", "controlled-query": "Controlled discovery" };
type Dashboard = components["schemas"]["LiteratureDashboardOut"];
type ProviderRow = NonNullable<Dashboard["providers"]>[number];

function providerRows(runs: Array<{ source: string; status: string; error?: string | null }>, fromApi?: ProviderRow[]) {
 if (fromApi && fromApi.length) return fromApi;
 const present = new Set<string>();
 for (const run of runs) for (const token of run.source.split("+")) if (token in PROVIDER_LABELS) present.add(token);
 return Object.keys(PROVIDER_LABELS).filter((id) => present.has(id)).map((id) => {
  const match = runs.find((run) => run.source.split("+").includes(id));
  return { source_id: id, label: PROVIDER_LABELS[id], enabled: true, status: match?.status || "not_checked", fetched: null, last_error: match?.status === "failed" ? match.error : null, last_task_uuid: null, stages: [] };
 });
}

export function LiteratureSourcesView() {
 const { lang, text } = useSourceText(); const query = useSourceQuery<Dashboard>("/research-radar/dashboard"); const action = useCollectionAction(); const [since, setSince] = useState(""); const [source, setSource] = useState("");
 const runs = (query.data?.latest_runs ?? []).filter((run) => !["research-radar-autopilot", "evidence-gap-discovery"].includes(run.source)); const providers = [...new Set(runs.map((run) => run.source))]; const visibleRuns = runs.filter((run) => !source || run.source === source);
 const rows = providerRows(runs, query.data?.providers).filter((row) => !source || source === row.source_id || source.split("+").includes(row.source_id));
 function sinceValue() { return since ? new Date(since).toISOString() : undefined; }
 function submit() { const body: components["schemas"]["LiteratureRunCreate"] = { line: "literature", ...(sinceValue() ? { since: sinceValue() } : {}) }; void action.submit(body); }
 function submitOne(sourceId: string) { void action.submit({ line: "literature", ...(sinceValue() ? { since: sinceValue() } : {}), sources: [sourceId] }); }
 return <WorkspacePage eyebrow={text("collection")} title={text("source_literature")} description={text("source_literature_desc")} actions={<SourceLinks type="sync_literature" kind="literature" />}>
  <section className="cc-panel space-y-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">{text("source_sync")}</h2><p className="cc-muted mt-1">{text("source_since_hint")}</p><p className="cc-muted mt-1">{text("source_one_at_a_time")}</p></div><div className="text-right"><span className="cc-muted">{text("source_total_articles")}</span><p className="text-2xl font-semibold tabular-nums">{query.data?.total_articles ?? "—"}</p></div></div><form className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); submit(); }}><label className="text-sm">{text("source_since")}<input type="datetime-local" className="cc-input mt-1 block" value={since} onChange={(event) => setSince(event.target.value)} /></label><button className="cc-button cc-button-primary" disabled={action.busy}>{text(action.busy ? "source_submitting" : "source_sync")}</button></form></section>
  <CollectionActionResult action={action} /><SourceFeedback query={query} />
  <section className="cc-panel space-y-3"><h2 className="font-semibold">{text("source_providers")}</h2><p className="cc-muted">{text("source_provider_hint")}</p><p className="cc-muted">{text("source_no_provider_config")}</p>
   {query.data && !rows.length && <p className="cc-muted">{text("source_empty")}</p>}
   {rows.length > 0 && <div className="overflow-x-auto"><table className="cc-table w-full text-left"><thead><tr>{["source_provider", "source_enabled", "source_status", "source_fetched", "source_stages", "source_sync_one"].map((key) => <th scope="col" key={key}>{text(key as Parameters<typeof text>[0])}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.source_id}><td className="align-top font-semibold">{row.label}</td><td className="align-top">{text(row.enabled ? "source_enabled" : "source_disabled")}</td><td className="align-top"><SourceStatus status={row.status} />{row.last_error && <p className="mt-2 max-w-sm break-words text-xs text-rose-800">{row.last_error}</p>}</td><td className="align-top tabular-nums">{row.fetched ?? "—"}</td><td className="align-top"><SourceStages stages={row.stages} /></td><td className="align-top"><button type="button" className="cc-button" aria-label={`${text("source_sync_one")}: ${row.label}`} disabled={action.busy || !row.enabled} onClick={() => submitOne(row.source_id)}>{text("source_sync_one")}</button>{row.last_task_uuid && <Link className="mt-2 block text-sm text-orange-800 underline" href={`/collection/runs/${encodeURIComponent(row.last_task_uuid)}`}>{text("source_latest_run")}</Link>}</td></tr>)}</tbody></table></div>}
  </section>
  <section className="cc-panel space-y-3"><h2 className="font-semibold">{text("source_sync_records")}</h2><label className="inline-flex items-center gap-2 text-sm">{text("source_filter")}<select className="cc-input" value={source} onChange={(event) => setSource(event.target.value)}><option value="">{text("source_all")}</option>{providers.map((provider) => <option key={provider}>{provider}</option>)}</select></label>
   {query.data && !visibleRuns.length && <p className="cc-muted">{text("source_empty_history")}</p>}
   {visibleRuns.length > 0 && <div className="overflow-x-auto"><table className="cc-table w-full text-left"><thead><tr>{["source_provider", "source_status", "source_started", "source_window", "source_counts"].map((key) => <th scope="col" key={key}>{text(key as Parameters<typeof text>[0])}</th>)}</tr></thead><tbody>{visibleRuns.map((run) => <tr key={run.run_uuid}><td className="align-top"><strong>{run.source}</strong><p className="mt-1 max-w-48 break-all font-mono text-xs">{run.run_uuid}</p></td><td className="align-top"><SourceStatus status={run.status} />{run.error && <p className="mt-2 max-w-sm break-words text-xs text-rose-800">{run.error}</p>}</td><td className="align-top whitespace-nowrap">{sourceDate(run.started_at, lang)}</td><td className="align-top text-xs">{sourceDate(run.from_indexed_at, lang)}<br />→ {sourceDate(run.through_indexed_at, lang)}</td><td className="align-top"><details><summary className="cursor-pointer text-sm">{text("source_counts")}</summary><pre className="mt-2 max-w-sm overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(run.counts ?? {}, null, 2)}</pre></details></td></tr>)}</tbody></table></div>}
  </section>
  <section className="cc-panel space-y-3"><h2 className="font-semibold">{text("source_articles")}</h2>{query.data && !query.data.latest_articles.length && <p className="cc-muted">{text("source_empty")}</p>}<div className="divide-y divide-stone-200">{query.data?.latest_articles.map((article) => <article className="space-y-1 py-3" key={article.article_id}><h3 className="text-sm font-semibold">{article.title}</h3><p className="cc-muted">{article.journal || "—"} · {text("source_indexed")}: {sourceDate(article.indexed_at, lang)}</p>{article.doi && <a className="break-all text-xs text-orange-800 underline" href={`https://doi.org/${encodeURIComponent(article.doi)}`} target="_blank" rel="noreferrer">DOI: {article.doi}</a>}</article>)}</div></section>
 </WorkspacePage>;
}
