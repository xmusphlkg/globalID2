"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  X,
  CircleDot,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { type CountryScope, homeRoute, visibleNavigationSections } from "@/shared/navigation/route-registry";

const topLevelItems = [homeRoute];
const navGroups = visibleNavigationSections;

function scopeLabel(lang: "en" | "zh", scope: CountryScope) {
  if (scope === "required") return t(lang, "nav_country_required");
  if (scope === "optional") return t(lang, "nav_country_optional");
  return t(lang, "nav_country_global");
}

function scopeClass(scope: CountryScope) {
  if (scope === "required") return "border-emerald-200/30 bg-emerald-300/[0.12] text-emerald-100";
  if (scope === "optional") return "border-blue-200/25 bg-blue-300/[0.12] text-blue-100";
  return "border-white/10 bg-white/[0.06] text-white/[0.55]";
}

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
    <div
      className={cn(
        "flex grow flex-col gap-y-4 overflow-y-auto border-r border-[#263a35] bg-[#17211f] pb-6 text-white shadow-[8px_0_24px_rgba(23,33,31,0.08)]",
        collapsed ? "px-2.5" : "px-5",
      )}
    >
      <div className={cn("flex shrink-0 flex-col gap-4 pt-5", collapsed ? "items-center" : "")}>
        <Link
          href="/"
          onClick={onClose}
          className={cn(
            "group flex items-center rounded-tremor-default transition hover:bg-white/[0.08]",
            collapsed ? "h-11 w-11 justify-center" : "gap-3 px-2 py-2",
          )}
          title={t(lang, "brand_name")}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-tremor-default bg-[#dbeee8] text-sm font-bold text-[#17352f]">
            G
          </span>
          {!collapsed ? (
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-white">{t(lang, "brand_name")}</span>
              <span className="block truncate text-xs text-white/[0.58]">{t(lang, "workspace_subtitle")}</span>
            </span>
          ) : null}
        </Link>

        {!collapsed ? (
          <div className="rounded-tremor-default border border-white/10 bg-white/[0.06] px-3 py-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] font-semibold uppercase text-white/[0.48]">{t(lang, "country_scope")}</p>
              <span className="flex h-2 w-2 rounded-full bg-emerald-300" />
            </div>
            <p className="mt-2 text-sm font-semibold text-white">{t(lang, "country_scope_optional")}</p>
            <p className="mt-1 text-xs leading-5 text-white/[0.58]">{t(lang, "active_country_hint")}</p>
          </div>
        ) : null}
      </div>
      <nav className="flex flex-1 flex-col">
        <ul role="list" className={`flex flex-1 flex-col ${collapsed ? "gap-y-4" : "gap-y-6"}`}>
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
                          ? "bg-white text-[#17211f] shadow-sm"
                          : "text-white/[0.72] hover:bg-white/[0.08] hover:text-white",
                        collapsed
                          ? "group mx-auto flex h-10 w-10 items-center justify-center rounded-tremor-default text-sm leading-6 transition-colors"
                          : "group flex items-center gap-x-3 rounded-tremor-default px-2.5 py-2.5 text-sm leading-6 transition-colors"
                      )}
                    >
                      <item.icon
                        className={cn(
                          active ? "text-[#0f6b62]" : "text-white/[0.46] group-hover:text-white",
                          "h-5 w-5 shrink-0"
                        )}
                        aria-hidden="true"
                      />
                      {!collapsed ? <span>{t(lang, item.labelKey)}</span> : null}
                      {!collapsed ? (
                        <span className={cn("ml-auto rounded-tremor-default border px-1.5 py-0.5 text-[10px] font-semibold", active ? "border-[#0f6b62]/20 bg-[#0f6b62]/10 text-[#0f6b62]" : scopeClass(item.countryScope))}>
                          {scopeLabel(lang, item.countryScope)}
                        </span>
                      ) : null}
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
                        "flex h-7 w-7 items-center justify-center rounded-tremor-default text-[10px] font-semibold",
                        groupActive ? "bg-[#dbeee8] text-[#17352f]" : "bg-white/[0.08] text-white/[0.48]",
                      )}
                    >
                      <group.icon className="h-3.5 w-3.5" />
                    </span>
                  </div>
                ) : (
                  <div className="mb-2 px-0.5">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase text-white/[0.50]">
                      <span className={cn("flex h-5 w-5 items-center justify-center rounded-tremor-default text-[10px]", groupActive ? "bg-[#dbeee8] text-[#17352f]" : "bg-white/[0.08] text-white/[0.48]" )}>
                        <group.icon className="h-3 w-3" />
                      </span>
                      {t(lang, group.titleKey)}
                      {groupActive ? <CircleDot className="ml-auto h-3.5 w-3.5 text-emerald-300" /> : null}
                    </div>
                    <p className="mt-1 pl-7 text-[11px] font-medium normal-case leading-4 text-white/[0.40]">
                      {t(lang, group.descriptionKey)}
                    </p>
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
                              ? "bg-white text-[#17211f] shadow-sm"
                              : "text-white/[0.72] hover:bg-white/[0.08] hover:text-white",
                            collapsed
                              ? "group mx-auto flex h-10 w-10 items-center justify-center rounded-tremor-default text-sm leading-6 transition-colors"
                              : "group flex items-center gap-x-3 rounded-tremor-default px-2.5 py-2.5 text-sm leading-6 transition-colors"
                          )}
                        >
                          <item.icon
                            className={cn(
                              active ? "text-[#0f6b62]" : "text-white/[0.46] group-hover:text-white",
                              "h-5 w-5 shrink-0"
                            )}
                            aria-hidden="true"
                          />
                          {!collapsed ? <span>{t(lang, item.labelKey)}</span> : null}
                          {!collapsed ? (
                            <span className={cn("ml-auto rounded-tremor-default border px-1.5 py-0.5 text-[10px] font-semibold", active ? "border-[#0f6b62]/20 bg-[#0f6b62]/10 text-[#0f6b62]" : scopeClass(item.countryScope))}>
                              {scopeLabel(lang, item.countryScope)}
                            </span>
                          ) : null}
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
      <div className={`hidden lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:flex-col ${collapsed ? "lg:w-24" : "lg:w-80"}`}>
        {SidebarContent}
      </div>
    </>
  );
}
