/**
 * Assurance > Agent quality (D-107). S4 accepts only an ISO week (`2026-W40`) and answers the newest
 * published week when none is sent; the tab used to send `?week=2026-09-28`, so every visit was a 422
 * red error. "No run published for the week" (404) is an empty state, not a fault.
 */
import { screen } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { problem } from '@/mocks/http';
import { resetMockState, server } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const ROUTE = '/analyst/assurance/agent-quality';

/** Answers like the mock but records the query string of every request. */
function watchQuery(): string[] {
  const seen: string[] = [];
  server.use(
    http.get(paths.audit.agentQuality, ({ request }) => {
      seen.push(new URL(request.url).search);
      return HttpResponse.json({ week: '2026-W40', golden_cases: 80, regressions: 0, drift: 'none', rows: [{ agent: 'intake', metric: 'extraction accuracy', n: 10, pct: 96.4, lower_is_better: false }] });
    }),
  );
  return seen;
}

describe('Agent quality tab', () => {
  it('asks for no week by default, so S4 answers the newest published week', async () => {
    resetMockState('closed');
    const seen = watchQuery();
    renderApp(ROUTE, { as: USERS.rahul });
    expect(await screen.findByText(/Week 2026-W40/)).toBeInTheDocument();
    expect(seen).toEqual(['']);
  });

  it('turns a date in the address bar into its ISO week, the form S4 accepts', async () => {
    resetMockState('closed');
    const seen = watchQuery();
    renderApp(`${ROUTE}?week=2026-09-28`, { as: USERS.rahul });
    expect(await screen.findByText(/Week 2026-W40/)).toBeInTheDocument();
    expect(seen).toEqual(['?week=2026-W40']);
  });

  it('never sends a week it cannot read', async () => {
    resetMockState('closed');
    const seen = watchQuery();
    renderApp(`${ROUTE}?week=last-week`, { as: USERS.rahul });
    expect(await screen.findByText(/Week 2026-W40/)).toBeInTheDocument();
    expect(seen).toEqual(['']);
  });

  it('shows the published run from the mock backend, which refuses a non-ISO week like S4 does', async () => {
    resetMockState('closed');
    renderApp(`${ROUTE}?week=2026-09-28`, { as: USERS.rahul });
    expect(await screen.findByText(/Week 2026-W40/)).toBeInTheDocument();
    expect(screen.getAllByText(/suggestion acceptance/).length).toBeGreaterThan(0);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows an empty state, not a red error, when no run is published for the week', async () => {
    resetMockState('closed');
    renderApp(`${ROUTE}?week=2026-W01`, { as: USERS.rahul });
    expect(await screen.findByText('No evaluation run is published for this week yet')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Trace id/)).not.toBeInTheDocument();
  });

  it('shows the same empty state for the 404 S4 answers when nothing was ever published', async () => {
    resetMockState('closed');
    server.use(http.get(paths.audit.agentQuality, () => problem(404, 'Not found', 'No agent evaluation has been published for that week.')));
    renderApp(ROUTE, { as: USERS.rahul });
    expect(await screen.findByText('No evaluation run is published for this week yet')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('keeps the red error with Retry for a real failure, such as the evals credential being missing', async () => {
    resetMockState('closed');
    server.use(http.get(paths.audit.agentQuality, () => problem(503, 'Upstream unavailable', 'The agent quality is not available.')));
    renderApp(ROUTE, { as: USERS.rahul });
    expect(await screen.findByRole('alert', {}, { timeout: 8000 })).toHaveTextContent('The agent quality is not available.');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText('No evaluation run is published for this week yet')).not.toBeInTheDocument();
  }, 15000);
});
