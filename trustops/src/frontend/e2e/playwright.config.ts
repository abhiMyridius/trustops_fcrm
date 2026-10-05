/**
 * Playwright config for TrustOps's web app, run against its own MSW-mocked dev server
 * (`npm run dev`), not the composed stack.
 *
 * BUILD-PLAN.md / docs/architecture/design/C-web-tests-ops-sdlc.md describe a `/tests/e2e`
 * Playwright suite with a `baseURL` of `http://localhost:8080` (the nginx gateway in front of
 * the real, composed 23-container stack, `FCRM_LLM_MODE=replay`) driving all 21 demo scenes plus
 * a third-party black-box smoke test. That stack cannot be brought up end to end on this host
 * today (see CLAUDE.md / the integration-lane brief: most services have no real HTTP layer yet,
 * and this machine's Docker Desktop has a `~/Downloads` bind-mount permission issue) -- so this
 * config intentionally does not point at port 8080 or try to start docker-compose.
 *
 * Instead it starts the web app's own standalone dev server (`npm run dev`, MSW-backed,
 * `VITE_API_MODE=msw` is already the default) and runs real, scripted browser scenes against it.
 * These are genuine, runnable, passing tests today -- not a live-stack rehearsal. When the real
 * services and the nginx gateway exist, the composed-stack config described in the design doc
 * belongs at the repo-root `tests/e2e/` (per that doc's own tree), reusing these scene scripts
 * with `baseURL` swapped to the gateway and MSW turned off; that migration is not done here.
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
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: BASE_URL,
    cwd: '..',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
