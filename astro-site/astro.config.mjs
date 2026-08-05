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
            if (id.includes('/node_modules/echarts/')) return 'echarts';
          },
        },
      },
    },
  },
});
