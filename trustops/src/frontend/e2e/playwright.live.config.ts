/**
 * Playwright config for the LIVE web app: the browser drives the nginx gateway on :8080 (the composed
 * stack from `make up`), with no dev server and no MSW. Run it with `npm run test:e2e:live`.
 *
 * The live scenes are in ./scenes-live: login routing per persona, the product owner's pages without a
 * score, band or rating, the analyst queue, a committee pack with hidden votes and the harness monitor.
 * They read what `make demo` left (the demo case closed) and the seed around it, so run them after it
 * (`make e2e` does). The MSW scenes in ./scenes stay on playwright.config.ts (`npm run test:e2e`).
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
