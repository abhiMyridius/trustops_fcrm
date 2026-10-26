/**
 * The Handoff tab's votes box belongs to a committee route only. A Band 1/2 case the analyst closed under
 * delegated authority has a decided pack nobody votes on, so neither the Votes card nor the committee
 * questions show, and no votes request is made.
 */
import { configure, screen } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { HandoffView } from '@/api/types/analyst';
import { resetMockState, server } from '@/mocks/server';
import * as AV from '@/mocks/views/analyst';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

const DEMO = 'FCRM-2026-0151';

function handoffWith(over: Partial<HandoffView>): void {
  const base = AV.handoff(DEMO);
  if (base === null) throw new Error('the demo case has no handoff view');
  server.use(http.get(paths.cases.handoff(DEMO), () => HttpResponse.json({ ...base, ...over })));
}

describe('the Handoff votes box', () => {
  it('shows for a case routed to the committee', async () => {
    resetMockState('closed');
    handoffWith({ route: 'committee' });
    renderApp(`/analyst/case/${DEMO}/handoff`, { as: USERS.rahul });
    expect(await screen.findByText('Votes')).toBeInTheDocument();
  });

  it('is not shown for a delegated close', async () => {
    resetMockState('closed');
    const base = AV.handoff(DEMO);
    const pack = base?.pack ? { ...base.pack, status: 'decided' as const } : null;
    handoffWith({ route: 'analyst', band: 2, released_to: [], pack });
    let votesAsked = false;
    server.use(
      http.get(paths.decision.votes('*'), () => {
        votesAsked = true;
        return HttpResponse.json({});
      }),
    );
    renderApp(`/analyst/case/${DEMO}/handoff`, { as: USERS.rahul });
    expect(await screen.findByText(/Release checks/)).toBeInTheDocument();
    expect(screen.queryByText('Votes')).not.toBeInTheDocument();
    expect(screen.queryByText('Committee questions (R6)')).not.toBeInTheDocument();
    expect(votesAsked).toBe(false);
  });
});
