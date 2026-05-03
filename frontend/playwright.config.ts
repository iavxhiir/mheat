import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for the MHEAT axe-core smoke test.
 *
 * The webServer block boots `npm run preview` on :4173 so the
 * production build (served from dist/) is what we audit — same
 * bundle that ships in the Docker image.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5000 },
  retries: 0,
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run preview',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
