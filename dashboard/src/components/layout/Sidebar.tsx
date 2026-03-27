"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Home,
  Activity,
  FileText,
  Database,
  ShieldCheck,
  Download,
  Cpu,
  Settings2,
  Send,
  Search as SearchIcon,
  GitBranch,
  X,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { t, type LangKey } from "@/lib/i18n";

interface NavGroup {
  step: number;
  titleKey: LangKey;
  icon: React.ElementType;
  items: { href: string; labelKey: LangKey; icon: React.ElementType }[];
}

const topLevelItems: NavGroup["items"] = [{ href: "/", labelKey: "home", icon: Home }];

const navGroups: NavGroup[] = [
  {
    step: 1,
    titleKey: "mod_sources",
    icon: Download,
    items: [
      { href: "/sources/flow", labelKey: "flow_nav_label", icon: GitBranch },
      { href: "/sources/tasks", labelKey: "crawl_tasks", icon: Download },
      { href: "/sources/automation", labelKey: "automation", icon: Settings2 },
    ],
  },
  {
    step: 2,
    titleKey: "mod_database",
    icon: Database,
    items: [
      { href: "/data/dashboard", labelKey: "dashboard", icon: BarChart3 },
      { href: "/data/release", labelKey: "data_release", icon: Send },
      { href: "/data/diseases", labelKey: "diseases", icon: Activity },
      { href: "/data/explorer", labelKey: "explorer", icon: SearchIcon },
      { href: "/data/quality", labelKey: "quality", icon: ShieldCheck },
    ],
  },
  {
    step: 3,
    titleKey: "mod_ai",
    icon: Cpu,
    items: [
      { href: "/ai/models", labelKey: "ai_models", icon: Settings2 },
      { href: "/ai/tasks", labelKey: "ai_tasks", icon: Cpu },
      { href: "/ai/interactions", labelKey: "ai_interactions", icon: SearchIcon },
    ],
  },
  {
    step: 4,
    titleKey: "mod_results",
    icon: Send,
    items: [
      { href: "/reports", labelKey: "reports", icon: FileText },
    ],
  },
];

export function Sidebar({
  mobileOpen,
  onClose,
  collapsed = false,
}: {
  mobileOpen?: boolean;
  onClose?: () => void;
  collapsed?: boolean;
}) {
  const path = usePathname();
  const { lang } = useAppStore();

  const isActive = (href: string) => {
    if (href === "/data/dashboard") return path === "/data/dashboard";
    if (href === "/ai/tasks") return path === "/ai" || path === "/ai/tasks";
    // For general routes, match exactly
    if (path === href) return true;
    
    // For nested routes, make sure we only highlight the parent if we're in a sub-path
    // that ISN'T explicitly listed in the sidebar.
    // e.g. /sources/flow is its own item, so /sources shouldn't be active for it.
    const isPrefixMatch = path.startsWith(`${href}/`);
    const isExplicitlyHandledChild = navGroups.some(g => 
      g.items.some(i => i.href !== href && path.startsWith(i.href))
    );
    
    return isPrefixMatch && !isExplicitlyHandledChild;
  };

  const SidebarContent = (
    <div className={`flex grow flex-col gap-y-4 overflow-y-auto border-r border-tremor-border bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(243,247,245,0.95))] pb-6 ${collapsed ? "px-2.5" : "px-6"}`}>
      <div className={`flex shrink-0 flex-col gap-3 pt-6 ${collapsed ? "items-center" : ""}`}>
        <div>
          <span className={`font-bold tracking-tight text-tremor-brand ${collapsed ? "text-base" : "text-xl"}`}>GlobalID</span>
          {!collapsed ? <p className="mt-1 text-sm text-tremor-content">{t(lang, "workspace_subtitle")}</p> : null}
        </div>
        {!collapsed ? (
          <div className="rounded-2xl border border-tremor-border bg-tremor-background/80 p-3 shadow-sm">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-tremor-content-subtle">
              {t(lang, "nav_overview")}
            </p>
            <p className="mt-1 text-sm font-medium text-tremor-content-strong">
              {t(lang, "home")}
            </p>
            <p className="mt-1 text-xs text-tremor-content">{t(lang, "active_country_hint")}</p>
          </div>
        ) : null}
      </div>
      <nav className="flex flex-1 flex-col">
        <ul role="list" className={`flex flex-1 flex-col ${collapsed ? "gap-y-4" : "gap-y-7"}`}>
          <li>
            <ul role="list" className={cn(collapsed ? "space-y-1.5" : "-mx-2 space-y-1")}>
              {topLevelItems.map((item) => {
                const active = isActive(item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      title={t(lang, item.labelKey)}
                      onClick={onClose}
                      className={cn(
                        active
                          ? "bg-tremor-brand text-tremor-brand-inverted font-medium shadow-sm"
                          : "text-tremor-content hover:bg-tremor-background hover:text-tremor-content-strong",
                        collapsed
                          ? "group flex h-11 w-11 items-center justify-center rounded-2xl text-sm leading-6 transition-colors mx-auto"
                          : "group flex items-center gap-x-3 rounded-xl p-2.5 text-sm leading-6 transition-colors"
                      )}
                    >
                      <item.icon
                        className={cn(
                          active ? "text-tremor-brand-inverted" : "text-tremor-content-subtle group-hover:text-tremor-content-strong",
                          "h-5 w-5 shrink-0"
                        )}
                        aria-hidden="true"
                      />
                      {!collapsed ? <span>{t(lang, item.labelKey)}</span> : null}
                      {!collapsed ? <ChevronRight className={cn("ml-auto h-4 w-4 transition", active ? "opacity-100" : "opacity-0 group-hover:opacity-60")} /> : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </li>
          {navGroups.map((group) => {
            const groupActive = group.items.some((item) => isActive(item.href));

            return (
              <li key={group.step}>
                {collapsed ? (
                  <div className="mb-2 flex justify-center">
                    <span
                      title={t(lang, group.titleKey)}
                      className={cn(
                        "flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-semibold",
                        groupActive ? "bg-tremor-brand text-tremor-brand-inverted" : "bg-tremor-background-muted text-tremor-content",
                      )}
                    >
                      {group.step}
                    </span>
                  </div>
                ) : (
                  <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-tremor-content-subtle">
                    <span className={cn("flex h-5 w-5 items-center justify-center rounded-full text-[10px]", groupActive ? "bg-tremor-brand text-tremor-brand-inverted" : "bg-tremor-background-muted text-tremor-content" )}>
                      {group.step}
                    </span>
                    {t(lang, group.titleKey)}
                  </div>
                )}
                <ul role="list" className={cn(collapsed ? "space-y-1.5" : "-mx-2 space-y-1")}>
                  {group.items.map((item) => {
                    const active = isActive(item.href);
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          title={t(lang, item.labelKey)}
                          onClick={onClose}
                          className={cn(
                            active
                              ? "bg-tremor-brand text-tremor-brand-inverted font-medium shadow-sm"
                              : "text-tremor-content hover:bg-tremor-background hover:text-tremor-content-strong",
                            collapsed
                              ? "group flex h-11 w-11 items-center justify-center rounded-2xl text-sm leading-6 transition-colors mx-auto"
                              : "group flex items-center gap-x-3 rounded-xl p-2.5 text-sm leading-6 transition-colors"
                          )}
                        >
                          <item.icon
                            className={cn(
                              active ? "text-tremor-brand-inverted" : "text-tremor-content-subtle group-hover:text-tremor-content-strong",
                              "h-5 w-5 shrink-0"
                            )}
                            aria-hidden="true"
                          />
                          {!collapsed ? <span>{t(lang, item.labelKey)}</span> : null}
                          {!collapsed ? <ChevronRight className={cn("ml-auto h-4 w-4 transition", active ? "opacity-100" : "opacity-0 group-hover:opacity-60")} /> : null}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );

  return (
    <>
      {/* Mobile Sidebar */}
      {mobileOpen && (
        <div className="relative z-50 lg:hidden" role="dialog" aria-modal="true">
          <div className="fixed inset-0 bg-tremor-background-emphasis/60 transition-opacity" onClick={onClose} />
          <div className="fixed inset-0 flex">
            <div className="relative mr-16 flex w-full max-w-xs flex-1">
              <div className="absolute left-full top-0 flex w-16 justify-center pt-5">
                <button type="button" className="-m-2.5 p-2.5" onClick={onClose}>
                  <span className="sr-only">Close sidebar</span>
                  <X className="h-6 w-6 text-tremor-brand-inverted" aria-hidden="true" />
                </button>
              </div>
              {SidebarContent}
            </div>
          </div>
        </div>
      )}

      {/* Desktop Sidebar */}
      <div className={`hidden lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:flex-col ${collapsed ? "lg:w-24" : "lg:w-72"}`}>
        {SidebarContent}
      </div>
    </>
  );
}
