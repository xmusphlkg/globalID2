"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown, Cpu, Globe2, HardDrive, Loader2, MemoryStick, Network, ServerCog, X } from "lucide-react";
import { useEffect, useRef } from "react";

import { useControlPlaneOverview } from "@/features/overview/api";
import { cn } from "@/lib/utils";
import { findSectionByPath, navigationSections } from "@/shared/navigation/route-registry";

const ACTIVE_TASK_STATUSES = ["pending", "queued", "running", "retrying"] as const;

function compactNumber(value: number): string {
  return new Intl.NumberFormat("en", { notation: value >= 1000 ? "compact" : "standard" }).format(value);
}

function formatPercent(value?: number | null): string {
  return value === null || value === undefined ? "-" : `${Math.round(value)}%`;
}

function proxyLocationLabel(proxy?: { lookup_status?: string; country_name?: string | null; country_code?: string | null; ip?: string | null }): string {
  if (!proxy || proxy.lookup_status === "unavailable") return "Unavailable";
  const country = proxy.country_name || proxy.country_code || "Locating";
  return proxy.ip ? `${country} · ${proxy.ip}` : country;
}

function relativeUpdateTime(value?: string | null): string {
  if (!value) return "Waiting for first sync";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Recently updated";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
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
  const pathname = usePathname();
  const activeSection = findSectionByPath(pathname);
  const mobileDialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const overview = useControlPlaneOverview();
  const data = overview.data;
  const activeTasks = ACTIVE_TASK_STATUSES.reduce((sum, status) => sum + (data?.tasks[status] ?? 0), 0);
  const failedTasks = data?.tasks.failed ?? 0;
  const actionItems = data?.action_items.length ?? 0;
  const liveServices = data?.runtime.services.length ?? 0;
  const apiLive = data?.runtime.services.some((service) => service.service === "api") ?? false;
  const runtimeAvailable = data?.runtime.heartbeat_available ?? false;
  const schedulesEnabled = data?.schedules.enabled ?? 0;
  const schedulesTotal = data?.schedules.total ?? 0;
  const resources = data?.system_resources;
  const proxyLabel = proxyLocationLabel(resources?.proxy);
  const hasAttention = overview.isError || actionItems > 0 || failedTasks > 0 || !runtimeAvailable || !apiLive;
  const statusLabel = overview.isLoading
    ? "Checking"
    : hasAttention
      ? "Attention"
      : activeTasks > 0
        ? "Running"
        : "Healthy";
  const statusDetail = overview.isLoading
    ? "Loading project status"
    : overview.isError
      ? "Control plane unavailable"
      : hasAttention
        ? `${compactNumber(actionItems || failedTasks || 1)} item${(actionItems || failedTasks || 1) === 1 ? "" : "s"} need review`
        : activeTasks > 0
          ? `${compactNumber(activeTasks)} active task${activeTasks === 1 ? "" : "s"}`
          : `API live · ${compactNumber(liveServices)} services · ${compactNumber(schedulesEnabled)}/${compactNumber(schedulesTotal)} jobs`;
  const statusTone = overview.isLoading
    ? "text-[#6B7280] bg-[#E5E5E2]"
    : hasAttention
      ? "text-[#B42318] bg-[#FEE4E2]"
      : activeTasks > 0
        ? "text-[#A15C07] bg-[#FEF0C7]"
        : "text-[#157F3C] bg-[#D1FADF]";

  useEffect(() => {
    if (!mobileOpen) return;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose?.();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        mobileDialogRef.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => !element.hasAttribute("hidden"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [mobileOpen, onClose]);

  const content = (
    <div className={cn("flex h-full flex-col border-r border-[#D9D9D6] bg-white", collapsed ? "px-2" : "px-3")}>
      <div className={cn("flex h-16 items-center border-b border-[#ECECEA]", collapsed ? "justify-center" : "gap-3 px-2")}>
        <Link href="/overview" onClick={onClose} className="flex items-center gap-3" aria-label="GIDS Control Center overview">
          <span className="control-center-mark" aria-hidden="true"><span /></span>
          {!collapsed ? (
            <span className="min-w-0">
              <span className="block text-sm font-semibold tracking-tight text-[var(--cc-text-strong)]">GIDS</span>
              <span className="block truncate text-[11px] font-medium uppercase tracking-[.08em] text-[var(--cc-text-muted)]">Control Center</span>
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

      {collapsed ? (
        <Link
          href="/operations/runtime"
          onClick={onClose}
          title={`Project status: ${statusLabel}. ${statusDetail}`}
          className="mb-3 flex h-10 items-center justify-center rounded-md border border-[#E5E5E2] bg-[#F7F7F5] text-[#4B5563] transition hover:border-[#D9D9D6] hover:bg-white hover:text-[#1D1D1F]"
          aria-label={`Project status: ${statusLabel}. ${statusDetail}`}
        >
          <span className="relative">
            {overview.isLoading ? <Loader2 className="h-[18px] w-[18px] animate-spin" /> : <ServerCog className="h-[18px] w-[18px]" />}
            <span className={cn("absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full ring-2 ring-[#F7F7F5]", statusTone)} />
          </span>
        </Link>
      ) : (
        <div className="mb-3 rounded-md border border-[#E5E5E2] bg-[#F7F7F5] px-3 py-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-[11px] font-semibold uppercase tracking-[.08em] text-[#6B7280]">System Resources</p>
              <p className="mt-1 truncate text-sm font-semibold text-[#1D1D1F]">{statusLabel}</p>
            </div>
            <span className={cn("inline-flex h-6 shrink-0 items-center rounded-md px-2 text-[11px] font-semibold", statusTone)}>
              {overview.isLoading ? "Sync" : hasAttention ? "Review" : "OK"}
            </span>
          </div>
          <p className="mt-1.5 line-clamp-2 text-[11px] leading-4 text-[#6B7280]">{statusDetail}</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <ResourceStat icon={Cpu} label="CPU" value={formatPercent(resources?.cpu.usage_percent)} muted={overview.isLoading} />
            <ResourceStat icon={MemoryStick} label="Memory" value={formatPercent(resources?.memory.used_percent)} muted={overview.isLoading} />
            <ResourceStat icon={HardDrive} label="Disk" value={formatPercent(resources?.disk.used_percent)} muted={overview.isLoading} />
            <ResourceStat icon={Network} label="Network" value={`${compactNumber(resources?.network.total ?? 0)} conns`} muted={overview.isLoading} />
          </div>
          <Link href="/operations/runtime" onClick={onClose} className="mt-3 flex items-center gap-2 border-t border-[#E5E5E2] pt-2 text-[11px] font-medium text-[#6B7280] hover:text-[#C2410C]" title={`Exit IP: ${proxyLabel}`}>
            <Globe2 className="h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate">Exit IP: {proxyLabel}</span>
            <span className="shrink-0">{relativeUpdateTime(data?.generated_at)}</span>
          </Link>
        </div>
      )}
    </div>
  );

  return (
    <>
      {mobileOpen ? (
        <div ref={mobileDialogRef} className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Control center navigation">
          <div className="fixed inset-0 bg-black/35" onClick={onClose} />
          <div className="fixed inset-y-0 left-0 w-[286px] shadow-2xl">
            <button ref={closeButtonRef} type="button" onClick={onClose} className="absolute right-3 top-3 z-10 rounded-md p-2 text-[var(--cc-text-muted)] hover:bg-[#F7F7F5]" aria-label="Close navigation">
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

function ResourceStat({
  icon: Icon,
  label,
  value,
  muted,
}: {
  icon: typeof ServerCog;
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="min-w-0 rounded-md border border-[#E5E5E2] bg-white px-2 py-2">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[.06em] text-[#6B7280]">
        <Icon className="h-3 w-3 shrink-0" />
        <span className="truncate">{label}</span>
      </div>
      <p className={cn("mt-1 truncate text-xs font-semibold text-[#1D1D1F]", muted ? "text-[#6B7280]" : null)}>{muted ? "-" : value}</p>
    </div>
  );
}
