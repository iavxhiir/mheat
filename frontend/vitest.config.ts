import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/test/**',
        'src/api/generated.ts',
        'src/main.tsx',
        'src/plotly.d.ts',
        'src/types.ts',
        // Map / chart components require canvas + WebGL + leaflet — exercised
        // by the a11y/e2e Playwright suite instead of jsdom unit tests.
        'src/components/MapView.tsx',
        'src/components/MapViewGL.tsx',
        'src/components/EventChart.tsx',
        'src/components/KeyboardHelp.tsx',
        'src/App.tsx',
      ],
      thresholds: {
        lines: 60,
        functions: 50,
        statements: 60,
        branches: 75,
      },
    },
  },
});
