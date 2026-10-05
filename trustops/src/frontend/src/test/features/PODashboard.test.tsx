import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, expectTypeOf, it } from 'vitest';
import type { CasePO, CasePOListItem, DashboardPO, IntakeViewPO, OutcomePO } from '@/api/types/po';
import { RESTRICTED_TEXT, USERS } from '../fixtures';
import { renderApp } from '../render';
import { violations } from '../axe';

describe('PO Dashboard (2.2) · visibility guard mirroring BB-02', () => {
  it('shows the seed figures 5 · 1 · 3.3 · 3 and Priya’s cases', async () => {
    renderApp('/', { as: USERS.priya });
    const kpis = await screen.findByRole('region', { name: 'Key figures' });
    await waitFor(() => expect(within(kpis).getByText('Active cases').nextElementSibling).toHaveTextContent('5'));
    expect(within(kpis).getByText('Awaiting my answer').nextElementSibling).toHaveTextContent('1');
    expect(within(kpis).getByText('Avg intake to decision').nextElementSibling).toHaveTextContent('3.3');
    expect(within(kpis).getByText('Open conditions').nextElementSibling).toHaveTextContent('3');
    const table = await screen.findByRole('table', { name: 'My cases' });
    for (const id of ['0150', '0146', '0139', '0121', '0109', '0151']) expect(table).toHaveTextContent(id);
  });

  it('renders no score, band, rating or clause text anywhere on the page, and has no axe violations', async () => {
    const { container } = renderApp('/', { as: USERS.priya });
    await screen.findByRole('table', { name: 'My cases' });
    await waitFor(() => expect(screen.queryAllByRole('status', { name: 'Loading' })).toHaveLength(0));
    expect(document.body.textContent ?? '').not.toMatch(RESTRICTED_TEXT);
    expect(await violations(container)).toEqual([]);
  });

  it('PO DTO types have no restricted fields (type-level fence)', () => {
    expectTypeOf<DashboardPO>().not.toHaveProperty('band');
    expectTypeOf<CasePOListItem>().not.toHaveProperty('band');
    expectTypeOf<CasePOListItem>().not.toHaveProperty('composite');
    expectTypeOf<CasePO>().not.toHaveProperty('score');
    expectTypeOf<CasePO>().not.toHaveProperty('route');
    expectTypeOf<IntakeViewPO>().not.toHaveProperty('clauses');
    expectTypeOf<OutcomePO>().not.toHaveProperty('votes');
  });

  it('a PO on the harness monitor gets 403 (ADR-19)', async () => {
    renderApp('/monitor/FCRM-2026-0151', { as: USERS.priya });
    expect(await screen.findByRole('heading', { name: /403 · Not available for your role/ })).toBeInTheDocument();
  });

  it('a PO on an analyst route is sent to their own home, with no hint the route exists', async () => {
    const { router } = renderApp('/analyst/config', { as: USERS.priya });
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
  });
});
