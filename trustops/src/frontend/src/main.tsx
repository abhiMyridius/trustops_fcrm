/**
 * Entry: QueryClientProvider · ToastProvider · AuthProvider · RouterProvider. The app talks to the
 * gateway (live) unless a dev run sets VITE_API_MODE=msw (`npm run dev:msw`): then the MSW worker is
 * started first and serves every /api/fcrm route from the P7 seed. Anything but an explicit `msw`
 * is live, so a production build can never load the mock code.
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { AppProviders, makeQueryClient } from '@/app/providers';
import { createAppRouter } from '@/app/router';
import '@/design/index.css';

async function start(): Promise<void> {
  if (import.meta.env.VITE_API_MODE === 'msw') {
    // Dynamic import behind a build-time constant keeps the mock backend and the seed out of the live bundle.
    const { startMockBackend } = await import('./mocks/browser');
    await startMockBackend();
  }
  const root = document.getElementById('root');
  if (!root) throw new Error('#root missing');
  const client = makeQueryClient();
  const router = createAppRouter();
  createRoot(root).render(
    <StrictMode>
      <AppProviders client={client}>
        <RouterProvider router={router} />
      </AppProviders>
    </StrictMode>,
  );
}

start().catch((error: unknown) => {
  // Nothing has rendered yet, so there is no error boundary: say so on the page instead of leaving it blank.
  console.error('the app could not start', error);
  const root = document.getElementById('root');
  if (root) root.textContent = 'TrustOps could not start. Reload the page; if it persists, tell support.';
});
