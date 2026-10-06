/** Browser MSW worker. Started from main.tsx only when VITE_API_MODE=msw (`npm run dev:msw`); the live build never loads it. */
import { setupWorker } from 'msw/browser';
import { boot } from './db';
import { handlers } from './handlers';
import { applyScenario } from './scenarios';

export async function startMockBackend(): Promise<void> {
  boot(applyScenario);
  const worker = setupWorker(...handlers);
  await worker.start({
    onUnhandledRequest(req, print) {
      if (new URL(req.url).pathname.startsWith('/api/')) print.warning();
    },
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
    quiet: true,
  });
}
