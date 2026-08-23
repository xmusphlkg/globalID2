"use client";

import { Suspense, useState } from "react";

import { Sidebar } from "@/components/layout/Sidebar";
import { TopNavbar } from "@/components/layout/TopNavbar";
import { useAppStore } from "@/stores/app-store";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { sidebarCollapsed, setSidebarCollapsed } = useAppStore();
  const desktopSidebarWidth = sidebarCollapsed ? "lg:pl-[72px]" : "lg:pl-[248px]";

  return (
    <div className="control-shell m-0 min-h-screen w-full pt-0">
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

        <main id="main-content" className="min-w-0 flex-1 pb-10">
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
