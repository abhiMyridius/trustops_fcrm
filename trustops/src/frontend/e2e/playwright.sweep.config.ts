/**
 * Playwright config for the LIVE browser sweep: every screen of the running app, as each of the four
 * roles, through the nginx gateway on :8080 (the stack from `make up` must already be up). Run it with
 * `npm run test:e2e:sweep`. It never starts or stops the stack and never makes an AI call: the specs
 * in ./sweep-live avoid every control that starts an agent run (uploads, submit, ask, search, publish,
 * vote, lock, release, re-run) and list them as "not exercised: needs approval".
 *
 * One worker, because the gateway allows ten sign-ins a minute and twenty requests a second per user, and
 * because the app's refresh cookie rotates on use (one signed-in browser per role per spec file).
 * Output: test-results/sweep-report.json (one entry per page, control and check) and
 * test-results/screens/<role>/*.png, under src/frontend and not committed.
 */
import { defineConfig, devices } from '@playwright/test';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:8080';

export default defineConfig({
  testDir: './sweep-live',
  testMatch: /.*\.spec\.ts/,
  globalSetup: './sweep-live/global-setup.ts',
  globalTeardown: './sweep-live/global-teardown.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 15 * 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['json', { outputFile: '../test-results/playwright-results.json' }]],
  outputDir: '../test-results/artifacts',
  use: {
    baseURL: BASE_URL,
    trace: 'off',
    screenshot: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
});
