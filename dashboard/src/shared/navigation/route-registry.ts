import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  Bell,
  BookOpen,
  Cpu,
  Database,
  Download,
  FileText,
  GitBranch,
  Home,
  Mail,
  Search,
  Send,
  Settings2,
  ShieldCheck,
} from "lucide-react";

import type { LangKey } from "@/lib/i18n";

export type RouteSectionId = "operations" | "data" | "ai" | "results" | "admin";
export type RouteStatus = "stable" | "beta" | "hidden";
export type CountryScope = "required" | "optional" | "none";

export interface RouteNode {
  id: string;
  href: string;
  labelKey: LangKey;
  icon: LucideIcon;
  section: RouteSectionId;
  status?: RouteStatus;
  countryScope: CountryScope;
}

export interface RouteSection {
  id: RouteSectionId;
  step: number;
  titleKey: LangKey;
  descriptionKey: LangKey;
  icon: LucideIcon;
  items: RouteNode[];
}

export const homeRoute: RouteNode = {
  id: "home",
  href: "/",
  labelKey: "home",
  icon: Home,
  section: "operations",
  countryScope: "optional",
};

export const navigationSections: RouteSection[] = [
  {
    id: "operations",
    step: 1,
    titleKey: "mod_sources",
    descriptionKey: "nav_operations_desc",
    icon: Download,
    items: [
      { id: "operations.flow", href: "/sources/flow", labelKey: "flow_nav_label", icon: GitBranch, section: "operations", countryScope: "optional" },
      { id: "operations.crawlTasks", href: "/sources/tasks", labelKey: "crawl_tasks", icon: Download, section: "operations", countryScope: "optional" },
      { id: "operations.automation", href: "/sources/automation", labelKey: "automation", icon: Settings2, section: "operations", countryScope: "none" },
      { id: "operations.allTasks", href: "/tasks", labelKey: "tasks", icon: Activity, section: "operations", status: "hidden", countryScope: "optional" },
    ],
  },
  {
    id: "data",
    step: 2,
    titleKey: "mod_database",
    descriptionKey: "nav_data_desc",
    icon: Database,
    items: [
      { id: "data.overview", href: "/data/dashboard", labelKey: "dashboard", icon: BarChart3, section: "data", countryScope: "required" },
      { id: "data.diseases", href: "/data/diseases", labelKey: "diseases", icon: Activity, section: "data", countryScope: "required" },
      { id: "data.quality", href: "/data/quality", labelKey: "quality", icon: ShieldCheck, section: "data", countryScope: "required" },
      { id: "data.explorer", href: "/data/explorer", labelKey: "explorer", icon: Search, section: "data", countryScope: "none" },
      { id: "data.knowledge", href: "/data/knowledge", labelKey: "knowledge_base", icon: BookOpen, section: "data", countryScope: "none" },
    ],
  },
  {
    id: "ai",
    step: 3,
    titleKey: "mod_ai",
    descriptionKey: "nav_ai_desc",
    icon: Cpu,
    items: [
      { id: "ai.tasks", href: "/ai/tasks", labelKey: "ai_tasks", icon: Cpu, section: "ai", countryScope: "required" },
      { id: "ai.agentRuns", href: "/ai/agent-runs", labelKey: "agent_runs", icon: GitBranch, section: "ai", countryScope: "optional" },
      { id: "ai.interactions", href: "/ai/interactions", labelKey: "ai_interactions", icon: Search, section: "ai", countryScope: "optional" },
      { id: "ai.diseaseAudit", href: "/ai/disease-audit", labelKey: "disease_audit", icon: ShieldCheck, section: "ai", countryScope: "none" },
      { id: "ai.models", href: "/ai/models", labelKey: "ai_models", icon: Settings2, section: "ai", countryScope: "none" },
    ],
  },
  {
    id: "results",
    step: 4,
    titleKey: "mod_results",
    descriptionKey: "nav_publishing_desc",
    icon: Send,
    items: [
      { id: "results.release", href: "/data/release", labelKey: "data_release", icon: Send, section: "results", countryScope: "none" },
      { id: "results.reports", href: "/reports", labelKey: "reports", icon: FileText, section: "results", countryScope: "required" },
      { id: "results.subscriptions", href: "/subscriptions", labelKey: "subscriptions", icon: Mail, section: "results", countryScope: "none" },
      { id: "results.notifications", href: "/subscriptions/notifications", labelKey: "notifications", icon: Bell, section: "results", countryScope: "none" },
    ],
  },
  {
    id: "admin",
    step: 5,
    titleKey: "mod_settings",
    descriptionKey: "nav_admin_desc",
    icon: Settings2,
    items: [
      { id: "admin.settings", href: "/setting", labelKey: "settings", icon: Settings2, section: "admin", countryScope: "none" },
    ],
  },
];

export const visibleNavigationSections = navigationSections.map((section) => ({
  ...section,
  items: section.items.filter((item) => item.status !== "hidden"),
}));

export const allRoutes = [
  homeRoute,
  ...navigationSections.flatMap((section) => section.items),
];

export function findRouteByPath(pathname: string): RouteNode | undefined {
  return allRoutes
    .filter((route) => pathname === route.href || pathname.startsWith(`${route.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0];
}
