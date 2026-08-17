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
      force: true,
      include: [
        '@astrojs/react/client.js',
        'react',
        'react-dom',
        'react/jsx-runtime',
        'react/jsx-dev-runtime',
        'echarts',
        'echarts-for-react',
        'echarts-for-react/lib/core.js',
        'tslib',
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
