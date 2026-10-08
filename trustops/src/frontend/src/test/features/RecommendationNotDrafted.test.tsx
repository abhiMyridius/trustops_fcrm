/**
 * D-118: on a case WITH an assessment run, a recommendation the narrative has not drafted yet (S1's 409
 * not_ready) is information that says what it waits for, not a red error with Retry. Real errors stay red.
 * The case with no run at all is D-106's (NoRunRecorded.test.tsx).
 */
import { configure, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { problem } from '@/mocks/http';
import { resetMockState, server } from '@/mocks/server';
import * as AV from '@/mocks/views/analyst';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

const DEMO = 'FCRM-2026-0151';
const NOT_DRAFTED = 'the narrative has not drafted the recommendation yet';

function inHandoff(): void {
  server.use(http.get(paths.cases.workbench(DEMO), () => HttpResponse.json({ ...AV.workbench(DEMO), stage: 'handoff', stage_label: 'Handed to you · assess' })));
}

describe('the Recommendation tab of a case with a run', () => {
  it('says what a not-yet-drafted recommendation waits for, with no Retry', async () => {
    resetMockState('released');
    server.use(http.get(paths.cases.recommendation(DEMO), () => problem(409, 'Not ready', NOT_DRAFTED)));
    renderApp(`/analyst/case/${DEMO}/recommendation`, { as: USERS.rahul });
    expect(await screen.findByText(/drafted once every review decision is made/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.queryByText(NOT_DRAFTED)).not.toBeInTheDocument();
  });

  it('points to the step handed to the analyst while the case is in handoff', async () => {
    resetMockState('released');
    inHandoff();
    server.use(http.get(paths.cases.recommendation(DEMO), () => problem(409, 'Not ready', NOT_DRAFTED)));
    renderApp(`/analyst/case/${DEMO}/recommendation`, { as: USERS.rahul });
    expect(await screen.findByText(/waiting for the step handed to you on the Summary tab/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });

  it('keeps a real error red with Retry', async () => {
    resetMockState('released');
    server.use(http.get(paths.cases.recommendation(DEMO), () => problem(500, 'Internal error', 'An unexpected error occurred.')));
    renderApp(`/analyst/case/${DEMO}/recommendation`, { as: USERS.rahul });
    expect(await screen.findByText('An unexpected error occurred.')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument());
  });
});
