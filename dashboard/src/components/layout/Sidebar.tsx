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
    ],
  },
  {
    step: 2,
    titleKey: "mod_database",
    icon: Database,
    items: [
      { href: "/data/dashboard", labelKey: "dashboard", icon: BarChart3 },
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

export function Sidebar({ mobileOpen, onClose }: { mobileOpen?: boolean; onClose?: () => void }) {
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
    <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-tremor-background dark:bg-dark-tremor-background border-r border-tremor-border dark:border-dark-tremor-border px-6 pb-4">
      <div className="flex h-16 shrink-0 items-center">
         <span className="text-xl font-bold text-tremor-brand dark:text-dark-tremor-brand tracking-tight">GlobalID</span>
      </div>
      <nav className="flex flex-1 flex-col">
        <ul role="list" className="flex flex-1 flex-col gap-y-7">
          <li>
            <ul role="list" className="-mx-2 space-y-1">
              {topLevelItems.map((item) => {
                const active = isActive(item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onClose}
                      className={cn(
                        active
                          ? "bg-tremor-brand-muted/20 text-tremor-brand dark:bg-dark-tremor-brand-muted/20 dark:text-dark-tremor-brand font-medium"
                          : "text-tremor-content hover:bg-tremor-background-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle dark:hover:text-dark-tremor-content-strong",
                        "group flex gap-x-3 rounded-tremor-default p-2 text-sm leading-6 transition-colors"
                      )}
                    >
                      <item.icon
                        className={cn(
                          active ? "text-tremor-brand dark:text-dark-tremor-brand" : "text-tremor-content-subtle group-hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:group-hover:text-dark-tremor-content-strong",
                          "h-5 w-5 shrink-0"
                        )}
                        aria-hidden="true"
                      />
                      {t(lang, item.labelKey)}
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
                <div className="text-xs font-semibold leading-6 text-tremor-content-subtle dark:text-dark-tremor-content-subtle uppercase tracking-wider flex items-center gap-2 mb-2">
                  <span className={cn("flex h-5 w-5 items-center justify-center rounded-full text-[10px]", groupActive ? "bg-tremor-brand text-tremor-brand-inverted dark:bg-dark-tremor-brand dark:text-dark-tremor-brand-inverted" : "bg-tremor-background-muted text-tremor-content dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content" )}>
                    {group.step}
                  </span>
                  {t(lang, group.titleKey)}
                </div>
                <ul role="list" className="-mx-2 space-y-1">
                  {group.items.map((item) => {
                    const active = isActive(item.href);
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          onClick={onClose}
                          className={cn(
                            active
                              ? "bg-tremor-brand-muted/20 text-tremor-brand dark:bg-dark-tremor-brand-muted/20 dark:text-dark-tremor-brand font-medium"
                              : "text-tremor-content hover:bg-tremor-background-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle dark:hover:text-dark-tremor-content-strong",
                            "group flex gap-x-3 rounded-tremor-default p-2 text-sm leading-6 transition-colors"
                          )}
                        >
                          <item.icon
                            className={cn(
                              active ? "text-tremor-brand dark:text-dark-tremor-brand" : "text-tremor-content-subtle group-hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:group-hover:text-dark-tremor-content-strong",
                              "h-5 w-5 shrink-0"
                            )}
                            aria-hidden="true"
                          />
                          {t(lang, item.labelKey)}
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
          <div className="fixed inset-0 bg-tremor-background-emphasis/80 dark:bg-dark-tremor-background-emphasis/80 transition-opacity" onClick={onClose} />
          <div className="fixed inset-0 flex">
            <div className="relative mr-16 flex w-full max-w-xs flex-1">
              <div className="absolute left-full top-0 flex w-16 justify-center pt-5">
                <button type="button" className="-m-2.5 p-2.5" onClick={onClose}>
                  <span className="sr-only">Close sidebar</span>
                  <X className="h-6 w-6 text-tremor-brand-inverted dark:text-dark-tremor-brand-inverted" aria-hidden="true" />
                </button>
              </div>
              {SidebarContent}
            </div>
          </div>
        </div>
      )}

      {/* Desktop Sidebar */}
      <div className="hidden lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:w-72 lg:flex-col">
        {SidebarContent}
      </div>
    </>
  );
}
