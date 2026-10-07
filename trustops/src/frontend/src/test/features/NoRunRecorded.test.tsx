/**
 * A case with no assessment run is not a failure (D-106). S1 answers the harness monitor with a 404 whose
 * problem type is `no_run_recorded`, and the analyst's Recommendation tab with a 409 `not_ready`. The
 * screens show both as information with no Retry. A missing or not-yours case, and a case that does have
 * a run, keep the red error.
 */
import { screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { problem } from '@/mocks/http';
import { resetMockState, server } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const SEEDED = 'FCRM-2026-0118';
const DEMO = 'FCRM-2026-0151';

function noRetry(): void {
  expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  expect(screen.queryByText(/Trace id/)).not.toBeInTheDocument();
}

describe('the harness monitor of a case with no run', () => {
  it.each([
    ['analyst', USERS.rahul],
    ['committee', USERS.ana],
    ['observer', USERS.observer],
  ])('shows information, a heading and no Retry to the %s', async (_role, user) => {
    renderApp(`/monitor/${SEEDED}`, { as: user });
    expect(await screen.findByText(/^No run is recorded for this case yet\./)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: `Harness monitor · ${SEEDED}` })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText(/not in your view/)).not.toBeInTheDocument();
    noRetry();
  });

  it('shows the same information while the real answer is the S1 problem body', async () => {
    server.use(
      http.get(paths.cases.harness(SEEDED), () =>
        HttpResponse.json(
          { type: 'https://trustops.local/errors/no_run_recorded', title: 'No run recorded', status: 404, detail: `no harness run is recorded for case ${SEEDED}` },
          { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    renderApp(`/monitor/${SEEDED}`, { as: USERS.observer });
    expect(await screen.findByText(/^No run is recorded for this case yet\./)).toBeInTheDocument();
    noRetry();
  });

  it('still says "not in your view", with Retry and a heading, for a case that is missing or not yours', async () => {
    renderApp('/monitor/FCRM-2099-0001', { as: USERS.rahul });
    expect(await screen.findByText('This case is not in your view.')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Harness monitor · FCRM-2099-0001' })).toBeInTheDocument();
    expect(screen.queryByText(/No run is recorded/)).not.toBeInTheDocument();
  });

  it('shows the monitor itself, with one h1, once the case has a run', async () => {
    resetMockState('closed');
    renderApp(`/monitor/${DEMO}`, { as: USERS.observer });
    expect(await screen.findByRole('heading', { level: 1, name: `Harness monitor · ${DEMO}` })).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.queryByText(/No run is recorded/)).not.toBeInTheDocument();
  });
});

describe('the analyst Recommendation tab', () => {
  it('shows information, not a red error, for a case with no assessment run', async () => {
    renderApp(`/analyst/case/${SEEDED}/recommendation`, { as: USERS.rahul });
    expect(await screen.findByText(/^No recommendation is drafted for this case/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    noRetry();
  });

  it('keeps the red error with Retry for a case that does have a run', async () => {
    resetMockState('released');
    server.use(http.get(paths.cases.recommendation(DEMO), () => problem(409, 'Not ready', 'the narrative has not drafted the recommendation yet')));
    renderApp(`/analyst/case/${DEMO}/recommendation`, { as: USERS.rahul });
    expect(await screen.findByText('the narrative has not drafted the recommendation yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText(/^No recommendation is drafted for this case/)).not.toBeInTheDocument();
  });

  it('keeps the red error for a case with no run when the failure is not "not ready"', async () => {
    server.use(http.get(paths.cases.recommendation(SEEDED), () => problem(500, 'Internal error', 'An unexpected error occurred.')));
    renderApp(`/analyst/case/${SEEDED}/recommendation`, { as: USERS.rahul });
    expect(await screen.findByText('An unexpected error occurred.')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument());
    expect(screen.queryByText(/^No recommendation is drafted for this case/)).not.toBeInTheDocument();
  });

  it('shows the recommendation as before when the case has one', async () => {
    resetMockState('released');
    renderApp(`/analyst/case/${DEMO}/recommendation`, { as: USERS.rahul });
    expect(await screen.findByText(/Corridor-scope vendor DD review/)).toBeInTheDocument();
    expect(screen.queryByText(/^No recommendation is drafted for this case/)).not.toBeInTheDocument();
  });
});
