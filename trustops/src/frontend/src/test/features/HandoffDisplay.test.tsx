/**
 * A case whose agent failed on both tries is with the analyst (H1's `handoff`). D-116: the queue lists it
 * under its own filter. D-117: the Summary banner names the failed agent and stage from the server, says
 * "nothing was skipped" once, lists several failed jobs, and shows even before the completeness report.
 */
import { configure, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { AssessmentSummary, WorkbenchHeader } from '@/api/types/analyst';
import { resetMockState, server } from '@/mocks/server';
import * as AV from '@/mocks/views/analyst';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

// The route chunks load lazily; with the whole suite running in parallel the first render can take longer than 1 s.
configure({ asyncUtilTimeout: 5000 });

const DEMO = 'FCRM-2026-0151';

type Failed = NonNullable<AssessmentSummary['agent_failed']>;

const RESEARCH: Failed = {
  step: 'assess',
  title: 'Research step handed to you',
  reason: 'The research agent failed on both tries in the assess stage.',
  failures: [{ agent: 'research', step: 'assess', gate_id: 'gate_h1' }],
};

function inHandoff(over: Partial<WorkbenchHeader> = {}): void {
  server.use(
    http.get(paths.cases.workbench(DEMO), () =>
      HttpResponse.json({ ...AV.workbench(DEMO), stage: 'handoff', stage_label: 'Handed to you · assess', ...over }),
    ),
    http.get(paths.cases.handoffGates(DEMO), () => HttpResponse.json({ case_id: DEMO, gates: [] })),
  );
}

function summaryWith(failed: Failed, ready = true): void {
  server.use(http.get(paths.cases.assessment(DEMO), () => HttpResponse.json({ ...AV.assessment(DEMO), ready, agent_failed: failed })));
}

describe('the Summary handoff banner', () => {
  it('names the failed agent and says once what to do', async () => {
    resetMockState('released');
    inHandoff();
    summaryWith(RESEARCH);
    renderApp(`/analyst/case/${DEMO}/summary`, { as: USERS.rahul });
    expect(await screen.findByText('Research step handed to you')).toBeInTheDocument();
    const body = screen.getByText(/^The research agent failed on both tries in the assess stage\./);
    expect(body).toHaveTextContent('The research agent failed on both tries in the assess stage. Complete the step by hand below.');
    expect(body.textContent).not.toMatch(/\.\./);
    expect(screen.getAllByText(/Complete the step by hand below/)).toHaveLength(1);
    expect(screen.queryByText(/agent run/)).not.toBeInTheDocument();
  });

  it('lists every failed job when several are waiting', async () => {
    resetMockState('released');
    inHandoff();
    summaryWith({
      step: 'assess',
      title: '2 steps handed to you',
      reason: '2 agent jobs failed on both tries in the assess stage.',
      failures: [
        { agent: 'research', step: 'assess', gate_id: 'gate_a' },
        { agent: 'policy_gap', step: 'assess', gate_id: 'gate_b' },
      ],
    });
    renderApp(`/analyst/case/${DEMO}/summary`, { as: USERS.rahul });
    expect(await screen.findByText('2 steps handed to you')).toBeInTheDocument();
    expect(screen.getByText('research agent · assess stage')).toBeInTheDocument();
    expect(screen.getByText('policy gap agent · assess stage')).toBeInTheDocument();
  });

  it('shows the banner, not "the core is running", for a case handed off before the report', async () => {
    resetMockState('submitted');
    inHandoff({ completeness_decision: null });
    summaryWith(RESEARCH, false);
    renderApp(`/analyst/case/${DEMO}/summary`, { as: USERS.rahul });
    expect(await screen.findByText('Research step handed to you')).toBeInTheDocument();
    expect(screen.getByText('The assessment core is waiting for this step')).toBeInTheDocument();
    expect(screen.queryByText('The assessment core is running')).not.toBeInTheDocument();
    expect(screen.queryByText(/Decision recorded/)).not.toBeInTheDocument();
  });
});

describe('the analyst queue', () => {
  it('offers the handoff filter and sends it to the server', async () => {
    let asked: string | null = null;
    server.use(
      http.get(paths.cases.queue, ({ request }) => {
        asked = new URL(request.url).searchParams.get('stage');
        return HttpResponse.json({ items: [], next_cursor: null, total: 0 });
      }),
    );
    renderApp('/analyst/queue?stage=handoff', { as: USERS.rahul });
    expect(await screen.findByRole('option', { name: 'Handed to you (agent failed)' })).toBeInTheDocument();
    await waitFor(() => expect(asked).toBe('handoff'));
  });
});
