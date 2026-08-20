import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
        { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
      ],
    }];
  },
  async redirects() {
    return [
      { source: "/", destination: "/overview", permanent: true },
      { source: "/ai", destination: "/production/ai", permanent: true },
      { source: "/ai/agent-runs", destination: "/production/runs", permanent: true },
      { source: "/ai/disease-audit", destination: "/data/audit", permanent: true },
      { source: "/ai/disease-mapping", destination: "/data/mappings", permanent: true },
      { source: "/ai/interactions", destination: "/production/interactions", permanent: true },
      { source: "/ai/models", destination: "/settings/models", permanent: true },
      { source: "/ai/tasks", destination: "/production/ai", permanent: true },
      { source: "/data", destination: "/data/analytics", permanent: true },
      { source: "/data/dashboard", destination: "/data/analytics", permanent: true },
      { source: "/data/release", destination: "/production/releases", permanent: true },
      { source: "/diseases", destination: "/data/diseases", permanent: true },
      { source: "/explorer", destination: "/data/explorer", permanent: true },
      { source: "/quality", destination: "/data/quality", permanent: true },
      { source: "/reports", destination: "/production/reports", permanent: true },
      { source: "/setting", destination: "/settings/integrations", permanent: true },
      { source: "/settings", destination: "/settings/integrations", permanent: true },
      { source: "/situation", destination: "/overview/events", permanent: true },
      { source: "/sources", destination: "/operations/sources", permanent: true },
      { source: "/sources/automation", destination: "/operations/schedules/ingestion", permanent: true },
      { source: "/sources/flow", destination: "/operations/sources", permanent: true },
      { source: "/sources/tasks", destination: "/operations/tasks", permanent: true },
      { source: "/subscriptions", destination: "/production/distribution", permanent: true },
      { source: "/subscriptions/notifications", destination: "/production/campaigns", permanent: true },
      { source: "/tasks", destination: "/operations/tasks", permanent: true },
    ];
  },
};

export default nextConfig;
