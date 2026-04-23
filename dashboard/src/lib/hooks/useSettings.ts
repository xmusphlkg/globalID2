import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export interface SmtpSettings {
  source: string;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_from_email: string;
  smtp_use_tls: boolean;
  admin_emails_raw: string;
  admin_emails: string[];
  smtp_password_present: boolean;
  smtp_configured: boolean;
  alerting_ready: boolean;
}

export interface GithubSettings {
  source: string;
  github_data_share_repo_url: string;
  github_data_share_repo_branch: string;
  github_data_share_raw_base_url: string;
  github_data_share_raw_base_url_effective: string;
  default_github_remote: string;
  default_github_branch: string;
  github_configured: boolean;
  release_defaults_ready: boolean;
}

export interface CloudflareSettings {
  source: string;
  cloudflare_api_token_present: boolean;
  cloudflare_account_id_present: boolean;
  default_cloudflare_project_name: string;
  cloudflare_configured: boolean;
}

export interface RuntimeSettings {
  smtp: SmtpSettings;
  github: GithubSettings;
  cloudflare: CloudflareSettings;
}

export interface SmtpSettingsInput {
  smtp_host?: string;
  smtp_port?: number;
  smtp_username?: string;
  smtp_password?: string;
  smtp_from_email?: string;
  smtp_use_tls?: boolean;
  admin_emails_raw?: string;
}

export interface GithubSettingsInput {
  github_data_share_repo_url?: string;
  github_data_share_repo_branch?: string;
  github_data_share_raw_base_url?: string;
  default_github_remote?: string;
  default_github_branch?: string;
}

export interface CloudflareSettingsInput {
  cloudflare_api_token?: string;
  cloudflare_account_id?: string;
  default_cloudflare_project_name?: string;
}

export function useSettings() {
  return useQuery<RuntimeSettings>({
    queryKey: ["settings"],
    queryFn: () => apiFetch("/settings"),
    staleTime: 5 * 1000,
  });
}

function invalidateSettingsRelatedQueries(queryClient: ReturnType<typeof useQueryClient>, sections: string[]) {
  queryClient.invalidateQueries({ queryKey: ["settings"] });
  queryClient.invalidateQueries({ queryKey: ["tasks"] });
  queryClient.invalidateQueries({ queryKey: ["task"] });
  if (sections.includes("smtp")) {
    queryClient.invalidateQueries({ queryKey: ["data-release"] });
    queryClient.invalidateQueries({ queryKey: ["sources-automation"] });
  }
  if (sections.includes("github") || sections.includes("cloudflare")) {
    queryClient.invalidateQueries({ queryKey: ["data-release"] });
    queryClient.invalidateQueries({ queryKey: ["data-release-checks"] });
  }
}

export function useUpdateSmtpSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SmtpSettingsInput) =>
      apiFetch("/settings/smtp", {
        method: "PUT",
        body: JSON.stringify(payload),
      }) as Promise<RuntimeSettings>,
    onSuccess: () => invalidateSettingsRelatedQueries(queryClient, ["smtp"]),
  });
}

export function useUpdateGithubSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: GithubSettingsInput) =>
      apiFetch("/settings/github", {
        method: "PUT",
        body: JSON.stringify(payload),
      }) as Promise<RuntimeSettings>,
    onSuccess: () => invalidateSettingsRelatedQueries(queryClient, ["github"]),
  });
}

export function useUpdateCloudflareSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CloudflareSettingsInput) =>
      apiFetch("/settings/cloudflare", {
        method: "PUT",
        body: JSON.stringify(payload),
      }) as Promise<RuntimeSettings>,
    onSuccess: () => invalidateSettingsRelatedQueries(queryClient, ["cloudflare"]),
  });
}

export function useResetSettingsSection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (section: "smtp" | "github" | "cloudflare") =>
      apiFetch(`/settings/${section}`, {
        method: "DELETE",
      }) as Promise<RuntimeSettings>,
    onSuccess: () => invalidateSettingsRelatedQueries(queryClient, ["smtp", "github", "cloudflare"]),
  });
}

export function useTestSmtpConnection() {
  return useMutation({
    mutationFn: () =>
      apiFetch<{ ok: boolean; message: string; checked_at: string }>("/settings/smtp/test", {
        method: "POST",
        timeoutMs: 30_000,
      }),
  });
}

export function useSendTestEmail() {
  return useMutation({
    mutationFn: (recipient: string) =>
      apiFetch<{ ok: boolean; message: string; checked_at: string }>("/settings/smtp/send-test-email", {
        method: "POST",
        body: JSON.stringify({ recipient }),
        timeoutMs: 30_000,
      }),
  });
}
