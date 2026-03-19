"use client";

import Link from "next/link";
import { Badge, Card, Grid, Text, Title } from "@tremor/react";
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
import { getCountryDisplayName, useCountries } from "@/lib/hooks/useCountries";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const quickLinks = [
  {
    href: "/data/dashboard",
    titleKey: "dashboard",
    descEn: "Core disease metrics and trend analytics.",
    descZh: "先看核心疫情指标和趋势变化。",
    icon: BarChart3,
    color: "blue",
  },
  {
    href: "/sources/tasks",
    titleKey: "crawl_tasks",
    descEn: "Manage ingestion jobs and inspect workbook logs.",
    descZh: "管理采集任务并查看工作簿日志。",
    icon: Download,
    color: "teal",
  },
  {
    href: "/sources/flow",
    titleKey: "flow_nav_label",
    descEn: "Track fetch, check, process, and finalize by source.",
    descZh: "按数据源追踪拉取、判断、入库和收尾。",
    icon: GitBranch,
    color: "violet",
  },
  {
    href: "/data/diseases",
    titleKey: "diseases",
    descEn: "Explore disease-level records and comparisons.",
    descZh: "查看疾病维度数据并进行对比。",
    icon: Database,
    color: "cyan",
  },
  {
    href: "/data/quality",
    titleKey: "quality",
    descEn: "Check completeness, gaps, and source consistency.",
    descZh: "检查完整性、时间缺口和来源一致性。",
    icon: ShieldCheck,
    color: "amber",
  },
  {
    href: "/reports",
    titleKey: "reports",
    descEn: "Review generated report outputs and statuses.",
    descZh: "查看报告结果和发布状态。",
    icon: FileText,
    color: "rose",
  },
  {
    href: "/data/explorer",
    titleKey: "explorer",
    descEn: "Browse tables and run quick record lookups.",
    descZh: "浏览数据表并进行快速检索。",
    icon: Search,
    color: "slate",
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
    ? "rose"
    : isLoading
      ? "amber"
      : selectedCountry
        ? "emerald"
        : "slate";

  return (
    <div className="space-y-6">
      <section className="page-intro overflow-hidden rounded-[28px] border border-tremor-border bg-[linear-gradient(135deg,rgba(15,118,110,0.08),rgba(255,255,255,0.92)_45%,rgba(20,184,166,0.12))] p-6 shadow-sm md:p-8">
        <div className="grid gap-6 lg:grid-cols-[1.5fr_0.9fr]">
          <div className="space-y-4">
            <Badge color="teal" className="w-fit">
              GlobalID
            </Badge>
            <div className="space-y-2">
              <Title className="text-3xl md:text-4xl">{t(lang, "workspace_title")}</Title>
              <Text className="max-w-2xl text-base text-tremor-content">{t(lang, "workspace_subtitle")}</Text>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Card className="border-none bg-white/75 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <Text className="text-xs font-semibold uppercase tracking-[0.18em] text-tremor-content-subtle">
                      {t(lang, "active_country")}
                    </Text>
                    <p className="mt-2 text-2xl font-semibold text-tremor-content-strong">
                      {selectedCountryLabel || "-"}
                    </p>
                    <Text className="mt-2">
                      {selectedCountry?.timezone || t(lang, "active_country_hint")}
                    </Text>
                  </div>
                  <Badge color={countryStatusTone as never} className="shrink-0">
                    {countryCode || selectedCountry?.code || "--"}
                  </Badge>
                </div>
              </Card>

              <Card className="border-none bg-white/75 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <Text className="text-xs font-semibold uppercase tracking-[0.18em] text-tremor-content-subtle">
                      {t(lang, "recommended_next")}
                    </Text>
                    <p className="mt-2 text-lg font-semibold text-tremor-content-strong">
                      {t(lang, recommendedLink.titleKey)}
                    </p>
                    <Text className="mt-2">
                      {lang === "zh" ? recommendedLink.descZh : recommendedLink.descEn}
                    </Text>
                  </div>
                  <Sparkles className="mt-1 h-5 w-5 text-tremor-brand" />
                </div>
                <Link
                  href={recommendedLink.href}
                  className="mt-4 inline-flex items-center gap-2 rounded-full bg-tremor-brand px-4 py-2 text-sm font-semibold text-tremor-brand-inverted transition hover:opacity-90"
                >
                  {t(lang, "home_jump_to")}
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </Card>
            </div>
          </div>

          <Card className="border-none bg-white/80 shadow-sm">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-tremor-brand" />
              <Title>{t(lang, "system_status")}</Title>
            </div>
            <div className="mt-5 space-y-3">
              <div className="flex items-center justify-between rounded-2xl border border-tremor-border bg-tremor-background px-4 py-3">
                <div>
                  <p className="text-sm font-medium text-tremor-content-strong">{t(lang, "system_status_country")}</p>
                  <p className="text-xs text-tremor-content">
                    {error
                      ? t(lang, "system_status_country_unavailable")
                      : countries && countries.length > 0
                        ? t(lang, "system_status_country_ready")
                        : t(lang, "system_status_country_empty")}
                  </p>
                </div>
                <Badge color={countryStatusTone as never}>
                  {error
                    ? t(lang, "system_status_unavailable")
                    : countries && countries.length > 0
                      ? t(lang, "system_status_ready")
                      : t(lang, "system_status_partial")}
                </Badge>
              </div>
              <div className="rounded-2xl border border-dashed border-tremor-border px-4 py-4">
                <p className="text-sm font-medium text-tremor-content-strong">{t(lang, "recommended_next_subtitle")}</p>
                <p className="mt-2 text-sm text-tremor-content">
                  {selectedCountryLabel
                    ? `${selectedCountryLabel} · ${t(lang, "active_country_hint")}`
                    : t(lang, "home_empty_country")}
                </p>
              </div>
            </div>
          </Card>
        </div>
      </section>

      <section className="space-y-3">
        <div>
          <Title>{t(lang, "home_jump_to")}</Title>
          <Text>{selectedCountryLabel || t(lang, "home_empty_country")}</Text>
        </div>
      </section>

      <Grid numItems={1} numItemsSm={2} numItemsLg={3} className="gap-4">
        {quickLinks.map((item) => {
          const Icon = item.icon;
          const highlight = item.href === recommendedLink.href;
          return (
            <Link key={item.href} href={item.href} className="group block">
              <Card
                className={cn(
                  "h-full rounded-[24px] border border-tremor-border transition hover:-translate-y-0.5 hover:shadow-md",
                  highlight && "border-tremor-brand/40 bg-[linear-gradient(180deg,rgba(15,118,110,0.06),rgba(255,255,255,0.98))]",
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      {highlight ? <Badge color="teal">{t(lang, "recommended_next")}</Badge> : null}
                    </div>
                    <Text className="font-semibold text-tremor-content-strong">
                      {t(lang, item.titleKey)}
                    </Text>
                    <Text>{lang === "zh" ? item.descZh : item.descEn}</Text>
                  </div>
                  <Badge color={item.color as never} className="shrink-0">
                    <Icon className="h-4 w-4" />
                  </Badge>
                </div>
                <div className="mt-4 flex items-center text-sm text-tremor-brand">
                  {lang === "zh" ? "进入" : "Open"}
                  <ArrowRight className="ml-1 h-4 w-4 transition group-hover:translate-x-0.5" />
                </div>
              </Card>
            </Link>
          );
        })}
      </Grid>
    </div>
  );
}
