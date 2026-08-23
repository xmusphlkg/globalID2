import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { AppShell } from "@/shared/layout/AppShell";

export const metadata: Metadata = {
  applicationName: "GIDS Control Center",
  title: {
    default: "GIDS Control Center",
    template: "%s | GIDS Control Center",
  },
  description: "GIDS operations, data quality, and publication control center",
  manifest: "/manifest.webmanifest",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#B54708",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="m-0 h-full bg-tremor-background-subtle text-tremor-content-strong antialiased">
        <Providers>
          <AppShell>
            {children}
          </AppShell>
        </Providers>
      </body>
    </html>
  );
}
