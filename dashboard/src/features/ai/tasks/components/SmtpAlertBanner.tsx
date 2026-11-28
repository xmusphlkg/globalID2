"use client";

import Link from "next/link";
import { Mail } from "lucide-react";
import { Card, Text, Title } from "@/components/ui/tremor";

interface SmtpAlertBannerProps {
  lang: "en" | "zh";
  alertingReady?: boolean;
}

export function SmtpAlertBanner({ lang, alertingReady }: SmtpAlertBannerProps) {
  return (
    <Card
      className={`border ${
        alertingReady
          ? "border-emerald-200 dark:border-emerald-900/40"
          : "border-amber-200 dark:border-amber-900/40"
      }`}
    >
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="flex items-start gap-3">
          <div
            className={`rounded-tremor-default p-2 ${
              alertingReady
                ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-300"
                : "bg-amber-50 text-amber-600 dark:bg-amber-950/30 dark:text-amber-300"
            }`}
          >
            <Mail className="h-5 w-5" />
          </div>
          <div>
            <Text className="text-xs font-semibold uppercase text-tremor-content-subtle">
              {lang === "zh" ? "SMTP 提醒" : "SMTP Alerts"}
            </Text>
            <Title className="mt-1 text-xl">
              {alertingReady
                ? lang === "zh"
                  ? "邮件提醒已就绪"
                  : "Email alerts are ready"
                : lang === "zh"
                  ? "需要补齐设置"
                  : "Needs setup"}
            </Title>
            <Text className="mt-2 text-sm">
              {alertingReady
                ? lang === "zh"
                  ? "AI 报告完成邮件，以及任务失败、取消等提醒，都会通过 SMTP 发送到设置中心维护的邮箱。"
                  : "AI report completion mail, plus failure and cancellation alerts, all go through SMTP to the Settings recipient list."
                : lang === "zh"
                  ? "先到设置中心配置 SMTP 主机、密码和收件人。"
                  : "Open Settings to configure SMTP host, password, and recipients."}
            </Text>
          </div>
        </div>
        <Link
          href="/settings/integrations"
          className="inline-flex items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 py-2 text-sm font-semibold text-tremor-brand-inverted transition hover:opacity-90"
        >
          {lang === "zh" ? "打开设置中心" : "Open Settings"}
          <Mail className="h-4 w-4" />
        </Link>
      </div>
    </Card>
  );
}
