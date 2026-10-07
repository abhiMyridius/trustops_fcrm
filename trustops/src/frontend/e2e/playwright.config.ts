/**
 * Playwright config for TrustOps's web app, run against its own MSW-mocked dev server
 * (`npm run dev:msw`, `VITE_API_MODE=msw`), not the composed stack, so it needs no Docker and
 * no AWS keys. The suites against the running stack on :8080 are playwright.live.config.ts
 * (the live scenes) and playwright.sweep.config.ts (the browser sweep of every screen).
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = 5190; // not 5173, so this never collides with a developer's own `npm run dev`
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './scenes',
  fullyParallel: false, // scenes share one MSW in-memory store per worker; keep it one worker
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: './report' }]],
  outputDir: './artifacts',
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: `npm run dev:msw -- --port ${PORT} --strictPort`,
    url: BASE_URL,
    cwd: '..',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
