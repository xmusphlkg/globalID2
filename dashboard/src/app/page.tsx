"use client";

import Link from "next/link";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { ArrowRight, BarChart3, Database, Download, FileText, GitBranch, Search, ShieldCheck } from "lucide-react";
import { Badge, Card, Grid, Text, Title } from "@tremor/react";

const quickLinks = [
  {
    href: "/data/dashboard",
    titleKey: "dashboard",
    descEn: "Core disease metrics and trend analytics.",
    descZh: "核心疫情指标与趋势分析。",
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
    descEn: "Track fetch -> check -> process -> finalize by source.",
    descZh: "按数据源跟踪 拉取 -> 增量判断 -> 入库 -> 收尾。",
    icon: GitBranch,
    color: "violet",
  },
  {
    href: "/data/diseases",
    titleKey: "diseases",
    descEn: "Explore disease-level records and comparisons.",
    descZh: "查看疾病维度数据与对比分析。",
    icon: Database,
    color: "cyan",
  },
  {
    href: "/data/quality",
    titleKey: "quality",
    descEn: "Check completeness, gaps, and source consistency.",
    descZh: "检查完整性、时间缺口与来源一致性。",
    icon: ShieldCheck,
    color: "amber",
  },
  {
    href: "/reports",
    titleKey: "reports",
    descEn: "Review generated report outputs and statuses.",
    descZh: "查看报告生成结果与发布状态。",
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
  const { lang, countryName } = useAppStore();

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-2">
        <Badge color="blue" className="w-fit">GlobalID</Badge>
        <Title className="text-3xl">
          {lang === "zh" ? "数据监测工作台" : "Disease Monitoring Workspace"}
        </Title>
        <Text>
          {lang === "zh"
            ? `当前国家: ${countryName || "-"}。选择一个入口开始。`
            : `Current country: ${countryName || "-"}. Choose an entry point to continue.`}
        </Text>
      </div>

      <Grid numItems={1} numItemsSm={2} numItemsLg={3} className="gap-4">
        {quickLinks.map((item) => {
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} className="group block">
              <Card className="h-full transition hover:shadow-md">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <Text className="font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                      {t(lang, item.titleKey)}
                    </Text>
                    <Text>{lang === "zh" ? item.descZh : item.descEn}</Text>
                  </div>
                  <Badge color={item.color as any} className="shrink-0">
                    <Icon className="h-4 w-4" />
                  </Badge>
                </div>
                <div className="mt-4 flex items-center text-sm text-tremor-brand dark:text-dark-tremor-brand">
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
