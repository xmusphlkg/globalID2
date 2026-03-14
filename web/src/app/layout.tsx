import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { TremorAppShell } from "@/components/layout/TremorAppShell";

export const metadata: Metadata = {
  title: "GlobalID Dashboard",
  description: "Disease surveillance dashboard",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f8fafc", // Tremor background subtle
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <head>
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
        />
      </head>
      <body className="h-full bg-tremor-background-subtle dark:bg-dark-tremor-background-subtle text-tremor-content-strong antialiased m-0">
        <Providers>
          <TremorAppShell>
            {children}
          </TremorAppShell>
        </Providers>
      </body>
    </html>
  );
}
