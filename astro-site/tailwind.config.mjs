/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      colors: {
        brand: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
          950: '#172554',
        },
        teal: {
          400: '#2dd4bf',
          500: '#14b8a6',
          600: '#0d9488',
        },
        surface: {
          DEFAULT: '#0f172a',
          card: '#1e293b',
          border: '#334155',
          muted: '#475569',
        },
      },
      typography: (theme) => ({
        DEFAULT: {
          css: {
            color: theme('colors.slate.300'),
            a: { color: theme('colors.brand.400') },
            strong: { color: theme('colors.slate.100') },
            h1: { color: theme('colors.slate.100') },
            h2: { color: theme('colors.slate.100') },
            h3: { color: theme('colors.slate.200') },
            h4: { color: theme('colors.slate.200') },
            code: { color: theme('colors.teal.400') },
            blockquote: { color: theme('colors.slate.400') },
          },
        },
      }),
    },
  },
  plugins: [require('@tailwindcss/typography')],
};
