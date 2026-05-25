"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { Badge, Button, Card, Text, Title } from "@tremor/react";
import {
  Cloud,
  ExternalLink,
  GitBranch,
  Mail,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  WandSparkles,
} from "lucide-react";
import { MetricTile } from "@/components/ui/MetricTile";
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
  useUpdateSmtpSettings,
  type CloudflareSettingsInput,
  type GithubSettingsInput,
  type SmtpSettingsInput,
} from "@/features/admin/api";

type NoticeState = {
  kind: "success" | "error";
  message: string;
} | null;

const inputCls =
  "w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-emphasis outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-emphasis dark:focus:border-dark-tremor-brand-subtle dark:focus:ring-dark-tremor-brand-muted";

function SectionLabel({ children }: { children: ReactNode }) {
  return <label className="mb-1 block text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{children}</label>;
}

function StatusBadge({ label, color = "slate" }: { label: string; color?: "slate" | "teal" | "emerald" | "rose" | "amber" | "blue" }) {
  return <Badge color={color} size="xs">{label}</Badge>;
}

export default function SettingPage() {
  const { lang } = useAppStore();
  const isZh = lang === "zh";
  const { data: settings, isLoading } = useSettings();
  const updateSmtp = useUpdateSmtpSettings();
  const updateGithub = useUpdateGithubSettings();
  const updateCloudflare = useUpdateCloudflareSettings();
  const resetSection = useResetSettingsSection();
  const testSmtp = useTestSmtpConnection();
  const sendTestEmail = useSendTestEmail();

  const [smtpForm, setSmtpForm] = useState<SmtpSettingsInput>({
    smtp_host: "",
    smtp_port: 587,
    smtp_username: "",
    smtp_password: "",
    smtp_from_email: "",
    smtp_use_tls: true,
    admin_emails_raw: "",
  });
  const [githubForm, setGithubForm] = useState<GithubSettingsInput>({
    github_data_share_repo_url: "",
    github_data_share_repo_branch: "",
    github_data_share_raw_base_url: "",
    default_github_remote: "origin",
    default_github_branch: "",
  });
  const [cloudflareForm, setCloudflareForm] = useState<CloudflareSettingsInput>({
    cloudflare_api_token: "",
    cloudflare_account_id: "",
    default_cloudflare_project_name: "globalid",
  });
  const [testRecipient, setTestRecipient] = useState("");
  const [smtpResult, setSmtpResult] = useState<NoticeState>(null);
  const [emailResult, setEmailResult] = useState<NoticeState>(null);
  const [notice, setNotice] = useState<NoticeState>(null);

  useEffect(() => {
    if (!settings) return;

    setSmtpForm((current) => ({
      ...current,
      smtp_host: settings.smtp.smtp_host,
      smtp_port: settings.smtp.smtp_port,
      smtp_username: settings.smtp.smtp_username,
      smtp_from_email: settings.smtp.smtp_from_email,
      smtp_use_tls: settings.smtp.smtp_use_tls,
      admin_emails_raw: settings.smtp.admin_emails_raw,
      smtp_password: "",
    }));
    setGithubForm((current) => ({
      ...current,
      github_data_share_repo_url: settings.github.github_data_share_repo_url,
      github_data_share_repo_branch: settings.github.github_data_share_repo_branch,
      github_data_share_raw_base_url: settings.github.github_data_share_raw_base_url,
      default_github_remote: settings.github.default_github_remote,
      default_github_branch: settings.github.default_github_branch,
    }));
    setCloudflareForm((current) => ({
      ...current,
      cloudflare_api_token: "",
      cloudflare_account_id: "",
      default_cloudflare_project_name: settings.cloudflare.default_cloudflare_project_name,
    }));
    setTestRecipient((current) => current.trim() ? current : settings.smtp.admin_emails_raw);
  }, [settings]);

  const summaryCards = useMemo(() => [
    {
      title: isZh ? "SMTP 提醒" : "SMTP Alerts",
      value: settings?.smtp.alerting_ready ? (isZh ? "已就绪" : "Ready") : (isZh ? "未就绪" : "Not ready"),
      desc: settings?.smtp.alerting_ready
        ? (isZh ? "任务失败/取消会自动发信。" : "Failed or cancelled tasks will trigger email alerts.")
        : (isZh ? "补齐收件人和 SMTP 凭据。" : "Add recipients and SMTP credentials."),
      color: settings?.smtp.alerting_ready ? "emerald" : "amber",
    },
    {
      title: isZh ? "GitHub 默认值" : "GitHub Defaults",
      value: settings?.github.github_configured ? (isZh ? "可用" : "Configured") : (isZh ? "未配置" : "Not set"),
      desc: settings?.github.github_data_share_repo_url || settings?.github.github_data_share_raw_base_url_effective || "-",
      color: settings?.github.github_configured ? "teal" : "slate",
    },
    {
      title: isZh ? "Cloudflare 默认值" : "Cloudflare Defaults",
      value: settings?.cloudflare.cloudflare_configured ? (isZh ? "可用" : "Configured") : (isZh ? "未配置" : "Not set"),
      desc: settings?.cloudflare.default_cloudflare_project_name || "-",
      color: settings?.cloudflare.cloudflare_configured ? "blue" : "slate",
    },
  ], [isZh, settings]);

  const saveSmtp = async () => {
    setNotice(null);
    setSmtpResult(null);
    const payload: SmtpSettingsInput = {
      smtp_host: smtpForm.smtp_host?.trim() || "",
      smtp_port: smtpForm.smtp_port ? Number(smtpForm.smtp_port) : 587,
      smtp_username: smtpForm.smtp_username?.trim() || "",
      smtp_from_email: smtpForm.smtp_from_email?.trim() || "",
      smtp_use_tls: Boolean(smtpForm.smtp_use_tls),
      admin_emails_raw: smtpForm.admin_emails_raw?.trim() || "",
    };
    if (smtpForm.smtp_password?.trim()) {
      payload.smtp_password = smtpForm.smtp_password.trim();
    }

    try {
      await updateSmtp.mutateAsync(payload);
      setNotice({
        kind: "success",
        message: isZh ? "SMTP 设置已保存。" : "SMTP settings saved.",
      });
      setSmtpForm((current) => ({ ...current, smtp_password: "" }));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ kind: "error", message });
    }
  };

  const saveGithub = async () => {
    setNotice(null);
    const payload: GithubSettingsInput = {
      github_data_share_repo_url: githubForm.github_data_share_repo_url?.trim() || "",
      github_data_share_repo_branch: githubForm.github_data_share_repo_branch?.trim() || "",
      github_data_share_raw_base_url: githubForm.github_data_share_raw_base_url?.trim() || "",
      default_github_remote: githubForm.default_github_remote?.trim() || "",
      default_github_branch: githubForm.default_github_branch?.trim() || "",
    };

    try {
      await updateGithub.mutateAsync(payload);
      setNotice({
        kind: "success",
        message: isZh ? "GitHub 设置已保存。" : "GitHub settings saved.",
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ kind: "error", message });
    }
  };

  const saveCloudflare = async () => {
    setNotice(null);
    const payload: CloudflareSettingsInput = {
      default_cloudflare_project_name: cloudflareForm.default_cloudflare_project_name?.trim() || "",
    };
    if (cloudflareForm.cloudflare_api_token?.trim()) {
      payload.cloudflare_api_token = cloudflareForm.cloudflare_api_token.trim();
    }
    if (cloudflareForm.cloudflare_account_id?.trim()) {
      payload.cloudflare_account_id = cloudflareForm.cloudflare_account_id.trim();
    }

    try {
      await updateCloudflare.mutateAsync(payload);
      setNotice({
        kind: "success",
        message: isZh ? "Cloudflare 设置已保存。" : "Cloudflare settings saved.",
      });
      setCloudflareForm((current) => ({
        ...current,
        cloudflare_api_token: "",
        cloudflare_account_id: "",
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ kind: "error", message });
    }
  };

  const reset = async (section: "smtp" | "github" | "cloudflare") => {
    setNotice(null);
    try {
      await resetSection.mutateAsync(section);
      setNotice({
        kind: "success",
        message: isZh ? "已恢复为环境默认值。" : "Reset to environment defaults.",
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ kind: "error", message });
    }
  };

  const runSmtpTest = async () => {
    setSmtpResult(null);
    try {
      const result = await testSmtp.mutateAsync();
      setSmtpResult({
        kind: "success",
        message: result.message || (isZh ? "SMTP 连接成功。" : "SMTP connection successful."),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSmtpResult({ kind: "error", message });
    }
  };

  const sendEmailProbe = async () => {
    setEmailResult(null);
    const recipient = testRecipient.trim();
    if (!recipient) {
      setEmailResult({
        kind: "error",
        message: isZh ? "请输入测试收件人。" : "Enter a test recipient.",
      });
      return;
    }
    try {
      const result = await sendTestEmail.mutateAsync(recipient);
      setEmailResult({
        kind: "success",
        message: result.message || (isZh ? "测试邮件已发送。" : "Test email sent."),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setEmailResult({ kind: "error", message });
    }
  };

  const smtpCardSubtitle = isZh
    ? "集中管理邮件凭据与收件人。AI 报告完成邮件、任务失败/取消告警、自动化提醒和测试邮件都会从这里读取。"
    : "Centralize email credentials and recipients. AI report completion mail, task alerts, automation warnings, and test emails all read from here.";

  const githubCardSubtitle = isZh
    ? "管理下载仓库和数据发布默认值，避免在各个页面重复填写。"
    : "Manage the download repository and publish defaults so each workflow does not need to repeat them.";

  const cloudflareCardSubtitle = isZh
    ? "管理 Pages 项目的默认值与访问凭据。"
    : "Manage the default Pages project and access credentials.";

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={isZh ? "设置中心" : "Settings Center"}
        title={isZh ? "统一设置管理" : "Unified Settings"}
        description={
          isZh
            ? "把 SMTP、GitHub 和 Cloudflare 的运行时设置收归到一个入口，任务告警、数据发布和外部接入都读取同一份配置。"
            : "Keep SMTP, GitHub, and Cloudflare runtime settings in one place so alerts, publishing, and external access share the same configuration."
        }
        meta={
          <>
            <HeaderBadge tone={settings?.smtp.alerting_ready ? "success" : "warning"}>
              {isZh ? "SMTP 告警" : "SMTP Alerts"}: {settings?.smtp.alerting_ready ? (isZh ? "已就绪" : "Ready") : (isZh ? "未就绪" : "Not ready")}
            </HeaderBadge>
            <HeaderBadge>{settings?.smtp.source || "env"}</HeaderBadge>
            <HeaderBadge>{settings?.github.source || "env"}</HeaderBadge>
            <HeaderBadge>{settings?.cloudflare.source || "env"}</HeaderBadge>
          </>
        }
        actions={
          <>
            <Link href="/ai/tasks" className="inline-flex h-10 items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 text-sm font-semibold text-tremor-brand-inverted transition hover:opacity-90">
              {isZh ? "回到 AI 任务" : "Back to AI Tasks"}
              <ExternalLink className="h-4 w-4" />
            </Link>
            <Link href="/data/release" className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-4 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background-subtle dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle">
              {isZh ? "打开数据发布" : "Open Release"}
              <ExternalLink className="h-4 w-4" />
            </Link>
          </>
        }
      />

      {notice && (
        <div className={`rounded-tremor-default border px-4 py-3 text-sm ${notice.kind === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-200" : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200"}`}>
          {notice.message}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        {summaryCards.map((card) => (
          <MetricTile
            key={card.title}
            label={card.title}
            value={card.value}
            hint={card.desc || "-"}
            icon={<WandSparkles className="h-4 w-4" />}
            tone={card.color === "rose" ? "danger" : card.color === "amber" ? "warning" : card.color === "emerald" ? "success" : "primary"}
          />
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-1">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div className="rounded-tremor-default bg-tremor-background-muted p-2 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong">
                <Mail className="h-4 w-4" />
              </div>
              <div>
                <Title className="!text-base">{isZh ? "SMTP 设置" : "SMTP Settings"}</Title>
                <Text className="text-xs">{smtpCardSubtitle}</Text>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge label={settings?.smtp.smtp_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "未配置" : "Missing")} color={settings?.smtp.smtp_configured ? "emerald" : "amber"} />
              <Button size="xs" variant="light" icon={RotateCcw} onClick={() => reset("smtp")} loading={resetSection.isPending}>
                {isZh ? "重置" : "Reset"}
              </Button>
            </div>
          </div>

          <div className="mt-4 space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <SectionLabel>{isZh ? "SMTP 主机" : "SMTP Host"}</SectionLabel>
                <input value={smtpForm.smtp_host ?? ""} onChange={(e) => setSmtpForm((current) => ({ ...current, smtp_host: e.target.value }))} className={inputCls} placeholder="email-smtp.us-east-1.amazonaws.com" />
              </div>
              <div>
                <SectionLabel>{isZh ? "SMTP 端口" : "SMTP Port"}</SectionLabel>
                <input type="number" min={1} value={smtpForm.smtp_port ?? 587} onChange={(e) => setSmtpForm((current) => ({ ...current, smtp_port: Number(e.target.value) || 587 }))} className={inputCls} />
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <SectionLabel>{isZh ? "用户名" : "Username"}</SectionLabel>
                <input value={smtpForm.smtp_username ?? ""} onChange={(e) => setSmtpForm((current) => ({ ...current, smtp_username: e.target.value }))} className={inputCls} />
              </div>
              <div>
                <SectionLabel>{isZh ? "发件邮箱" : "From Email"}</SectionLabel>
                <input value={smtpForm.smtp_from_email ?? ""} onChange={(e) => setSmtpForm((current) => ({ ...current, smtp_from_email: e.target.value }))} className={inputCls} />
              </div>
            </div>

            <div>
              <SectionLabel>{isZh ? "密码" : "Password"}</SectionLabel>
              <input
                type="password"
                value={smtpForm.smtp_password ?? ""}
                onChange={(e) => setSmtpForm((current) => ({ ...current, smtp_password: e.target.value }))}
                className={inputCls}
                placeholder={settings?.smtp.smtp_password_present ? (isZh ? "留空则保留现有密码" : "Leave blank to keep current password") : (isZh ? "输入新的 SMTP 密码" : "Enter a new SMTP password")}
              />
              <Text className="mt-1 text-xs text-tremor-content-subtle">
                {settings?.smtp.smtp_password_present
                  ? (isZh ? "当前密码已保存，但不会回显。" : "A password is already saved, but it is never shown.")
                  : (isZh ? "当前没有保存密码。" : "No password is currently saved.")}
              </Text>
            </div>

            <div>
              <SectionLabel>{isZh ? "管理员邮箱" : "Admin Emails"}</SectionLabel>
              <textarea
                rows={3}
                value={smtpForm.admin_emails_raw ?? ""}
                onChange={(e) => setSmtpForm((current) => ({ ...current, admin_emails_raw: e.target.value }))}
                className={inputCls}
                placeholder="admin@example.com, ops@example.com"
              />
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <label className="flex cursor-pointer items-center gap-2 rounded-tremor-default border border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
                <input
                  type="checkbox"
                  checked={Boolean(smtpForm.smtp_use_tls)}
                  onChange={(e) => setSmtpForm((current) => ({ ...current, smtp_use_tls: e.target.checked }))}
                  className="h-4 w-4 rounded border-tremor-border text-tremor-brand focus:ring-tremor-brand-muted"
                />
                <span className="text-sm text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                  {isZh ? "使用 STARTTLS" : "Use STARTTLS"}
                </span>
              </label>
              <div className="rounded-tremor-default border border-tremor-border px-3 py-2 dark:border-dark-tremor-border">
                <Text className="text-[11px] uppercase text-tremor-content-subtle">
                  {isZh ? "状态" : "Status"}
                </Text>
                <div className="mt-2 flex flex-wrap gap-2">
                  <StatusBadge label={settings?.smtp.smtp_password_present ? (isZh ? "密码已保存" : "Password saved") : (isZh ? "无密码" : "No password")} color={settings?.smtp.smtp_password_present ? "emerald" : "slate"} />
                  <StatusBadge label={settings?.smtp.alerting_ready ? (isZh ? "提醒可用" : "Alerts ready") : (isZh ? "提醒未就绪" : "Alerts not ready")} color={settings?.smtp.alerting_ready ? "emerald" : "amber"} />
                </div>
              </div>
            </div>

            {smtpResult && (
              <div className={`rounded-tremor-default border px-3 py-2 text-xs ${smtpResult.kind === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-200" : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200"}`}>
                {smtpResult.message}
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="primary" icon={RefreshCw} onClick={saveSmtp} loading={updateSmtp.isPending}>
                {isZh ? "保存 SMTP" : "Save SMTP"}
              </Button>
              <Button size="sm" variant="secondary" icon={RefreshCw} onClick={runSmtpTest} loading={testSmtp.isPending}>
                {isZh ? "测试连接" : "Test Connection"}
              </Button>
            </div>

            <div className="rounded-tremor-default border border-dashed border-tremor-border p-3 dark:border-dark-tremor-border">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <Text className="font-medium text-tremor-content-strong">{isZh ? "发送测试邮件" : "Send Test Email"}</Text>
                  <Text className="mt-1 text-xs text-tremor-content-subtle">{isZh ? "默认可直接使用管理员邮箱。" : "You can reuse the admin email list as the recipient."}</Text>
                </div>
                <Button size="xs" variant="light" icon={Send} onClick={sendEmailProbe} loading={sendTestEmail.isPending}>
                  {isZh ? "发送" : "Send"}
                </Button>
              </div>
              <input
                className={`${inputCls} mt-3`}
                value={testRecipient}
                onChange={(e) => setTestRecipient(e.target.value)}
                placeholder="ops@example.com"
              />
              {emailResult && (
                <div className={`mt-3 rounded-tremor-default border px-3 py-2 text-xs ${emailResult.kind === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-200" : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/40 dark:bg-rose-950/30 dark:text-rose-200"}`}>
                  {emailResult.message}
                </div>
              )}
            </div>
          </div>
        </Card>

        <Card className="xl:col-span-1">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div className="rounded-tremor-default bg-tremor-background-muted p-2 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong">
                <GitBranch className="h-4 w-4" />
              </div>
              <div>
                <Title className="!text-base">{isZh ? "GitHub 设置" : "GitHub Settings"}</Title>
                <Text className="text-xs">{githubCardSubtitle}</Text>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge label={settings?.github.github_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "未配置" : "Missing")} color={settings?.github.github_configured ? "teal" : "slate"} />
              <Button size="xs" variant="light" icon={RotateCcw} onClick={() => reset("github")} loading={resetSection.isPending}>
                {isZh ? "重置" : "Reset"}
              </Button>
            </div>
          </div>

          <div className="mt-4 space-y-4">
            <div>
              <SectionLabel>{isZh ? "下载仓库 URL" : "Download Repo URL"}</SectionLabel>
              <input value={githubForm.github_data_share_repo_url ?? ""} onChange={(e) => setGithubForm((current) => ({ ...current, github_data_share_repo_url: e.target.value }))} className={inputCls} placeholder="git@github.com:owner/repo.git" />
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <SectionLabel>{isZh ? "下载分支" : "Download Branch"}</SectionLabel>
                <input value={githubForm.github_data_share_repo_branch ?? ""} onChange={(e) => setGithubForm((current) => ({ ...current, github_data_share_repo_branch: e.target.value }))} className={inputCls} placeholder="main" />
              </div>
              <div>
                <SectionLabel>{isZh ? "原始文件 Base URL" : "Raw Base URL"}</SectionLabel>
                <input value={githubForm.github_data_share_raw_base_url ?? ""} onChange={(e) => setGithubForm((current) => ({ ...current, github_data_share_raw_base_url: e.target.value }))} className={inputCls} placeholder="https://raw.githubusercontent.com/..." />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <SectionLabel>{isZh ? "默认 remote" : "Default Remote"}</SectionLabel>
                <input value={githubForm.default_github_remote ?? ""} onChange={(e) => setGithubForm((current) => ({ ...current, default_github_remote: e.target.value }))} className={inputCls} placeholder="origin" />
              </div>
              <div>
                <SectionLabel>{isZh ? "默认 branch" : "Default Branch"}</SectionLabel>
                <input value={githubForm.default_github_branch ?? ""} onChange={(e) => setGithubForm((current) => ({ ...current, default_github_branch: e.target.value }))} className={inputCls} placeholder="main" />
              </div>
            </div>

            <div className="rounded-tremor-default border border-dashed border-tremor-border p-3 dark:border-dark-tremor-border">
              <Text className="text-[11px] uppercase text-tremor-content-subtle">{isZh ? "推断值" : "Derived"}</Text>
              <p className="mt-2 break-all text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {settings?.github.github_data_share_raw_base_url_effective || "/downloads"}
              </p>
              <Text className="mt-1 text-xs text-tremor-content-subtle">
                {isZh ? "如果不填写 raw base URL，会根据仓库地址和分支自动推导。" : "If raw base URL is empty, it will be derived from the repo URL and branch."}
              </Text>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="primary" icon={RefreshCw} onClick={saveGithub} loading={updateGithub.isPending}>
                {isZh ? "保存 GitHub" : "Save GitHub"}
              </Button>
            </div>
          </div>
        </Card>

        <Card className="xl:col-span-1">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div className="rounded-tremor-default bg-tremor-background-muted p-2 text-tremor-content-strong dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong">
                <Cloud className="h-4 w-4" />
              </div>
              <div>
                <Title className="!text-base">{isZh ? "Cloudflare 设置" : "Cloudflare Settings"}</Title>
                <Text className="text-xs">{cloudflareCardSubtitle}</Text>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge label={settings?.cloudflare.cloudflare_configured ? (isZh ? "已配置" : "Configured") : (isZh ? "未配置" : "Missing")} color={settings?.cloudflare.cloudflare_configured ? "blue" : "slate"} />
              <Button size="xs" variant="light" icon={RotateCcw} onClick={() => reset("cloudflare")} loading={resetSection.isPending}>
                {isZh ? "重置" : "Reset"}
              </Button>
            </div>
          </div>

          <div className="mt-4 space-y-4">
            <div>
              <SectionLabel>{isZh ? "API Token" : "API Token"}</SectionLabel>
              <input
                type="password"
                value={cloudflareForm.cloudflare_api_token ?? ""}
                onChange={(e) => setCloudflareForm((current) => ({ ...current, cloudflare_api_token: e.target.value }))}
                className={inputCls}
                placeholder={settings?.cloudflare.cloudflare_api_token_present ? (isZh ? "留空则保留当前 token" : "Leave blank to keep current token") : (isZh ? "输入新的 Cloudflare token" : "Enter a new Cloudflare token")}
              />
              <Text className="mt-1 text-xs text-tremor-content-subtle">
                {settings?.cloudflare.cloudflare_api_token_present
                  ? (isZh ? "当前 token 已保存，但不会回显。" : "A token is already saved, but it is never shown.")
                  : (isZh ? "当前没有保存 token。" : "No token is currently saved.")}
              </Text>
            </div>
            <div>
              <SectionLabel>{isZh ? "Account ID" : "Account ID"}</SectionLabel>
              <input
                value={cloudflareForm.cloudflare_account_id ?? ""}
                onChange={(e) => setCloudflareForm((current) => ({ ...current, cloudflare_account_id: e.target.value }))}
                className={inputCls}
                placeholder="cloudflare account id"
              />
            </div>
            <div>
              <SectionLabel>{isZh ? "默认 Pages 项目名" : "Default Pages Project"}</SectionLabel>
              <input
                value={cloudflareForm.default_cloudflare_project_name ?? ""}
                onChange={(e) => setCloudflareForm((current) => ({ ...current, default_cloudflare_project_name: e.target.value }))}
                className={inputCls}
                placeholder="globalid"
              />
            </div>
            <div className="rounded-tremor-default border border-dashed border-tremor-border p-3 dark:border-dark-tremor-border">
              <div className="flex items-center gap-2 text-xs text-tremor-content-subtle">
                <ShieldCheck className="h-4 w-4" />
                <span>{isZh ? "项目与凭据会被数据发布流程读取。" : "The release pipeline reads the project and credentials from here."}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <StatusBadge label={settings?.cloudflare.cloudflare_api_token_present ? (isZh ? "Token 已保存" : "Token saved") : (isZh ? "无 Token" : "No token")} color={settings?.cloudflare.cloudflare_api_token_present ? "emerald" : "slate"} />
                <StatusBadge label={settings?.cloudflare.cloudflare_account_id_present ? (isZh ? "Account ID 已保存" : "Account ID saved") : (isZh ? "无 Account ID" : "No Account ID")} color={settings?.cloudflare.cloudflare_account_id_present ? "emerald" : "slate"} />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="primary" icon={RefreshCw} onClick={saveCloudflare} loading={updateCloudflare.isPending}>
                {isZh ? "保存 Cloudflare" : "Save Cloudflare"}
              </Button>
            </div>
          </div>
        </Card>
      </div>

      <Card className="border-dashed border-tremor-border">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <Title className="!text-base">{isZh ? "统一归口说明" : "Unified Routing"}</Title>
            <Text className="mt-1 text-sm">
              {isZh
                ? "AI 任务完成邮件、失败取消提醒、自动化告警和数据发布默认值都会读取这份设置。"
                : "AI completion mail, task alerts, automation warnings, and data release defaults all read from this same settings layer."}
            </Text>
          </div>
          <Link href="/ai/tasks" className="inline-flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-semibold text-tremor-brand-inverted transition hover:opacity-90">
            {isZh ? "回到 AI 任务" : "Back to AI Tasks"}
            <ExternalLink className="h-4 w-4" />
          </Link>
        </div>
      </Card>
    </div>
  );
}
