import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  site: 'https://globalid.pages.dev', // Update with your actual Cloudflare Pages URL
  output: 'static',
  integrations: [
    react(),
    tailwind({ applyBaseStyles: false }),
  ],
  build: {
    inlineStylesheets: 'auto',
  },
  vite: {
    resolve: {
      alias: {
        // react-plotly.js requires plotly.js/dist/plotly; redirect to the minified dist bundle
        'plotly.js/dist/plotly': 'plotly.js-dist-min',
      },
    },
    optimizeDeps: {
      include: ['react-plotly.js', 'plotly.js-dist-min'],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            plotly: ['plotly.js-dist-min'],
          },
        },
      },
    },
  },
});
