"use client";

import { useState } from "react";

import { Sidebar } from "@/components/layout/Sidebar";
import { TopNavbar } from "@/components/layout/TopNavbar";
import { useAppStore } from "@/stores/app-store";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { sidebarCollapsed, setSidebarCollapsed } = useAppStore();
  const desktopSidebarWidth = sidebarCollapsed ? "lg:pl-24" : "lg:pl-80";

  return (
    <div className="m-0 min-h-screen w-full bg-tremor-background-subtle pt-0 text-tremor-content-strong">
      <Sidebar
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        collapsed={sidebarCollapsed}
      />

      <div className={`flex min-h-screen flex-col ${desktopSidebarWidth}`}>
        <TopNavbar
          onMenuClick={() => setSidebarOpen(true)}
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
        />

        <main className="flex-1 pb-10">
          <div className={`mx-auto w-full max-w-[1560px] px-4 py-5 sm:px-6 lg:py-7 ${sidebarCollapsed ? "lg:px-6" : "lg:px-8"}`}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
