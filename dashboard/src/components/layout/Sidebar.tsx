"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { findSectionByPath, navigationSections } from "@/shared/navigation/route-registry";

export function Sidebar({
  mobileOpen,
  onClose,
  collapsed = false,
}: {
  mobileOpen?: boolean;
  onClose?: () => void;
  collapsed?: boolean;
}) {
  const pathname = usePathname();
  const activeSection = findSectionByPath(pathname);

  const content = (
    <div className={cn("flex h-full flex-col border-r border-[#D9D9D6] bg-white", collapsed ? "px-2" : "px-3")}>
      <div className={cn("flex h-16 items-center border-b border-[#ECECEA]", collapsed ? "justify-center" : "gap-3 px-2")}>
        <Link href="/overview" onClick={onClose} className="flex items-center gap-3" aria-label="GIDS Overview">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[#F48120] text-sm font-bold text-white shadow-sm">G</span>
          {!collapsed ? (
            <span className="min-w-0">
              <span className="block text-sm font-semibold tracking-tight text-[#1D1D1F]">GIDS</span>
              <span className="block truncate text-[11px] text-[#6B7280]">Control Center</span>
            </span>
          ) : null}
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto py-4" aria-label="Control center workspaces">
        <ul className="space-y-1">
          {navigationSections.map((section) => {
            const active = section.id === activeSection.id;
            const SectionIcon = section.icon;
            return (
              <li key={section.id}>
                <Link
                  href={section.href}
                  onClick={onClose}
                  title={section.title}
                  className={cn(
                    "group flex min-h-10 items-center rounded-md text-sm font-medium transition",
                    collapsed ? "justify-center px-2" : "gap-3 px-3",
                    active ? "bg-[#FFF3E8] text-[#9A3412]" : "text-[#4B5563] hover:bg-[#F7F7F5] hover:text-[#1D1D1F]",
                  )}
                >
                  <SectionIcon className={cn("h-[18px] w-[18px] shrink-0", active ? "text-[#C2410C]" : "text-[#6B7280]")} />
                  {!collapsed ? <span className="min-w-0 flex-1 truncate">{section.title}</span> : null}
                  {!collapsed && active ? <ChevronDown className="h-3.5 w-3.5 text-[#C2410C]" /> : null}
                </Link>

                {!collapsed && active ? (
                  <ul className="ml-[21px] mt-1 space-y-0.5 border-l border-[#E5E5E2] pl-3">
                    {section.items.map((item) => {
                      const itemActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
                      return (
                        <li key={item.id}>
                          <Link
                            href={item.href}
                            onClick={onClose}
                            className={cn(
                              "block rounded-md px-3 py-2 text-[13px] leading-5 transition",
                              itemActive
                                ? "bg-[#F7F7F5] font-semibold text-[#1D1D1F] shadow-[inset_2px_0_0_#F48120]"
                                : "text-[#6B7280] hover:bg-[#F7F7F5] hover:text-[#1D1D1F]",
                            )}
                          >
                            {item.label}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </nav>

      {!collapsed ? (
        <div className="mb-3 rounded-md border border-[#E5E5E2] bg-[#F7F7F5] px-3 py-3">
          <div className="flex items-center gap-2 text-xs font-medium text-[#374151]">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            Single administrator
          </div>
          <p className="mt-1.5 text-[11px] leading-4 text-[#6B7280]">All privileged API calls stay behind the same-origin control plane.</p>
        </div>
      ) : null}
    </div>
  );

  return (
    <>
      {mobileOpen ? (
        <div className="relative z-50 lg:hidden" role="dialog" aria-modal="true">
          <div className="fixed inset-0 bg-black/35" onClick={onClose} />
          <div className="fixed inset-y-0 left-0 w-[286px] shadow-2xl">
            <button type="button" onClick={onClose} className="absolute right-3 top-3 z-10 rounded-md p-2 text-[#6B7280] hover:bg-[#F7F7F5]" aria-label="Close navigation">
              <X className="h-5 w-5" />
            </button>
            {content}
          </div>
        </div>
      ) : null}
      <div className={cn("fixed inset-y-0 left-0 z-40 hidden lg:block", collapsed ? "w-[72px]" : "w-[248px]")}>
        {content}
      </div>
    </>
  );
}
