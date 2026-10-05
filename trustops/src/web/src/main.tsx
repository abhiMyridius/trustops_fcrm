/**
 * Entry: QueryClientProvider · ToastProvider · AuthProvider · RouterProvider. In VITE_API_MODE=msw
 * (the default until the services exist) the MSW worker is started first and serves every
 * /api/fcrm route from the P7 seed; in live mode the mock code is never loaded.
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { AppProviders, makeQueryClient } from '@/app/providers';
import { createAppRouter } from '@/app/router';
import '@/design/index.css';

const apiMode = (import.meta.env.VITE_API_MODE as string | undefined) ?? 'msw';

async function start(): Promise<void> {
  if (apiMode === 'msw') {
    // Dynamic import keeps the mock backend and the seed out of the live bundle.
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

void start();
