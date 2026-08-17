import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Bell,
  BookOpen,
  Bot,
  BrainCircuit,
  ChartNoAxesCombined,
  CircleGauge,
  Cpu,
  Database,
  FileSearch,
  FileText,
  GitBranch,
  HardDriveDownload,
  HeartPulse,
  Home,
  Mail,
  Microscope,
  Network,
  Rocket,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Waypoints,
} from "lucide-react";

import type { LangKey } from "@/lib/i18n";

export type RouteSectionId = "overview" | "operations" | "data" | "production" | "settings";
export type RouteStatus = "stable" | "beta" | "hidden";
export type CountryScope = "required" | "optional" | "none";

export interface RouteNode {
  id: string;
  href: string;
  label: string;
  labelKey: LangKey;
  description: string;
  icon: LucideIcon;
  section: RouteSectionId;
  status?: RouteStatus;
  countryScope: CountryScope;
}

export interface RouteSection {
  id: RouteSectionId;
  step: number;
  href: string;
  title: string;
  titleKey: LangKey;
  description: string;
  descriptionKey: LangKey;
  icon: LucideIcon;
  items: RouteNode[];
}

export const homeRoute: RouteNode = {
  id: "overview.home",
  href: "/overview",
  label: "Overview",
  labelKey: "overview",
  description: "Runtime health, action items, and recent activity",
  icon: Home,
  section: "overview",
  countryScope: "optional",
};

export const navigationSections: RouteSection[] = [
  {
    id: "overview",
    step: 1,
    href: "/overview",
    title: "Overview",
    titleKey: "overview",
    description: "Health, action items, and signals",
    descriptionKey: "workspace_subtitle",
    icon: CircleGauge,
    items: [
      homeRoute,
      { id: "overview.events", href: "/overview/events", label: "Events & Signals", labelKey: "overview", description: "Situation candidates and operational signals", icon: HeartPulse, section: "overview", countryScope: "none" },
    ],
  },
  {
    id: "operations",
    step: 2,
    href: "/operations/sources",
    title: "Ingestion & Tasks",
    titleKey: "mod_sources",
    description: "Sources, task runs, schedules, and runtime",
    descriptionKey: "nav_operations_desc",
    icon: HardDriveDownload,
    items: [
      { id: "operations.sources", href: "/operations/sources", label: "Sources", labelKey: "flow_nav_label", description: "Source flow and latest ingestion state", icon: GitBranch, section: "operations", countryScope: "optional" },
      { id: "operations.tasks", href: "/operations/tasks", label: "Task Runs", labelKey: "tasks", description: "All background work and execution logs", icon: Activity, section: "operations", countryScope: "optional" },
      { id: "operations.schedules", href: "/operations/schedules", label: "Schedules", labelKey: "automation", description: "Automated ingestion and publishing jobs", icon: SlidersHorizontal, section: "operations", countryScope: "none" },
      { id: "operations.runtime", href: "/operations/runtime", label: "Runtime", labelKey: "settings", description: "API, scheduler, and worker health", icon: Network, section: "operations", countryScope: "none" },
    ],
  },
  {
    id: "data",
    step: 3,
    href: "/data/analytics",
    title: "Data Governance",
    titleKey: "mod_database",
    description: "Analytics, quality, catalogue, and knowledge",
    descriptionKey: "nav_data_desc",
    icon: Database,
    items: [
      { id: "data.analytics", href: "/data/analytics", label: "Country Analytics", labelKey: "dashboard", description: "Country KPIs and surveillance trends", icon: ChartNoAxesCombined, section: "data", countryScope: "required" },
      { id: "data.diseases", href: "/data/diseases", label: "Diseases & Series", labelKey: "diseases", description: "Disease records and surveillance series", icon: Microscope, section: "data", countryScope: "required" },
      { id: "data.quality", href: "/data/quality", label: "Quality", labelKey: "quality", description: "Completeness, gaps, and provenance", icon: ShieldCheck, section: "data", countryScope: "required" },
      { id: "data.explorer", href: "/data/explorer", label: "Explorer", labelKey: "explorer", description: "Allowlisted database catalogue", icon: Search, section: "data", countryScope: "none" },
      { id: "data.knowledge", href: "/data/knowledge", label: "Knowledge", labelKey: "knowledge_base", description: "Reviewed disease briefs and evidence", icon: BookOpen, section: "data", countryScope: "none" },
      { id: "data.mappings", href: "/data/mappings", label: "Disease Mapping", labelKey: "disease_mapping", description: "Source taxonomy mapping and releases", icon: Waypoints, section: "data", countryScope: "none" },
      { id: "data.audit", href: "/data/audit", label: "Disease Audit", labelKey: "disease_audit", description: "Duplicates and new disease candidates", icon: FileSearch, section: "data", countryScope: "none" },
    ],
  },
  {
    id: "production",
    step: 4,
    href: "/production/ai",
    title: "AI & Reports",
    titleKey: "mod_ai",
    description: "Generation, review, publishing, and distribution",
    descriptionKey: "nav_ai_desc",
    icon: BrainCircuit,
    items: [
      { id: "production.ai", href: "/production/ai", label: "AI Generation", labelKey: "ai_tasks", description: "Create analysis and knowledge tasks", icon: Bot, section: "production", countryScope: "required" },
      { id: "production.runs", href: "/production/runs", label: "Agent Runs", labelKey: "agent_runs", description: "Agent workflow execution and evidence", icon: GitBranch, section: "production", countryScope: "optional" },
      { id: "production.interactions", href: "/production/interactions", label: "Interactions", labelKey: "ai_interactions", description: "Prompts, responses, tokens, and quality", icon: Cpu, section: "production", countryScope: "optional" },
      { id: "production.reports", href: "/production/reports", label: "Reports", labelKey: "reports", description: "Generated reports and review state", icon: FileText, section: "production", countryScope: "required" },
      { id: "production.research", href: "/production/research", label: "Research Radar", labelKey: "knowledge_base", description: "Literature synchronization and editorial review", icon: BookOpen, section: "production", countryScope: "none" },
      { id: "production.releases", href: "/production/releases", label: "Data Releases", labelKey: "data_release", description: "Release checks and deployment jobs", icon: Rocket, section: "production", countryScope: "none" },
      { id: "production.distribution", href: "/production/distribution", label: "Subscriptions", labelKey: "subscriptions", description: "Audience and delivery preferences", icon: Mail, section: "production", countryScope: "none" },
      { id: "production.campaigns", href: "/production/campaigns", label: "Campaigns", labelKey: "notifications", description: "Notification campaigns and delivery status", icon: Bell, section: "production", countryScope: "none" },
    ],
  },
  {
    id: "settings",
    step: 5,
    href: "/settings/integrations",
    title: "Settings",
    titleKey: "mod_settings",
    description: "Integrations, AI models, and runtime defaults",
    descriptionKey: "nav_admin_desc",
    icon: Settings2,
    items: [
      { id: "settings.integrations", href: "/settings/integrations", label: "Integrations", labelKey: "settings", description: "SMTP, GitHub, Cloudflare, and site settings", icon: Settings2, section: "settings", countryScope: "none" },
      { id: "settings.models", href: "/settings/models", label: "AI Providers & Models", labelKey: "ai_models", description: "Provider credentials and runtime routes", icon: Cpu, section: "settings", countryScope: "none" },
    ],
  },
];

export const visibleNavigationSections = navigationSections.map((section) => ({
  ...section,
  items: section.items.filter((item) => item.status !== "hidden"),
}));

export const allRoutes = navigationSections.flatMap((section) => section.items);

export function findRouteByPath(pathname: string): RouteNode | undefined {
  return allRoutes
    .filter((route) => pathname === route.href || pathname.startsWith(`${route.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0];
}

export function findSectionByPath(pathname: string): RouteSection {
  const route = findRouteByPath(pathname);
  return navigationSections.find((section) => section.id === route?.section) ?? navigationSections[0];
}
