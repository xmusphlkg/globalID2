import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

const siteUrl = process.env.GLOBALID_SITE_URL || process.env.SITE_URL || 'https://globalinfectiousdisease.com';

export default defineConfig({
  site: siteUrl,
  output: 'static',
  integrations: [
    react(),
  ],
  build: {
    // Inline compact page-specific styles while preserving the shared base
    // stylesheet as a cacheable asset across the generated page catalogue.
    inlineStylesheets: 'auto',
  },
  vite: {
    optimizeDeps: {
      include: [
        '@astrojs/react/client.js',
        'react',
        'react-dom',
        // The chart registries import ECharts through these modular entry
        // points. List them up front so Vite does not discover and re-bundle
        // them while an Astro island is already hydrating; that invalidates
        // the dependency URLs and produces "504 Outdated Optimize Dep".
        'echarts/core',
        'echarts/charts',
        'echarts/components',
        'echarts/renderers',
        'echarts-for-react/lib/core.js',
        'tslib',
      ],
      exclude: [
        // Let Vite resolve the JSX runtime per mode. Pre-bundling this module
        // can lock the dev server to React's production dev-runtime shim, where
        // jsxDEV is intentionally undefined and React islands fail to hydrate.
        'react/jsx-runtime',
        'react/jsx-dev-runtime',
      ],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            // ECharts shares zrender across every feature registry. Keeping it
            // as a stable cacheable vendor chunk prevents the shared graph
            // from collapsing back into a >500 kB monolith.
            if (id.includes('/node_modules/zrender/')) return 'zrender';
          },
        },
      },
    },
  },
});
