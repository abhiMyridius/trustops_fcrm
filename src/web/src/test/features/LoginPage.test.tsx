import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { renderApp } from '../render';
import { violations } from '../axe';

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
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(await screen.findByRole('heading', { level: 1, name: /Welcome back, Priya/ })).toBeInTheDocument();
  });

  it('redirects a signed-out visitor to /login with next', async () => {
    const { router } = renderApp('/analyst/queue');
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent('/analyst/queue')}`);
  });
});
