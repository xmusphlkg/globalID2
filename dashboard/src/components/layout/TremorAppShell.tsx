"use client";

import { useState } from "react";
import { useAppStore } from "@/stores/app-store";
import { Sidebar } from "./Sidebar";
import { TopNavbar } from "./TopNavbar";

export function TremorAppShell({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { sidebarCollapsed, setSidebarCollapsed } = useAppStore();
  const desktopSidebarWidth = sidebarCollapsed ? "lg:pl-24" : "lg:pl-72";

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
          <div className={`mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 ${sidebarCollapsed ? "lg:px-6" : "lg:px-8"}`}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
