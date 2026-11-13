"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/generated/api";
import { ApiError, apiFetch } from "@/lib/api";
import { StatusBadge } from "@/shared/ui";
import { modelPublishDate, modelPublishLabel } from "./models";
import { publicationPlanningMessages } from "./publication-planning-messages";

export type PublicationCapabilities = components["schemas"]["PublicationCapabilitiesOut"];
type PublicationPlan = components["schemas"]["PublicationPlanOut"];
type PublicationUsage = components["schemas"]["PublicationUsageOut"];
type PlanMode = "auto" | "content" | "theme" | "template" | "full";
type PublicationRun = { task_uuid?: string; status?: string; reason?: string };
type PublicationReceipt = { authoritative_committed?: boolean | null; public_verified?: boolean | null; database_confirmed?: boolean | null;
  content_render_count?: number | null; html_assembly_count?: number | null; template_compile_count?: number | null;
  uploaded_objects?: number | null; uploaded_bytes?: number | null; duration_seconds?: number | null; version?: string };
type ThemeHistory = { items: Array<{ plan_id: string; created_at: string; version: string; public_verified: boolean; compatibility: string }> };
type Language = "en" | "zh";

// Optional endpoints support rolling upgrades. Only an explicit 404/405 means
// the server is old; other failures are unavailable, never a healthy fallback.
async function optionalEndpoint<T>(path: string): Promise<T | null> {
  try { return await apiFetch<T>(path); }
  catch (error) {
    if (error instanceof ApiError && [404, 405].includes(error.status)) return null;
    throw error;
  }
}

export function usePublicationCapabilities() {
  return useQuery({
    queryKey: ["/publish/capabilities"],
    queryFn: () => optionalEndpoint<PublicationCapabilities>("/publish/capabilities"),
    staleTime: 60_000, retry: false,
  });
}

function planProblem(error: unknown, lang: Language) {
  const text = publicationPlanningMessages[lang];
  if (error instanceof ApiError) {
    if ([409, 410].includes(error.status)) return text.recheck;
    if ([401, 403].includes(error.status)) return text.denied;
  }
  return text.planFailed;
}

export function PublicationPlanning({ capabilities, unavailable, loading, lang }: {
  capabilities: PublicationCapabilities | null | undefined; unavailable: boolean; loading: boolean; lang: Language;
}) {
  const text = publicationPlanningMessages[lang];
  const client = useQueryClient();
  const [plan, setPlan] = useState<PublicationPlan | null>(null);
  const [mode, setMode] = useState<PlanMode>("auto");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState<"check" | "run" | "rollback" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<PublicationRun | null>(null);
  const [invalidated, setInvalidated] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const lock = useRef(false);
  const available = capabilities?.schema_version === 1 && capabilities.plan_preview === true;
  const modes = capabilities?.modes.filter(item => ["content", "theme", "template", "full"].includes(item.id)) ?? [];
  const channels = capabilities?.channels.filter(item => item.enabled && item.supported_modes.includes("content")) ?? [];
  const selectedChannel = channels.some(item => item.id === channel) ? channel : channels[0]?.id;
  const run = useQuery({
    queryKey: ["/collection/runs", result?.task_uuid],
    queryFn: () => apiFetch<components["schemas"]["RunOut"]>(`/collection/runs/${encodeURIComponent(result!.task_uuid!)}`),
    enabled: !!result?.task_uuid,
    refetchInterval: query => ["completed", "failed", "cancelled"].includes(query.state.data?.status ?? "") ? false : 5000,
    retry: false,
  });
  const runStatus = run.data?.status ?? result?.status;
  const active = !!result?.task_uuid && !["completed", "failed", "cancelled"].includes(runStatus ?? "");
  const expired = plan ? !Number.isFinite(Date.parse(plan.expires_at)) || Date.parse(plan.expires_at) <= now : false;
  useEffect(() => {
    if (!plan?.executable || result?.task_uuid) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [plan, result?.task_uuid]);
  useEffect(() => {
    if (runStatus === "completed") {
      void client.invalidateQueries({ queryKey: ["/publish/pending"] });
      void client.invalidateQueries({ queryKey: ["/publish/theme-history"] });
    }
  }, [runStatus, client]);
  async function check() {
    if (lock.current || !available || active) return;
    lock.current = true; setBusy("check"); setError(null); setPlan(null); setResult(null); setInvalidated(false);
    try {
      setPlan(await apiFetch<PublicationPlan>("/publish/plans", {
        method: "POST", body: JSON.stringify({ job_id: "site-release", ...(mode !== "auto" ? { kind: mode } : {}),
          ...(mode === "content" ? { channel: selectedChannel } : {}) }), timeoutMs: 120_000,
      }));
      setNow(Date.now());
    } catch (value) { setError(value); }
    finally { lock.current = false; setBusy(null); }
  }
  async function execute() {
    if (lock.current || !plan?.executable || plan.preview_only || expired || invalidated || result?.task_uuid || !capabilities?.plan_execution) return;
    lock.current = true; setBusy("run"); setError(null);
    try {
      // Derive the same key from the frozen plan even after a lost response or
      // another tab retries it. Backend idempotency is still authoritative.
      setResult(await apiFetch<PublicationRun>("/publish/runs", { method: "POST", timeoutMs: 120_000,
        body: JSON.stringify({ plan_id: plan.plan_id, plan_digest: plan.plan_digest, idempotency_key: `publication:${plan.plan_id}` }) }));
      await client.invalidateQueries({ queryKey: ["/publish/pending"] });
    } catch (value) { setError(value); if (value instanceof ApiError && [409, 410].includes(value.status)) setInvalidated(true); }
    finally { lock.current = false; setBusy(null); }
  }
  async function rollback(targetPlanId: string) {
    if (lock.current || active || !capabilities?.rollback) return;
    lock.current = true; setBusy("rollback"); setError(null); setPlan(null); setResult(null); setInvalidated(false);
    try {
      setPlan(await apiFetch<PublicationPlan>("/publish/rollback-plans", { method: "POST", timeoutMs: 120_000,
        body: JSON.stringify({ kind: "theme", target_plan_id: targetPlanId, job_id: "site-release" }) }));
      setNow(Date.now());
    } catch (value) { setError(value); }
    finally { lock.current = false; setBusy(null); }
  }
  const kind = (value: string) => text.kinds[value as keyof typeof text.kinds] ?? value;
  return <section className="cc-panel space-y-4" aria-labelledby="publication-plan-title">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 id="publication-plan-title" className="text-base font-semibold">{text.title}</h2><p className="cc-muted mt-1 text-sm">{text.description}</p></div>
      <button className="cc-button cc-button-primary" disabled={!available || unavailable || !!busy || active || (mode === "content" && !selectedChannel)} onClick={() => void check()}>{busy === "check" ? text.checking : text.check}</button>
    </div>
    {available && capabilities?.plan_execution && <div className="flex flex-wrap gap-3 text-sm">
      <label className="grid gap-1.5"><span>{text.purpose}</span><select className="cc-input" value={mode} disabled={!!busy || active} onChange={event => { setMode(event.target.value as PlanMode); setPlan(null); setResult(null); setError(null); }}>
        <option value="auto">{text.autoDetect}</option>
        {modes.map(item => <option value={item.id} key={item.id} disabled={!item.supported}>{kind(item.id)}{!item.supported ? ` (${text.notEnabled})` : ""}</option>)}
      </select></label>
      {mode === "content" && <label className="grid gap-1.5"><span>{text.channels}</span><select className="cc-input" value={selectedChannel ?? ""} disabled={!!busy || active || !channels.length} onChange={event => { setChannel(event.target.value); setPlan(null); setResult(null); }}>
        {channels.map(item => <option key={item.id} value={item.id}>{modelPublishLabel(item.id, lang) || item.label}</option>)}
      </select></label>}
      {mode === "full" && <p className="self-end text-amber-800">{text.fullWarning}</p>}
    </div>}
    {!available && <p className="cc-muted text-sm">{loading ? text.loading : unavailable ? text.unavailable : text.legacy}</p>}
    {error != null && <p role="alert" className="rounded-md bg-rose-50 p-3 text-sm text-rose-800">{planProblem(error, lang)}</p>}
    {plan && <div className="space-y-3" role="status">
      <div className="flex flex-wrap gap-2"><StatusBadge tone="neutral">{kind(plan.kind)}</StatusBadge><StatusBadge tone={plan.executable && !plan.preview_only ? "neutral" : "warning"}>{plan.executable && !plan.preview_only ? text.readyPlan : text.previewOnly}</StatusBadge>{plan.rollback_of && <StatusBadge tone="warning">{text.rollbackPlan}</StatusBadge>}</div>
      <p className="cc-muted text-sm">{plan.executable && !plan.preview_only ? text.executionHint : text.previewHint}</p>
      <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 text-sm">
        {[
          [text.contentCount, plan.content_record_count ?? text.unknown],
          [text.htmlCount, plan.html_page_count ?? text.unknown],
          [text.assets, plan.asset_compile_scope?.join(", ") || text.none],
          [text.pages, plan.pages_deploy_required ? text.required : text.notRequired],
          [text.worker, plan.worker_deploy_required ? text.required : text.notRequired],
          [text.channels, plan.channels?.map(value => modelPublishLabel(value, lang)).join(", ") || text.none],
          [text.templates, plan.affected_templates?.join(", ") || text.none],
          [text.confidence, text.confidences[plan.cost_estimate_confidence]],
        ].map(([label, value]) => <div key={label}><dt className="cc-muted">{label}</dt><dd className="mt-1 break-words font-medium">{value}</dd></div>)}
      </dl>
      <p className="cc-muted text-xs">{text.created}: {modelPublishDate(plan.created_at, lang)} · {text.expires}: {modelPublishDate(plan.expires_at, lang)}</p>
      {expired && plan.executable && !result?.task_uuid && <p className="text-sm text-amber-800">{text.recheck}</p>}
      {plan.blocked_reason && <p className="text-sm text-amber-800">{text.reasons[plan.blocked_reason as keyof typeof text.reasons] ?? text.warningUnknown}</p>}
      {!!plan.warnings?.length && <ul className="list-inside list-disc space-y-1 rounded-md bg-amber-50 p-3 text-sm text-amber-900">{plan.warnings.map((warning, index) => <li key={index}>{text.reasons[warning as keyof typeof text.reasons] ?? text.warningUnknown}</li>)}</ul>}
      <details className="text-sm"><summary className="cursor-pointer font-medium">{text.details}</summary>
        <dl className="mt-3 grid gap-2 text-xs"><div><dt className="cc-muted">{text.planId}</dt><dd className="break-all font-mono">{plan.plan_id}</dd></div><div><dt className="cc-muted">{text.revision}</dt><dd className="break-all font-mono">{plan.source_revision || text.unknown}</dd></div><div><dt className="cc-muted">{text.digest}</dt><dd className="break-all font-mono">{plan.plan_digest}</dd></div></dl>
        {!!plan.changed_files?.length && <div className="mt-3 overflow-x-auto"><table className="cc-table w-full text-left text-xs"><thead><tr><th scope="col">{text.file}</th><th scope="col">{text.classification}</th></tr></thead><tbody>{plan.changed_files.map(file => <tr key={file.path}><td className="break-all font-mono">{file.path}</td><td>{kind(file.kind)}</td></tr>)}</tbody></table></div>}
      </details>
      {plan.kind === "theme" && plan.executable && <ThemePlanPreview planId={plan.plan_id} lang={lang} />}
      {capabilities?.plan_execution && plan.executable && !plan.preview_only && <button className="cc-button cc-button-primary" disabled={!!busy || expired || invalidated || !!result?.task_uuid || unavailable} onClick={() => void execute()}>{busy === "run" ? text.submitting : plan.rollback_of ? text.executeRollback : text.execute}</button>}
    </div>}
    {result && <div className="space-y-2 border-t border-[var(--cc-border)] pt-3" role="status">
      <p className="text-sm">{result.task_uuid ? text.accepted : text.notQueued}</p>
      {result.task_uuid && <div className="flex flex-wrap items-center gap-3"><StatusBadge status={runStatus}>{modelPublishLabel(runStatus ?? "queued", lang)}</StatusBadge><Link className="cc-link underline text-sm" href={`/publish/tasks?detail=${encodeURIComponent(result.task_uuid)}`}>{text.openRun}</Link></div>}
      {run.error && <p className="text-sm text-amber-800">{text.runUnavailable}</p>}
      {runStatus === "failed" && <p className="text-sm text-rose-800">{text.runFailed}</p>}
      {result.task_uuid && plan && <PublicationReceiptPanel planId={plan.plan_id} runStatus={runStatus} lang={lang} />}
    </div>}
    {capabilities?.rollback && <ThemeRollbackHistory lang={lang} disabled={!!busy || active} onPlan={rollback} />}
  </section>;
}

function PublicationReceiptPanel({ planId, runStatus, lang }: { planId: string; runStatus?: string; lang: Language }) {
  const text = publicationPlanningMessages[lang];
  const query = useQuery({ queryKey: ["/publish/plans", planId, "receipt"],
    queryFn: () => apiFetch<PublicationReceipt>(`/publish/plans/${encodeURIComponent(planId)}/receipt`),
    refetchInterval: current => current.state.data?.database_confirmed || ["failed", "cancelled"].includes(runStatus ?? "") ? false : 5000,
    retry: false });
  const receipt = query.data;
  if (query.error) return <p className="text-sm text-amber-800">{text.receiptUnavailable}</p>;
  if (!receipt) return null;
  const state = (value?: boolean | null) => value === true ? text.stageDone : value === false ? text.stagePending : text.unknown;
  const partial = receipt.authoritative_committed === true && receipt.public_verified === false;
  return <div className="space-y-3 text-sm">
    {partial && <p className="rounded-md bg-amber-50 p-3 text-amber-900">{text.partiallyCommitted}</p>}
    <dl className="grid gap-3 sm:grid-cols-3">
      {[[text.authoritativeCommit, state(receipt.authoritative_committed)], [text.publicVerification, state(receipt.public_verified)], [text.databaseConfirmation, state(receipt.database_confirmed)]].map(([label, value]) => <div key={label}><dt className="cc-muted">{label}</dt><dd>{value}</dd></div>)}
    </dl>
    <dl className="grid gap-3 sm:grid-cols-3">
      {[[text.actualContent, receipt.content_render_count], [text.actualHtml, receipt.html_assembly_count], [text.actualCompile, receipt.template_compile_count], [text.uploadedObjects, receipt.uploaded_objects], [text.uploadedBytes, receipt.uploaded_bytes], [text.durationSeconds, receipt.duration_seconds]].map(([label, value]) => <div key={label}><dt className="cc-muted">{label}</dt><dd>{value ?? text.unknown}</dd></div>)}
    </dl>
  </div>;
}

function ThemePlanPreview({ planId, lang }: { planId: string; lang: Language }) {
  const text = publicationPlanningMessages[lang];
  const [open, setOpen] = useState(false);
  const [locale, setLocale] = useState("en");
  const [family, setFamily] = useState("home");
  const [width, setWidth] = useState(390);
  const preview = useQuery({ queryKey: ["/publish/plans", planId, "previews"],
    queryFn: () => apiFetch<{ pages: string[]; missing: string[] }>(`/publish/plans/${encodeURIComponent(planId)}/previews`),
    enabled: open, staleTime: Infinity, retry: false });
  const present = preview.data?.pages?.includes(`${locale}/${family}`);
  return <div className="space-y-3">
    <button className="cc-button" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? text.closePreview : text.previewTheme}</button>
    {open && <div className="space-y-3 rounded-md border border-[var(--cc-border)] p-3">
      <p className="cc-muted text-sm">{text.previewThemeHint}</p>
      <div className="flex flex-wrap gap-3 text-sm">
        <label className="grid gap-1"><span>{text.previewLanguage}</span><select className="cc-input" value={locale} onChange={event => setLocale(event.target.value)}><option value="en">English</option><option value="zh">中文</option><option value="fr">Français</option></select></label>
        <label className="grid gap-1"><span>{text.previewPage}</span><select className="cc-input" value={family} onChange={event => setFamily(event.target.value)}><option value="home">{text.home}</option><option value="country">{text.country}</option></select></label>
        <label className="grid gap-1"><span>{text.previewDevice}</span><select className="cc-input" value={width} onChange={event => setWidth(Number(event.target.value))}><option value={390}>{text.mobile}</option><option value={1280}>{text.desktop}</option></select></label>
      </div>
      {preview.error || (preview.data && !present) ? <p className="text-sm text-amber-800">{text.previewUnavailable}</p> : present ? <div className="overflow-x-auto rounded-md border border-[var(--cc-border)] bg-white"><iframe key={`${planId}/${locale}/${family}`} title={text.previewTheme} sandbox="" referrerPolicy="no-referrer" src={`/api/v1/publish/plans/${encodeURIComponent(planId)}/preview/${locale}/${family}`} width={width} height={650} style={{ width, minWidth: width, border: 0 }} /></div> : <p className="cc-muted text-sm">{text.loadingPreview}</p>}
    </div>}
  </div>;
}

function ThemeRollbackHistory({ lang, disabled, onPlan }: { lang: Language; disabled: boolean; onPlan: (id: string) => Promise<void> }) {
  const text = publicationPlanningMessages[lang];
  const [target, setTarget] = useState("");
  const history = useQuery({ queryKey: ["/publish/theme-history"],
    queryFn: () => optionalEndpoint<ThemeHistory>("/publish/theme-history"), staleTime: 30_000, retry: false });
  const items = history.data?.items?.filter(item => item.public_verified && item.compatibility === "v1") ?? [];
  const selected = items.some(item => item.plan_id === target) ? target : items[0]?.plan_id;
  return <div className="space-y-3 border-t border-[var(--cc-border)] pt-3">
    <h3 className="text-sm font-semibold">{text.rollbackTheme}</h3><p className="cc-muted text-sm">{text.rollbackHint}</p>
    {history.error ? <p className="text-sm text-amber-800">{text.historyUnavailable}</p> : !items.length ? <p className="cc-muted text-sm">{text.noVerifiedThemes}</p> : <div className="flex flex-wrap items-end gap-3"><label className="grid gap-1.5 text-sm"><span>{text.targetVersion}</span><select className="cc-input" value={selected} disabled={disabled} onChange={event => setTarget(event.target.value)}>{items.map(item => <option key={item.plan_id} value={item.plan_id}>{modelPublishDate(item.created_at, lang)} · {item.version.slice(0, 12)}</option>)}</select></label><button className="cc-button" disabled={disabled || !selected} onClick={() => void onPlan(selected!)}>{text.checkRollback}</button></div>}
  </div>;
}

export function PublicationUsagePanel({ lang }: { lang: Language }) {
  const text = publicationPlanningMessages[lang];
  const query = useQuery({
    queryKey: ["/publish/usage"], queryFn: () => optionalEndpoint<PublicationUsage>("/publish/usage"),
    staleTime: 60_000, refetchInterval: 60_000, retry: false,
  });
  const usage = query.data;
  const status = query.error || !usage || !["known", "stale"].includes(usage.status) ? "unknown" : usage.status;
  const number = (value: number | null | undefined) => status === "unknown" || value == null ? text.unknown : value.toLocaleString(lang === "zh" ? "zh-CN" : "en-US");
  return <section className="cc-panel space-y-3" aria-labelledby="publication-usage-title">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 id="publication-usage-title" className="text-base font-semibold">{text.usage}</h2><StatusBadge tone={status === "stale" ? "warning" : "neutral"}>{text.statuses[status]}</StatusBadge></div>
    <p className="cc-muted text-sm">{text.usageHint}</p>
    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 text-sm">
      {[[text.accountPlan, usage?.plan === "free" ? "Workers Free" : usage?.plan === "paid" ? "Workers Paid" : text.unknown], [text.total, number(usage?.account_total)], [text.limit, number(usage?.limit)], [text.remaining, number(usage?.remaining)], [text.projected, number(usage?.projected_usage)], [text.observed, modelPublishDate(usage?.observed_at, lang)], [text.source, usage?.source === "cloudflare_graphql_workersInvocationsAdaptive_estimate" ? text.adaptiveSource : usage?.source || text.unknown], [text.period, usage?.period_start && usage.period_end ? `${modelPublishDate(usage.period_start, lang)} – ${modelPublishDate(usage.period_end, lang)}` : text.unknown]].map(([label, value]) => <div key={label}><dt className="cc-muted">{label}</dt><dd className="mt-1 break-words font-medium">{value}</dd></div>)}
    </dl>
    {usage?.plan === "unknown" && <p className="cc-muted text-sm">{text.usagePlanUnknown}</p>}
    {status === "unknown" && <p className="cc-muted text-sm">{text.usageUnknown}</p>}
    {status === "stale" && <p className="text-sm text-amber-800">{text.usageStale}</p>}
    {!!usage?.services?.length && <dl className="flex flex-wrap gap-4 border-t border-[var(--cc-border)] pt-3 text-sm">{usage.services.map(service => <div key={service.id}><dt className="cc-muted">{service.id}</dt><dd>{number(service.requests)}</dd></div>)}</dl>}
  </section>;
}
