"use client";

import { Suspense, useState, useEffect } from "react";

import { t } from "@/lib/i18n";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopNavbar } from "@/components/layout/TopNavbar";
import { useAppStore } from "@/stores/app-store";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { lang, sidebarCollapsed, setSidebarCollapsed } = useAppStore();
  useEffect(() => { document.documentElement.lang = lang === "zh" ? "zh-CN" : "en"; }, [lang]);
  const desktopSidebarWidth = sidebarCollapsed ? "lg:pl-[72px]" : "lg:pl-[248px]";

  return (
    <div className="control-shell m-0 min-h-screen w-full pt-0">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-2 focus:z-[100] focus:rounded focus:bg-white focus:p-3">{t(lang, "cc_skip")}</a>
      <Sidebar
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        collapsed={sidebarCollapsed}
      />

      <div className={`flex min-h-screen min-w-0 flex-col ${desktopSidebarWidth}`}>
        <Suspense fallback={<div className="h-14 border-b border-[#D9D9D6] bg-white" />}>
          <TopNavbar
            onMenuClick={() => setSidebarOpen(true)}
            sidebarCollapsed={sidebarCollapsed}
            onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
          />
        </Suspense>

        <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 pb-10">
          <div className="w-full min-w-0 px-4 py-5 sm:px-6 lg:py-6">
            <Suspense fallback={<div className="min-h-48 animate-pulse rounded-md border border-[#E5E5E2] bg-white" />}>
              {children}
            </Suspense>
          </div>
        </main>
      </div>
    </div>
  );
}
