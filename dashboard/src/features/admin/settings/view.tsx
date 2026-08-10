"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { Badge, Button, Card, Text, Title } from "@/components/ui/tremor";
import {
  Check,
  Cloud,
  ExternalLink,
  GitBranch,
  Globe2,
  Mail,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  TestTube2,
} from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge as HeaderBadge } from "@/components/ui/StatusBadge";
import { useAppStore } from "@/stores/app-store";
import {
  useResetSettingsSection,
  useSendTestEmail,
  useSettings,
  useTestSmtpConnection,
  useUpdateCloudflareSettings,
  useUpdateGithubSettings,
  useUpdateSiteSettings,
  useUpdateSmtpSettings,
  type CloudflareSettingsInput,
  type GithubSettingsInput,
  type SiteSettingsInput,
  type SmtpSettingsInput,
} from "@/features/admin/api";

type NoticeState = { kind: "success" | "error"; message: string } | null;
type SectionKey = "smtp" | "github" | "archive" | "cloudflare" | "site";

const inputCls =
  "h-10 w-full rounded-lg border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-emphasis outline-none transition placeholder:text-tremor-content-subtle focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis";
const areaCls = `${inputCls} h-auto min-h-20 py-2`;

function Label({ children }: { children: ReactNode }) {
  return <label className="mb-1.5 block text-xs font-semibold text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">{children}</label>;
}

function StateBadge({ label, color = "slate" }: { label: string; color?: "slate" | "teal" | "emerald" | "rose" | "amber" | "blue" }) {
  return <Badge color={color} size="xs">{label}</Badge>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <div><Label>{label}</Label>{children}{hint && <p className="mt-1.5 text-xs leading-5 text-tremor-content-subtle">{hint}</p>}</div>;
}

function SectionHeading({ icon, title, description, status, onReset, resetLabel }: { icon: ReactNode; title: string; description: string; status: ReactNode; onReset: () => void; resetLabel: string }) {
  return (
    <div className="flex flex-col gap-3 border-b border-tremor-border pb-4 dark:border-dark-tremor-border sm:flex-row sm:items-start sm:justify-between">
      <div className="flex gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-tremor-brand-muted text-tremor-brand dark:bg-dark-tremor-brand-muted dark:text-dark-tremor-brand">{icon}</div>
        <div><Title className="!text-base">{title}</Title><p className="mt-1 max-w-2xl text-xs leading-5 text-tremor-content-subtle">{description}</p></div>
      </div>
      <div className="flex shrink-0 items-center gap-2">{status}<button type="button" onClick={onReset} className="text-xs font-medium text-tremor-content-subtle transition hover:text-tremor-content-strong">{resetLabel}</button></div>
    </div>
  );
}

function SaveRow({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 border-t border-tremor-border pt-4 dark:border-dark-tremor-border">{children}</div>;
}

export default function SettingPage() {
  const { lang } = useAppStore();
  const isZh = lang === "zh";
  const { data: settings, isLoading } = useSettings();
  const updateSmtp = useUpdateSmtpSettings();
  const updateGithub = useUpdateGithubSettings();
  const updateCloudflare = useUpdateCloudflareSettings();
  const updateSite = useUpdateSiteSettings();
  const resetSection = useResetSettingsSection();
  const testSmtp = useTestSmtpConnection();
  const sendTestEmail = useSendTestEmail();

  const [activeSection, setActiveSection] = useState<SectionKey>("smtp");
  const [smtpForm, setSmtpForm] = useState<SmtpSettingsInput>({ smtp_host: "", smtp_port: 587, smtp_username: "", smtp_password: "", smtp_from_email: "", smtp_use_tls: true, admin_emails_raw: "" });
  const [githubForm, setGithubForm] = useState<GithubSettingsInput>({ github_data_share_repo_url: "", github_data_share_repo_branch: "", github_data_share_raw_base_url: "", raw_archive_enabled: false, raw_archive_repo_url: "", raw_archive_branch: "main", default_github_remote: "origin", default_github_branch: "" });
  const [cloudflareForm, setCloudflareForm] = useState<CloudflareSettingsInput>({ cloudflare_api_token: "", cloudflare_account_id: "", default_cloudflare_project_name: "globalid" });
  const [siteForm, setSiteForm] = useState<SiteSettingsInput>({ public_ga4_measurement_id: "G-8P39XV52NC" });
  const [testRecipient, setTestRecipient] = useState("");
  const [smtpResult, setSmtpResult] = useState<NoticeState>(null);
  const [emailResult, setEmailResult] = useState<NoticeState>(null);
  const [notice, setNotice] = useState<NoticeState>(null);

  useEffect(() => {
    if (!settings) return;
    setSmtpForm((v) => ({ ...v, smtp_host: settings.smtp.smtp_host, smtp_port: settings.smtp.smtp_port, smtp_username: settings.smtp.smtp_username, smtp_from_email: settings.smtp.smtp_from_email, smtp_use_tls: settings.smtp.smtp_use_tls, admin_emails_raw: settings.smtp.admin_emails_raw, smtp_password: "" }));
    setGithubForm((v) => ({ ...v, github_data_share_repo_url: settings.github.github_data_share_repo_url, github_data_share_repo_branch: settings.github.github_data_share_repo_branch, github_data_share_raw_base_url: settings.github.github_data_share_raw_base_url, raw_archive_enabled: settings.github.raw_archive_enabled, raw_archive_repo_url: settings.github.raw_archive_repo_url, raw_archive_branch: settings.github.raw_archive_branch, default_github_remote: settings.github.default_github_remote, default_github_branch: settings.github.default_github_branch }));
    setCloudflareForm((v) => ({ ...v, cloudflare_api_token: "", cloudflare_account_id: "", default_cloudflare_project_name: settings.cloudflare.default_cloudflare_project_name }));
    setSiteForm({ public_ga4_measurement_id: settings.site?.public_ga4_measurement_id ?? "G-8P39XV52NC" });
    setTestRecipient((v) => v.trim() ? v : settings.smtp.admin_emails_raw);
  }, [settings]);

  const summary = useMemo(() => [
    { key: "smtp" as const, icon: <Mail className="h-4 w-4" />, title: isZh ? "邮件通知" : "Email alerts", value: settings?.smtp.alerting_ready ? (isZh ? "已就绪" : "Ready") : (isZh ? "待配置" : "Needs setup"), detail: settings?.smtp.smtp_host || (isZh ? "尚未配置 SMTP" : "SMTP not configured"), ready: Boolean(settings?.smtp.alerting_ready) },
    { key: "github" as const, icon: <GitBranch className="h-4 w-4" />, title: isZh ? "数据发布" : "Data publishing", value: settings?.github.github_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "待配置" : "Needs setup"), detail: settings?.github.github_data_share_repo_url || "—", ready: Boolean(settings?.github.github_configured) },
    { key: "archive" as const, icon: <ShieldCheck className="h-4 w-4" />, title: isZh ? "原始数据归档" : "Raw data archive", value: settings?.github.raw_archive_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "未启用" : "Not enabled"), detail: settings?.github.raw_archive_repo_url || "—", ready: Boolean(settings?.github.raw_archive_configured) },
    { key: "cloudflare" as const, icon: <Cloud className="h-4 w-4" />, title: "Cloudflare", value: settings?.cloudflare.cloudflare_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "待配置" : "Needs setup"), detail: settings?.cloudflare.default_cloudflare_project_name || "—", ready: Boolean(settings?.cloudflare.cloudflare_configured) },
    { key: "site" as const, icon: <Globe2 className="h-4 w-4" />, title: isZh ? "网站分析" : "Site analytics", value: settings?.site.ga4_configured ? (isZh ? "已启用" : "Enabled") : (isZh ? "未启用" : "Disabled"), detail: settings?.site.public_ga4_measurement_id || "—", ready: Boolean(settings?.site.ga4_configured) },
  ], [isZh, settings]);

  const saveSmtp = async () => {
    setNotice(null); setSmtpResult(null);
    const payload: SmtpSettingsInput = { smtp_host: smtpForm.smtp_host?.trim() || "", smtp_port: smtpForm.smtp_port ? Number(smtpForm.smtp_port) : 587, smtp_username: smtpForm.smtp_username?.trim() || "", smtp_from_email: smtpForm.smtp_from_email?.trim() || "", smtp_use_tls: Boolean(smtpForm.smtp_use_tls), admin_emails_raw: smtpForm.admin_emails_raw?.trim() || "" };
    if (smtpForm.smtp_password?.trim()) payload.smtp_password = smtpForm.smtp_password.trim();
    try { await updateSmtp.mutateAsync(payload); setNotice({ kind: "success", message: isZh ? "邮件设置已保存。" : "Email settings saved." }); setSmtpForm((v) => ({ ...v, smtp_password: "" })); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const githubPayload = (): GithubSettingsInput => ({ github_data_share_repo_url: githubForm.github_data_share_repo_url?.trim() || "", github_data_share_repo_branch: githubForm.github_data_share_repo_branch?.trim() || "", github_data_share_raw_base_url: githubForm.github_data_share_raw_base_url?.trim() || "", raw_archive_enabled: Boolean(githubForm.raw_archive_enabled), raw_archive_repo_url: githubForm.raw_archive_repo_url?.trim() || "", raw_archive_branch: githubForm.raw_archive_branch?.trim() || "main", default_github_remote: githubForm.default_github_remote?.trim() || "", default_github_branch: githubForm.default_github_branch?.trim() || "" });
  const saveGithub = async () => {
    setNotice(null);
    try { await updateGithub.mutateAsync(githubPayload()); setNotice({ kind: "success", message: isZh ? "数据发布设置已保存。" : "Publishing settings saved." }); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const saveArchive = async () => {
    setNotice(null);
    if (githubForm.raw_archive_enabled && !githubForm.raw_archive_repo_url?.trim()) { setNotice({ kind: "error", message: isZh ? "启用原始数据归档时必须填写归档仓库 URL。" : "Archive repository URL is required when raw data archiving is enabled." }); return; }
    try { await updateGithub.mutateAsync(githubPayload()); setNotice({ kind: "success", message: isZh ? "原始数据归档设置已保存。" : "Archive settings saved." }); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const saveCloudflare = async () => {
    setNotice(null); const payload: CloudflareSettingsInput = { default_cloudflare_project_name: cloudflareForm.default_cloudflare_project_name?.trim() || "" };
    if (cloudflareForm.cloudflare_api_token?.trim()) payload.cloudflare_api_token = cloudflareForm.cloudflare_api_token.trim();
    if (cloudflareForm.cloudflare_account_id?.trim()) payload.cloudflare_account_id = cloudflareForm.cloudflare_account_id.trim();
    try { await updateCloudflare.mutateAsync(payload); setNotice({ kind: "success", message: isZh ? "Cloudflare 设置已保存。" : "Cloudflare settings saved." }); setCloudflareForm((v) => ({ ...v, cloudflare_api_token: "", cloudflare_account_id: "" })); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const saveSite = async () => {
    setNotice(null); const measurementId = siteForm.public_ga4_measurement_id?.trim().toUpperCase() || "";
    if (measurementId && !/^G-[A-Z0-9]{6,20}$/.test(measurementId)) { setNotice({ kind: "error", message: isZh ? "GA4 Measurement ID 格式应为 G-XXXXXXXXXX。" : "GA4 Measurement ID must use G-XXXXXXXXXX format." }); return; }
    try { await updateSite.mutateAsync({ public_ga4_measurement_id: measurementId }); setNotice({ kind: "success", message: isZh ? "网站设置已保存。下次构建时生效。" : "Site settings saved. They apply on the next build." }); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const reset = async (section: SectionKey) => {
    if (!window.confirm(isZh ? "恢复该分组的环境默认值？" : "Reset this section to environment defaults?")) return;
    try { await resetSection.mutateAsync(section === "archive" ? "github" : section); setNotice({ kind: "success", message: isZh ? "已恢复环境默认值。" : "Reset to environment defaults." }); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const clearSmtpPassword = async () => {
    if (!window.confirm(isZh ? "清除已保存的 SMTP 密码？" : "Clear the saved SMTP password?")) return;
    try { await updateSmtp.mutateAsync({ clear_smtp_password: true }); setNotice({ kind: "success", message: isZh ? "SMTP 密码已清除。" : "SMTP password cleared." }); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const clearCloudflareCredentials = async () => {
    if (!window.confirm(isZh ? "清除已保存的 Cloudflare 凭据？" : "Clear the saved Cloudflare credentials?")) return;
    try { await updateCloudflare.mutateAsync({ clear_cloudflare_api_token: true, clear_cloudflare_account_id: true }); setNotice({ kind: "success", message: isZh ? "Cloudflare 凭据已清除。" : "Cloudflare credentials cleared." }); } catch (e) { setNotice({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const runSmtpTest = async () => {
    setSmtpResult(null); try { const r = await testSmtp.mutateAsync(); setSmtpResult({ kind: "success", message: r.message || (isZh ? "SMTP 连接成功。" : "SMTP connection successful.") }); } catch (e) { setSmtpResult({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };
  const sendEmailProbe = async () => {
    const recipient = testRecipient.trim(); setEmailResult(null);
    if (!recipient) { setEmailResult({ kind: "error", message: isZh ? "请输入测试收件人。" : "Enter a test recipient." }); return; }
    try { const r = await sendTestEmail.mutateAsync(recipient); setEmailResult({ kind: "success", message: r.message || (isZh ? "测试邮件已发送。" : "Test email sent.") }); } catch (e) { setEmailResult({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
  };

  const noticeCls = (n: NoticeState) => n ? `rounded-lg border px-3 py-2.5 text-sm ${n.kind === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}` : "";

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <PageHeader eyebrow={isZh ? "管理" : "Administration"} title={isZh ? "设置中心" : "Settings"} description={isZh ? "集中管理系统连接和网站配置。选择一个分组开始编辑。" : "Manage system connections and site configuration in one place. Choose a section to edit."} actions={<div className="flex gap-2"><Link href="/data/release" className="inline-flex h-10 items-center gap-2 rounded-lg border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong hover:bg-tremor-background-subtle"><ExternalLink className="h-4 w-4" />{isZh ? "数据发布" : "Release"}</Link></div>} />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {summary.map((item) => <button key={item.key} type="button" onClick={() => setActiveSection(item.key)} className={`rounded-xl border bg-tremor-background p-4 text-left transition hover:-translate-y-0.5 hover:shadow-sm ${activeSection === item.key ? "border-tremor-brand ring-1 ring-tremor-brand/20" : "border-tremor-border"}`}><div className="flex items-center justify-between"><span className="text-tremor-content-subtle">{item.icon}</span><StateBadge label={item.value} color={item.ready ? "emerald" : "amber"} /></div><p className="mt-3 text-sm font-semibold text-tremor-content-strong">{item.title}</p><p className="mt-1 truncate text-xs text-tremor-content-subtle">{item.detail}</p></button>)}
      </div>

      {notice && <div className={noticeCls(notice)}>{notice.message}</div>}
      {isLoading && !settings ? <Card><Text>{isZh ? "正在加载设置…" : "Loading settings…"}</Text></Card> : (
        <Card className="min-w-0 !p-5 sm:!p-6">
            {activeSection === "smtp" && <>
              <SectionHeading icon={<Mail className="h-4 w-4" />} title={isZh ? "邮件通知" : "Email notifications"} description={isZh ? "任务告警、AI 报告和自动化提醒都会使用这里的配置。" : "Task alerts, AI reports and automation notices use this configuration."} status={<StateBadge label={settings?.smtp.smtp_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "待配置" : "Needs setup")} color={settings?.smtp.smtp_configured ? "emerald" : "amber"} />} onReset={() => reset("smtp")} resetLabel={isZh ? "恢复默认" : "Reset"} />
              <div className="mt-5 space-y-5"><div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_140px]"><Field label={isZh ? "SMTP 主机" : "SMTP host"}><input className={inputCls} value={smtpForm.smtp_host ?? ""} onChange={(e) => setSmtpForm((v) => ({ ...v, smtp_host: e.target.value }))} placeholder="email-smtp.us-east-1.amazonaws.com" /></Field><Field label={isZh ? "端口" : "Port"}><input className={inputCls} type="number" min={1} value={smtpForm.smtp_port ?? 587} onChange={(e) => setSmtpForm((v) => ({ ...v, smtp_port: Number(e.target.value) || 587 }))} /></Field></div><div className="grid gap-4 md:grid-cols-2"><Field label={isZh ? "用户名" : "Username"}><input className={inputCls} value={smtpForm.smtp_username ?? ""} onChange={(e) => setSmtpForm((v) => ({ ...v, smtp_username: e.target.value }))} /></Field><Field label={isZh ? "发件邮箱" : "From email"}><input className={inputCls} type="email" value={smtpForm.smtp_from_email ?? ""} onChange={(e) => setSmtpForm((v) => ({ ...v, smtp_from_email: e.target.value }))} /></Field></div><Field label={isZh ? "密码" : "Password"} hint={settings?.smtp.smtp_password_present ? (isZh ? "已保存密码不会回显；留空即可保留。" : "The saved password is hidden; leave blank to keep it.") : (isZh ? "当前没有保存密码。" : "No password is currently saved.")}><input className={inputCls} type="password" value={smtpForm.smtp_password ?? ""} onChange={(e) => setSmtpForm((v) => ({ ...v, smtp_password: e.target.value }))} placeholder={settings?.smtp.smtp_password_present ? (isZh ? "留空以保留当前密码" : "Leave blank to keep current password") : (isZh ? "输入 SMTP 密码" : "Enter SMTP password")} />{settings?.smtp.smtp_password_present && <button type="button" onClick={clearSmtpPassword} className="mt-2 text-xs font-medium text-rose-600 hover:underline">{isZh ? "清除已保存密码" : "Clear saved password"}</button>}</Field><Field label={isZh ? "管理员邮箱" : "Admin recipients"} hint={isZh ? "多个邮箱可用逗号或换行分隔。" : "Separate multiple recipients with commas or new lines."}><textarea className={areaCls} rows={2} value={smtpForm.admin_emails_raw ?? ""} onChange={(e) => setSmtpForm((v) => ({ ...v, admin_emails_raw: e.target.value }))} placeholder="admin@example.com, ops@example.com" /></Field><label className="flex cursor-pointer items-center gap-2 text-sm text-tremor-content-emphasis"><input type="checkbox" checked={Boolean(smtpForm.smtp_use_tls)} onChange={(e) => setSmtpForm((v) => ({ ...v, smtp_use_tls: e.target.checked }))} className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted" />{isZh ? "使用 STARTTLS" : "Use STARTTLS"}</label>{smtpResult && <div className={noticeCls(smtpResult)}>{smtpResult.message}</div>}<div className="rounded-lg bg-tremor-background-subtle p-3"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-sm font-semibold">{isZh ? "发送测试邮件" : "Send a test email"}</p><p className="mt-1 text-xs text-tremor-content-subtle">{isZh ? "验证连接和收件人配置是否可用。" : "Verify the connection and recipient configuration."}</p></div><div className="flex gap-2"><Button size="xs" variant="light" icon={TestTube2} onClick={runSmtpTest} loading={testSmtp.isPending}>{isZh ? "测试连接" : "Test connection"}</Button></div></div><div className="mt-3 flex flex-col gap-2 sm:flex-row"><input className={inputCls} value={testRecipient} onChange={(e) => setTestRecipient(e.target.value)} placeholder="ops@example.com" /><Button size="xs" variant="light" icon={Send} onClick={sendEmailProbe} loading={sendTestEmail.isPending}>{isZh ? "发送" : "Send"}</Button></div>{emailResult && <div className={`mt-3 ${noticeCls(emailResult)}`}>{emailResult.message}</div>}</div><SaveRow><Button size="sm" variant="primary" icon={RefreshCw} onClick={saveSmtp} loading={updateSmtp.isPending}>{isZh ? "保存邮件设置" : "Save email settings"}</Button></SaveRow></div>
            </>}
            {activeSection === "github" && <>
              <SectionHeading icon={<GitBranch className="h-4 w-4" />} title={isZh ? "数据发布" : "Data publishing"} description={isZh ? "集中管理下载仓库和发布默认值，减少重复填写。" : "Keep download repository and publishing defaults in one place."} status={<StateBadge label={settings?.github.github_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "待配置" : "Needs setup")} color={settings?.github.github_configured ? "emerald" : "slate"} />} onReset={() => reset("github")} resetLabel={isZh ? "恢复默认" : "Reset"} />
              <div className="mt-5 space-y-5"><Field label={isZh ? "下载仓库 URL" : "Download repository URL"}><input className={inputCls} value={githubForm.github_data_share_repo_url ?? ""} onChange={(e) => setGithubForm((v) => ({ ...v, github_data_share_repo_url: e.target.value }))} placeholder="git@github.com:owner/repo.git" /></Field><div className="grid gap-4 md:grid-cols-2"><Field label={isZh ? "下载分支" : "Download branch"}><input className={inputCls} value={githubForm.github_data_share_repo_branch ?? ""} onChange={(e) => setGithubForm((v) => ({ ...v, github_data_share_repo_branch: e.target.value }))} placeholder="main" /></Field><Field label={isZh ? "Raw Base URL（可选）" : "Raw base URL (optional)"}><input className={inputCls} value={githubForm.github_data_share_raw_base_url ?? ""} onChange={(e) => setGithubForm((v) => ({ ...v, github_data_share_raw_base_url: e.target.value }))} placeholder="https://raw.githubusercontent.com/..." /></Field></div><div className="grid gap-4 md:grid-cols-2"><Field label={isZh ? "默认 remote" : "Default remote"}><input className={inputCls} value={githubForm.default_github_remote ?? ""} onChange={(e) => setGithubForm((v) => ({ ...v, default_github_remote: e.target.value }))} placeholder="origin" /></Field><Field label={isZh ? "默认 branch" : "Default branch"}><input className={inputCls} value={githubForm.default_github_branch ?? ""} onChange={(e) => setGithubForm((v) => ({ ...v, default_github_branch: e.target.value }))} placeholder="main" /></Field></div><div className="rounded-lg bg-tremor-background-subtle p-3 text-xs"><span className="font-semibold">{isZh ? "自动推导的 Raw URL：" : "Derived raw URL: "}</span><span className="break-all text-tremor-content-subtle">{settings?.github.github_data_share_raw_base_url_effective || "/downloads"}</span></div><SaveRow><Button size="sm" variant="primary" icon={RefreshCw} onClick={saveGithub} loading={updateGithub.isPending}>{isZh ? "保存发布设置" : "Save publishing settings"}</Button></SaveRow></div>
            </>}
            {activeSection === "archive" && <>
              <SectionHeading icon={<ShieldCheck className="h-4 w-4" />} title={isZh ? "原始数据归档" : "Raw data archive"} description={isZh ? "将 data/raw 增量同步到独立仓库，与数据发布设置并列管理。" : "Incrementally mirror data/raw to a dedicated repository as a separate configuration."} status={<StateBadge label={settings?.github.raw_archive_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "未启用" : "Not enabled")} color={settings?.github.raw_archive_configured ? "emerald" : "slate"} />} onReset={() => reset("github")} resetLabel={isZh ? "恢复默认" : "Reset"} />
              <div className="mt-5 space-y-5"><div className="flex items-center justify-between gap-3 rounded-lg border border-tremor-border p-4 dark:border-dark-tremor-border"><div><p className="text-sm font-semibold">{isZh ? "启用原始数据归档" : "Enable raw data archive"}</p><p className="mt-1 text-xs text-tremor-content-subtle">{isZh ? "数据发布流程会将 data/raw 增量同步到此仓库。" : "The release pipeline will incrementally mirror data/raw to this repository."}</p></div><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(githubForm.raw_archive_enabled)} onChange={(e) => setGithubForm((v) => ({ ...v, raw_archive_enabled: e.target.checked }))} className="h-4 w-4 rounded border-tremor-border text-tremor-brand" />{isZh ? "启用" : "Enable"}</label></div><div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_140px]"><Field label={isZh ? "归档仓库 URL" : "Archive repository URL"}><input className={inputCls} value={githubForm.raw_archive_repo_url ?? ""} onChange={(e) => setGithubForm((v) => ({ ...v, raw_archive_repo_url: e.target.value }))} placeholder="git@github.com:owner/raw-archive.git" disabled={!githubForm.raw_archive_enabled} /></Field><Field label={isZh ? "归档分支" : "Archive branch"}><input className={inputCls} value={githubForm.raw_archive_branch ?? ""} onChange={(e) => setGithubForm((v) => ({ ...v, raw_archive_branch: e.target.value }))} placeholder="main" disabled={!githubForm.raw_archive_enabled} /></Field></div><SaveRow><Button size="sm" variant="primary" icon={RefreshCw} onClick={saveArchive} loading={updateGithub.isPending}>{isZh ? "保存归档设置" : "Save archive settings"}</Button></SaveRow></div>
            </>}
            {activeSection === "cloudflare" && <>
              <SectionHeading icon={<Cloud className="h-4 w-4" />} title="Cloudflare" description={isZh ? "管理 Pages 项目默认值和发布凭据。" : "Manage the Pages project default and deployment credentials."} status={<StateBadge label={settings?.cloudflare.cloudflare_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "待配置" : "Needs setup")} color={settings?.cloudflare.cloudflare_configured ? "emerald" : "slate"} />} onReset={() => reset("cloudflare")} resetLabel={isZh ? "恢复默认" : "Reset"} />
              <div className="mt-5 space-y-5"><Field label="API Token" hint={settings?.cloudflare.cloudflare_api_token_present ? (isZh ? "已有 token，不会回显；留空即可保留。" : "A token is saved but hidden; leave blank to keep it.") : (isZh ? "当前没有保存 token。" : "No token is currently saved.")}><input className={inputCls} type="password" value={cloudflareForm.cloudflare_api_token ?? ""} onChange={(e) => setCloudflareForm((v) => ({ ...v, cloudflare_api_token: e.target.value }))} placeholder={settings?.cloudflare.cloudflare_api_token_present ? (isZh ? "留空以保留当前 token" : "Leave blank to keep current token") : (isZh ? "输入新的 token" : "Enter a new token")} /></Field><div className="grid gap-4 md:grid-cols-2"><Field label="Account ID"><input className={inputCls} value={cloudflareForm.cloudflare_account_id ?? ""} onChange={(e) => setCloudflareForm((v) => ({ ...v, cloudflare_account_id: e.target.value }))} placeholder="Cloudflare account ID" /></Field><Field label={isZh ? "默认 Pages 项目名" : "Default Pages project"}><input className={inputCls} value={cloudflareForm.default_cloudflare_project_name ?? ""} onChange={(e) => setCloudflareForm((v) => ({ ...v, default_cloudflare_project_name: e.target.value }))} placeholder="globalid" /></Field></div><div className="flex items-center gap-2 text-xs text-tremor-content-subtle"><ShieldCheck className="h-4 w-4" />{isZh ? "凭据只用于数据发布，不会在界面中显示。" : "Credentials are used for publishing and are never displayed."}<StateBadge label={settings?.cloudflare.cloudflare_api_token_present ? (isZh ? "Token 已保存" : "Token saved") : (isZh ? "无 Token" : "No token")} color={settings?.cloudflare.cloudflare_api_token_present ? "emerald" : "slate"} /></div><SaveRow><Button size="sm" variant="primary" icon={RefreshCw} onClick={saveCloudflare} loading={updateCloudflare.isPending}>{isZh ? "保存 Cloudflare" : "Save Cloudflare"}</Button>{(settings?.cloudflare.cloudflare_api_token_present || settings?.cloudflare.cloudflare_account_id_present) && <Button size="sm" variant="light" onClick={clearCloudflareCredentials} loading={updateCloudflare.isPending}>{isZh ? "清除凭据" : "Clear credentials"}</Button>}</SaveRow></div>
            </>}
            {activeSection === "site" && <>
              <SectionHeading icon={<Globe2 className="h-4 w-4" />} title={isZh ? "网站分析" : "Site analytics"} description={isZh ? "管理静态网站构建时注入的公开配置。" : "Manage public configuration injected during the static site build."} status={<StateBadge label={settings?.site.ga4_configured ? (isZh ? "已启用" : "Enabled") : (isZh ? "未启用" : "Disabled")} color={settings?.site.ga4_configured ? "emerald" : "slate"} />} onReset={() => reset("site")} resetLabel={isZh ? "恢复默认" : "Reset"} />
              <div className="mt-5 max-w-xl space-y-5"><Field label="GA4 Measurement ID" hint={isZh ? "这是公开标识，不是密钥。留空可关闭 GA4。保存后在下次 Astro 构建时生效。" : "This is a public identifier, not a secret. Leave blank to disable GA4. It applies on the next Astro build."}><input className={inputCls} value={siteForm.public_ga4_measurement_id ?? ""} onChange={(e) => setSiteForm({ public_ga4_measurement_id: e.target.value.toUpperCase() })} placeholder="G-XXXXXXXXXX" autoComplete="off" /></Field><div className="flex items-center gap-2 rounded-lg bg-tremor-background-subtle p-3 text-xs text-tremor-content-subtle"><ShieldCheck className="h-4 w-4 shrink-0" />{isZh ? "生效流程：保存 → 本地构建 → 发布 dist。" : "Flow: save → local build → publish dist."}</div><SaveRow><Button size="sm" variant="primary" icon={RefreshCw} onClick={saveSite} loading={updateSite.isPending}>{isZh ? "保存网站设置" : "Save site settings"}</Button></SaveRow></div>
            </>}
        </Card>
      )}
    </div>
  );
}
