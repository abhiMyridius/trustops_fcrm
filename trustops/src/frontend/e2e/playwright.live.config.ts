/**
 * Playwright config for the LIVE web app: the browser drives the nginx gateway on :8080 (the composed
 * stack from `make up`), with no dev server and no MSW. Run it with `npm run test:e2e:live`.
 *
 * There are no live scenes yet: scenes land in ./scenes-live as each vertical goes live (until then
 * `npm run test:e2e:live` passes `--pass-with-no-tests`; drop that flag with the first scene). The MSW
 * scenes in ./scenes stay on playwright.config.ts (`npm run test:e2e`).
 *
 * One worker: the scenes walk one case through its lifecycle, so they share state and run in order.
 */
import { defineConfig, devices } from '@playwright/test';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:8080';

export default defineConfig({
  testDir: './scenes-live',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: './report-live' }]],
  outputDir: './artifacts-live',
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
