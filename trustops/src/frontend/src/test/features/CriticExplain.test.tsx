/**
 * A critic finding is decided by the analyst's explanation (S1 accepts only `explain`, with a reason of at least
 * 20 characters). Before, the Summary showed the finding with no way to decide it, so the review could not
 * finish and the recommendation was never drafted (seen live on 15 Oct, case 0152).
 */
import { configure, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { AssessmentSummary } from '@/api/types/analyst';
import { resetMockState, server } from '@/mocks/server';
import * as AV from '@/mocks/views/analyst';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

const DEMO = 'FCRM-2026-0151';
const FINDING = '[unsourced_claim] The jurisdiction notes are not supported by the source.';
const ITEM = 'itm_critic_1';

function withFinding(gateOpen: boolean): void {
  server.use(
    http.get(paths.cases.assessment(DEMO), () => {
      const base = AV.assessment(DEMO) as AssessmentSummary;
      return HttpResponse.json({
        ...base,
        ready: true,
        critic_findings: [{ id: ITEM, type: 'other', severity: 'high', text: FINDING, records: [23], target: null, gate_id: 'gate_1', gate_open: gateOpen }],
      });
    }),
  );
}

describe('explaining a critic finding', () => {
  it('records the explanation with decision explain and the reason', async () => {
    resetMockState('submitted');
    withFinding(true);
    let body: unknown = null;
    server.use(
      http.post(paths.cases.item(DEMO, ITEM), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, item_id: ITEM, status: 'explained', replayed: false });
      }),
    );
    renderApp(`/analyst/case/${DEMO}/summary`, { as: USERS.rahul });
    await userEvent.click(await screen.findByRole('button', { name: 'Explain…' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText(/Reason/), 'The destination field was read from page 3 of the business case.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record explanation' }));
    await waitFor(() => expect(body).toEqual({ decision: 'explain', reason: 'The destination field was read from page 3 of the business case.' }));
  });

  it('shows an explained finding as decided, with no button', async () => {
    resetMockState('submitted');
    withFinding(false);
    renderApp(`/analyst/case/${DEMO}/summary`, { as: USERS.rahul });
    expect(await screen.findByText('Explained')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Explain…' })).not.toBeInTheDocument();
  });
});
