/** Custom render helpers: the real providers and the real route tree on a memory router. */
import type { ReactElement, ReactNode } from 'react';
import { render, type RenderResult } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, RouterProvider, createMemoryRouter } from 'react-router-dom';
import { AppProviders, makeQueryClient } from '@/app/providers';
import { routes } from '@/app/router';
import { ToastProvider } from '@/components/ui/Toast';
import { signInAs } from './fixtures';

/** Render one component with Query, Toast and a router (no auth). */
export function renderUi(ui: ReactElement, { route = '/' }: { route?: string } = {}): RenderResult {
  const client = makeQueryClient();
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
  return render(ui, { wrapper: Wrapper });
}

/** Render the whole app at `route`, optionally signed in as a seeded user (refresh-cookie path). */
export function renderApp(route: string, opts: { as?: string } = {}): RenderResult & { router: ReturnType<typeof createMemoryRouter> } {
  if (opts.as) signInAs(opts.as);
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [route] });
  const result = render(
    <AppProviders client={client}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return { ...result, router };
}
