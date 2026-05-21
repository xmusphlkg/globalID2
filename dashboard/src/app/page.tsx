"use client";

import Link from "next/link";
import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Database,
  Download,
  FileText,
  GitBranch,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { getCountryDisplayName, useCountries } from "@/lib/hooks/useCountries";
import { t } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";

const quickLinks = [
  {
    href: "/data/dashboard",
    titleKey: "dashboard",
    descEn: "Core disease metrics and trend analytics.",
    descZh: "先看核心疫情指标和趋势变化。",
    icon: BarChart3,
    tone: "primary",
  },
  {
    href: "/sources/tasks",
    titleKey: "crawl_tasks",
    descEn: "Manage ingestion jobs and inspect workbook logs.",
    descZh: "管理采集任务并查看工作簿日志。",
    icon: Download,
    tone: "info",
  },
  {
    href: "/sources/flow",
    titleKey: "flow_nav_label",
    descEn: "Track fetch, check, process, and finalize by source.",
    descZh: "按数据源追踪拉取、判断、入库和收尾。",
    icon: GitBranch,
    tone: "primary",
  },
  {
    href: "/data/diseases",
    titleKey: "diseases",
    descEn: "Explore disease-level records and comparisons.",
    descZh: "查看疾病维度数据并进行对比。",
    icon: Database,
    tone: "info",
  },
  {
    href: "/data/quality",
    titleKey: "quality",
    descEn: "Check completeness, gaps, and source consistency.",
    descZh: "检查完整性、时间缺口和来源一致性。",
    icon: ShieldCheck,
    tone: "warning",
  },
  {
    href: "/reports",
    titleKey: "reports",
    descEn: "Review generated report outputs and statuses.",
    descZh: "查看报告结果和发布状态。",
    icon: FileText,
    tone: "danger",
  },
  {
    href: "/data/explorer",
    titleKey: "explorer",
    descEn: "Browse tables and run quick record lookups.",
    descZh: "浏览数据表并进行快速检索。",
    icon: Search,
    tone: "neutral",
  },
] as const;

export default function HomePage() {
  const { lang, countryId, countryName, countryCode } = useAppStore();
  const { data: countries, error, isLoading } = useCountries();

  const selectedCountry = countries?.find((country) => country.id === countryId) ?? null;
  const selectedCountryLabel = selectedCountry
    ? getCountryDisplayName(selectedCountry, lang)
    : countryName;
  const recommendedLink = selectedCountry ? quickLinks[0] : quickLinks[1];
  const countryStatusTone = error
    ? "danger"
    : isLoading
      ? "warning"
      : selectedCountry
        ? "success"
        : "neutral";

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "brand_name")}
        title={t(lang, "workspace_title")}
        description={t(lang, "workspace_subtitle")}
        meta={
          <>
            <StatusBadge tone={countryStatusTone}>
              {countryCode || selectedCountry?.code || "--"}
            </StatusBadge>
            <StatusBadge tone="primary">{selectedCountryLabel || t(lang, "home_empty_country")}</StatusBadge>
          </>
        }
        actions={
          <Link
            href={recommendedLink.href}
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default bg-tremor-brand px-4 text-sm font-semibold text-tremor-brand-inverted transition hover:opacity-90 dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted"
          >
            {t(lang, "home_jump_to")}
            <ArrowRight className="h-4 w-4" />
          </Link>
        }
      />

      <div className="grid gap-3 lg:grid-cols-3">
        <MetricTile
          label={t(lang, "active_country")}
          value={selectedCountryLabel || "-"}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone={countryStatusTone}
          hint={selectedCountry?.timezone || t(lang, "active_country_hint")}
        />
        <MetricTile
          label={t(lang, "recommended_next")}
          value={t(lang, recommendedLink.titleKey)}
          icon={<Sparkles className="h-4 w-4" />}
          tone="primary"
          hint={lang === "zh" ? recommendedLink.descZh : recommendedLink.descEn}
        />
        <div className="rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {t(lang, "system_status")}
              </p>
              <p className="mt-2 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {error
                  ? t(lang, "system_status_country_unavailable")
                  : countries && countries.length > 0
                    ? t(lang, "system_status_country_ready")
                    : t(lang, "system_status_country_empty")}
              </p>
            </div>
            <StatusBadge tone={countryStatusTone}>
              {error
                ? t(lang, "system_status_unavailable")
                : countries && countries.length > 0
                  ? t(lang, "system_status_ready")
                  : t(lang, "system_status_partial")}
            </StatusBadge>
          </div>
          <p className="mt-3 text-xs text-tremor-content dark:text-dark-tremor-content">
            {selectedCountryLabel
              ? `${selectedCountryLabel} · ${t(lang, "active_country_hint")}`
              : t(lang, "home_empty_country")}
          </p>
        </div>
      </div>

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "home_jump_to")}
            </h2>
            <p className="text-sm text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {selectedCountryLabel || t(lang, "home_empty_country")}
            </p>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {quickLinks.map((item) => {
            const Icon = item.icon;
            const highlight = item.href === recommendedLink.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`group rounded-tremor-default border bg-tremor-background p-4 transition hover:border-tremor-brand-muted hover:bg-tremor-background-subtle dark:bg-dark-tremor-background dark:hover:bg-dark-tremor-background-subtle ${
                  highlight
                    ? "border-tremor-brand/40 dark:border-dark-tremor-brand/50"
                    : "border-tremor-border dark:border-dark-tremor-border"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-2 flex items-center gap-2">
                      {highlight ? <StatusBadge tone="primary">{t(lang, "recommended_next")}</StatusBadge> : null}
                    </div>
                    <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {t(lang, item.titleKey)}
                    </h3>
                    <p className="mt-1 text-sm text-tremor-content dark:text-dark-tremor-content">
                      {lang === "zh" ? item.descZh : item.descEn}
                    </p>
                  </div>
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-tremor-default bg-tremor-background-subtle text-tremor-brand dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-brand">
                    <Icon className="h-4 w-4" />
                  </div>
                </div>
                <div className="mt-4 flex items-center text-sm font-medium text-tremor-brand dark:text-dark-tremor-brand">
                  {lang === "zh" ? "进入" : "Open"}
                  <ArrowRight className="ml-1 h-4 w-4 transition group-hover:translate-x-0.5" />
                </div>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}
