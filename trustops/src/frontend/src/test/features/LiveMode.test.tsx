/**
 * The app as it behaves against a live backend: no "mock backend" chrome, the monitor reports the
 * real stream status, and the login page falls back to a password form when identity serves no
 * demo picker (it answers 404 outside a local environment).
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { resetMockState, server } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const problem = (status: number, title: string) =>
  HttpResponse.json({ type: 'about:blank', title, status }, { status, headers: { 'Content-Type': 'application/problem+json' } });

describe('chrome', () => {
  it('carries no "mock backend" banner', async () => {
    renderApp('/', { as: USERS.priya });
    expect(await screen.findByRole('heading', { level: 1, name: /Welcome back, Priya/ })).toBeInTheDocument();
    expect(screen.queryByText(/mock backend|MSW/i)).not.toBeInTheDocument();
  });

  it('shows the stream status on the harness monitor, never "mock backend"', async () => {
    resetMockState('closed');
    renderApp('/monitor/FCRM-2026-0151', { as: USERS.observer });
    expect(await screen.findByText(/Harness monitor · FCRM-2026-0151/)).toBeInTheDocument();
    // jsdom has no EventSource, so the monitor is on its polling fallback.
    expect(screen.getByText('polling')).toBeInTheDocument();
    expect(screen.queryByText(/mock backend/i)).not.toBeInTheDocument();
  });
});

describe('login without a demo picker', () => {
  async function openSso() {
    renderApp('/login');
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with corporate SSO' }));
  }

  it.each([
    [404, 'Not found'],
    [403, 'Forbidden'],
  ])('shows the username and password form when the directory answers %i', async (status, title) => {
    server.use(http.get(paths.auth.demoUsers, () => problem(status, title)));
    await openSso();
    expect(await screen.findByLabelText(/Username/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Password/)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Product owners' })).not.toBeInTheDocument();
    expect(screen.queryByText(/demo directory is unavailable/i)).not.toBeInTheDocument();
  });

  it('asks the directory once: a refusal is not retried', async () => {
    let asked = 0;
    server.use(
      http.get(paths.auth.demoUsers, () => {
        asked += 1;
        return problem(404, 'Not found');
      }),
    );
    await openSso();
    await screen.findByLabelText(/Username/);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(asked).toBe(1);
  });

  it('signs in with the form', async () => {
    server.use(http.get(paths.auth.demoUsers, () => problem(404, 'Not found')));
    const { router } = renderApp('/login');
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with corporate SSO' }));
    await userEvent.type(await screen.findByLabelText(/Username/), 'pnair');
    await userEvent.type(screen.getByLabelText(/Password/), 'demo-pnair');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
  });

  it('explains an unreachable directory and still offers the form', async () => {
    server.use(http.get(paths.auth.demoUsers, () => problem(503, 'Service unavailable')));
    await openSso();
    expect(await screen.findByText(/demo directory is unavailable/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Username/)).toBeInTheDocument();
  });
});
