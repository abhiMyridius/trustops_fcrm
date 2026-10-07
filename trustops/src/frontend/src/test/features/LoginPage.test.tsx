import { configure, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { server } from '@/mocks/server';
import { renderApp } from '../render';
import { violations } from '../axe';
import { USERS } from '../fixtures';

// The route chunks load lazily; with the whole suite running in parallel the first render can take longer than 1 s.
configure({ asyncUtilTimeout: 5000 });

describe('LoginPage (2.1)', () => {
  it('renders without axe violations and lists the seeded directory after SSO', async () => {
    const { container } = renderApp('/login');
    expect(await screen.findByRole('heading', { level: 1, name: 'Sign in to TrustOps' })).toBeInTheDocument();
    expect(await violations(container)).toEqual([]);

    await userEvent.click(screen.getByRole('button', { name: 'Sign in with corporate SSO' }));
    const po = await screen.findByRole('group', { name: 'Product owners' });
    expect(within(po).getByRole('button', { name: /Priya Nair/ })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'FCRM analysts' })).toHaveTextContent('Rahul Iyer');
    const committee = screen.getByRole('group', { name: 'Risk Committee' });
    for (const n of ['Ana Mendes', 'Joseph Okafor', 'Lin Chen']) expect(committee).toHaveTextContent(n);
    // 11 seeded users plus the synthetic observer.
    expect(screen.getAllByRole('button', { name: /Sign in as/ })).toHaveLength(12);
    expect(await violations(container)).toEqual([]);
  });

  it('signs Priya in and lands on the PO dashboard', async () => {
    const { router } = renderApp('/login');
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with corporate SSO' }));
    await userEvent.click(await screen.findByRole('button', { name: /Priya Nair/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/po'));
    expect(await screen.findByRole('heading', { level: 1, name: /Welcome back, Priya/ })).toBeInTheDocument();
  });

  it('says "Unknown user or wrong password." when sign-in is refused, never "session ended" (D-108)', async () => {
    // No demo picker: the username and password form, which is where a wrong password happens.
    server.use(http.get(paths.auth.demoUsers, () => HttpResponse.json({ type: 'x', title: 'Not found', status: 404 }, { status: 404 })));
    const { router } = renderApp('/login');
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with corporate SSO' }));
    await userEvent.type(await screen.findByLabelText(/Username/), 'riyer-typo');
    await userEvent.type(screen.getByLabelText(/Password/), 'not-the-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Sign-in failed')).toBeInTheDocument();
    expect(screen.getByText('Unknown user or wrong password.')).toBeInTheDocument();
    expect(screen.queryByText(/session has ended/i)).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/login');
  });

  it('signing out lands on a plain /login, without next', async () => {
    const { router } = renderApp('/po', { as: USERS.priya });
    await userEvent.click(await screen.findByRole('button', { name: /Account menu for Priya Nair/ }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Sign out' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(router.state.location.search).toBe('');
  });

  it('sends a signed-in PO from / to /po', async () => {
    const { router } = renderApp('/', { as: USERS.priya });
    await waitFor(() => expect(router.state.location.pathname).toBe('/po'));
  });

  it('redirects a signed-out visitor to /login with next', async () => {
    const { router } = renderApp('/analyst/queue');
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent('/analyst/queue')}`);
  });
});
