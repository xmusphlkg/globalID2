import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import tailwind from '@astrojs/tailwind';

const siteUrl = process.env.GLOBALID_SITE_URL || process.env.SITE_URL || 'https://globalinfectiousdisease.com';

export default defineConfig({
  site: siteUrl,
  output: 'static',
  integrations: [
    react(),
    tailwind({ applyBaseStyles: false }),
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
        'echarts-for-react/lib/core',
        'tslib',
      ],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
          },
        },
      },
    },
  },
});
