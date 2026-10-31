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

export type RouteSectionId = "overview" | "collection" | "data" | "models" | "publish" | "settings";
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
  { id: "overview", step: 1, href: "/overview", title: "Overview", titleKey: "overview", description: "Overview", descriptionKey: "workspace_subtitle", icon: CircleGauge, items: [
    { id: "overview.home", href: "/overview", label: "Overview", labelKey: "overview", description: "Overview", icon: Home, section: "overview", countryScope: "optional" },
    { id: "overview.events", href: "/overview/events", label: "Events & Signals", labelKey: "cc_events", description: "Events & Signals", icon: HeartPulse, section: "overview", countryScope: "none" },
  ] },
  { id: "collection", step: 2, href: "/collection/sources/ingestion", title: "Ingestion & Tasks", titleKey: "wf_collection", description: "Ingestion & Tasks", descriptionKey: "wf_collection_desc", icon: HardDriveDownload, items: [
    { id: "collection.situation", href: "/collection/sources/situation", label: "Source - Situation", labelKey: "wf_situation", description: "Source - Situation", icon: HeartPulse, section: "collection", countryScope: "none" },
    { id: "collection.ingestion", href: "/collection/sources/ingestion", label: "Source - Ingestion", labelKey: "wf_ingestion", description: "Source - Ingestion", icon: GitBranch, section: "collection", countryScope: "required" },
    { id: "collection.literature", href: "/collection/sources/literature", label: "Source - Literature", labelKey: "wf_literature", description: "Source - Literature", icon: BookOpen, section: "collection", countryScope: "none" },
    { id: "collection.knowledge", href: "/collection/sources/knowledge", label: "Source - Knowledge", labelKey: "wf_knowledge", description: "Source - Knowledge", icon: Database, section: "collection", countryScope: "none" },
    { id: "collection.runs", href: "/collection/runs", label: "Tasks", labelKey: "tasks", description: "Tasks", icon: Activity, section: "collection", countryScope: "none" },
    { id: "collection.schedules", href: "/collection/schedules", label: "Schedules", labelKey: "cc_schedules", description: "Schedules", icon: SlidersHorizontal, section: "collection", countryScope: "none" },
    { id: "collection.runtime", href: "/collection/runtime", label: "Runtime", labelKey: "cc_runtime", description: "Runtime", icon: Network, section: "collection", countryScope: "none" },
  ] },
  { id: "data", step: 3, href: "/data", title: "Data Governance", titleKey: "wf_governance", description: "Data Governance", descriptionKey: "wf_governance_desc", icon: ShieldCheck, items: [
    { id: "data.home", href: "/data", label: "Governance workbench", labelKey: "wf_governance_home", description: "Governance workbench", icon: ShieldCheck, section: "data", countryScope: "optional" },
    { id: "data.quality", href: "/data/quality", label: "Quality & completeness", labelKey: "wf_quality", description: "Quality & completeness", icon: ShieldCheck, section: "data", countryScope: "required" },
    { id: "data.mappings", href: "/data/mappings", label: "Mapping & classification", labelKey: "wf_mapping", description: "Mapping & classification", icon: Waypoints, section: "data", countryScope: "none" },
    { id: "data.audit", href: "/data/audit", label: "Duplicate review", labelKey: "wf_duplicates", description: "Duplicate review", icon: FileSearch, section: "data", countryScope: "none" },
    { id: "data.analytics", href: "/data/analytics", label: "Jurisdiction Analytics", labelKey: "dashboard", description: "Jurisdiction Analytics", icon: ChartNoAxesCombined, section: "data", countryScope: "required" },
    { id: "data.diseases", href: "/data/diseases", label: "Diseases & Series", labelKey: "diseases", description: "Diseases & Series", icon: Microscope, section: "data", countryScope: "required" },
    { id: "data.explorer", href: "/data/explorer", label: "Data inspection", labelKey: "wf_inspection", description: "Data inspection", icon: Search, section: "data", countryScope: "none" },
    { id: "data.knowledge", href: "/data/knowledge", label: "Knowledge evidence", labelKey: "wf_catalogue", description: "Knowledge evidence", icon: BookOpen, section: "data", countryScope: "none" },
    { id: "data.literature", href: "/data/literature", label: "Literature evidence", labelKey: "wf_research", description: "Literature evidence", icon: BookOpen, section: "data", countryScope: "none" },
  ] },
  { id: "models", step: 4, href: "/models", title: "AI", titleKey: "wf_ai", description: "AI", descriptionKey: "wf_ai_desc", icon: BrainCircuit, items: [
    { id: "models.home", href: "/models", label: "AI workbench", labelKey: "wf_ai_home", description: "AI workbench", icon: BrainCircuit, section: "models", countryScope: "optional" },
    { id: "models.tasks", href: "/models/tasks", label: "Tasks", labelKey: "tasks", description: "Tasks", icon: Activity, section: "models", countryScope: "none" },
    { id: "models.queue", href: "/models/queue", label: "Routes & model queue", labelKey: "wf_queue", description: "Routes & model queue", icon: Cpu, section: "models", countryScope: "none" },
    { id: "models.agents", href: "/models/agents", label: "Agent workflows", labelKey: "wf_agents", description: "Agent workflows", icon: Bot, section: "models", countryScope: "none" },
    { id: "models.reports", href: "/models/reports", label: "Report production", labelKey: "wf_reports", description: "Report production", icon: FileText, section: "models", countryScope: "required" },
    { id: "models.knowledge", href: "/models/knowledge", label: "Knowledge briefs", labelKey: "wf_briefs", description: "Knowledge briefs", icon: BookOpen, section: "models", countryScope: "none" },
    { id: "models.literature", href: "/models/literature", label: "Literature enrichment", labelKey: "wf_literature_ai", description: "Literature enrichment", icon: BookOpen, section: "models", countryScope: "none" },
    { id: "models.interactions", href: "/models/interactions", label: "Interactions", labelKey: "ai_interactions", description: "Interactions", icon: Cpu, section: "models", countryScope: "optional" },
  ] },
  { id: "publish", step: 5, href: "/publish", title: "Publish", titleKey: "publish", description: "Publish", descriptionKey: "nav_publish_desc", icon: Rocket, items: [
    { id: "publish.home", href: "/publish", label: "Site Release", labelKey: "publish", description: "Site Release", icon: Rocket, section: "publish", countryScope: "none" },
    { id: "publish.tasks", href: "/publish/tasks", label: "Release tasks", labelKey: "wf_release_tasks", description: "Release tasks", icon: Activity, section: "publish", countryScope: "none" },
    { id: "publish.schedules", href: "/publish/schedules", label: "Release schedules", labelKey: "wf_release_schedules", description: "Release schedules", icon: SlidersHorizontal, section: "publish", countryScope: "none" },
    { id: "publish.distribution", href: "/production/distribution", label: "Subscriptions", labelKey: "subscriptions", description: "Subscriptions", icon: Mail, section: "publish", countryScope: "none" },
    { id: "publish.campaigns", href: "/production/campaigns", label: "Campaigns", labelKey: "notifications", description: "Campaigns", icon: Bell, section: "publish", countryScope: "none" },
  ] },
  { id: "settings", step: 6, href: "/settings", title: "Settings", titleKey: "settings", description: "Settings", descriptionKey: "wf_settings_desc", icon: Settings2, items: [
    { id: "settings.home", href: "/settings", label: "Configuration overview", labelKey: "wf_settings_home", description: "Configuration overview", icon: Settings2, section: "settings", countryScope: "none" },
    { id: "settings.integrations", href: "/settings/integrations", label: "Integrations", labelKey: "cc_integrations", description: "Integrations", icon: Settings2, section: "settings", countryScope: "none" },
    { id: "settings.models", href: "/settings/models", label: "Providers & model configuration", labelKey: "wf_providers", description: "Providers & model configuration", icon: Cpu, section: "settings", countryScope: "none" },
  ] },
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
