import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

const siteUrl = process.env.GLOBALID_SITE_URL || process.env.SITE_URL || 'https://globalinfectiousdisease.com';
const buildConcurrency = Number(process.env.GIDS_ASTRO_BUILD_CONCURRENCY ?? 2);
if (!Number.isInteger(buildConcurrency) || buildConcurrency < 1 || buildConcurrency > 8) {
  throw new Error('GIDS_ASTRO_BUILD_CONCURRENCY must be an integer from 1 to 8');
}

export default defineConfig({
  site: siteUrl,
  output: 'static',
  compressHTML: true,
  integrations: [
    react(),
  ],
  build: {
    concurrency: buildConcurrency,
    // Inline compact page-specific styles while preserving the shared base
    // stylesheet as a cacheable asset across the generated page catalogue.
    inlineStylesheets: 'auto',
  },
  vite: {
    plugins: [{
      name: 'local-subscription-options',
      apply: 'serve',
      configureServer(server) {
        server.middlewares.use('/__dev/subscription-options', async (request, response) => {
          response.setHeader('content-type', 'application/json; charset=utf-8');
          response.setHeader('cache-control', 'no-store');
          if (request.method !== 'GET') {
            response.statusCode = 405;
            response.end(JSON.stringify({ error: 'method_not_allowed' }));
            return;
          }
          try {
            const upstream = await fetch('https://subscriptions.globalinfectiousdisease.com/api/subscriptions/options', {
              signal: AbortSignal.timeout(10_000),
            });
            response.statusCode = upstream.status;
            response.end(await upstream.text());
          } catch {
            response.statusCode = 502;
            response.end(JSON.stringify({ error: 'options_unavailable' }));
          }
        });
      },
    }],
    optimizeDeps: {
      rolldownOptions: {
        // Vite's dependency optimizer can evaluate React's CommonJS runtime
        // entrypoints while pre-bundling islands. Keep dev JSX imports on
        // React's development branch so transformed TSX can call jsxDEV.
        transform: {
          define: {
            'process.env.NODE_ENV': '"development"',
          },
        },
      },
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
        // Disease report figures import marked while the island hydrates.
        'marked',
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
