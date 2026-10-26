/**
 * Two small changes beside the planned controls and the owner's values (D-133):
 *  - a suggested planned control can be denied (with a reason) as well as confirmed;
 *  - the Review & submit tab says when a value has other readings, and its Edit dialog lists them.
 */
import { configure, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { paths } from '@/api/endpoints';
import { resetMockState, server } from '@/mocks/server';
import * as AV from '@/mocks/views/analyst';
import * as PO from '@/mocks/views/po';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

const DEMO = 'FCRM-2026-0151';
const REASON = 'The vendor has not committed to this control in the contract.';

describe('a suggested planned control', () => {
  it('can be denied with a reason, beside Confirm', async () => {
    resetMockState('released');
    const risk = AV.risk(DEMO);
    if (!risk) throw new Error('no risk view');
    let posted: unknown = null;
    server.use(
      http.get(paths.cases.workbench(DEMO), () =>
        HttpResponse.json({ ...AV.workbench(DEMO), is_demo: true, review_started: true, locked: false, closed: false }),
      ),
      http.get(paths.cases.risk(DEMO), () =>
        HttpResponse.json({
          ...risk,
          planned: [{ id: 'itm_planned', control_id: 'CTL-T01', description: 'Corridor scope review', owner: 'Unassigned', due: 'No date set', gate_open: true }],
        }),
      ),
      http.post(paths.cases.item(DEMO, 'itm_planned'), async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ ok: true, item_id: 'itm_planned', status: 'removed', replayed: false });
      }),
    );
    renderApp(`/analyst/case/${DEMO}/risk-controls`, { as: USERS.rahul });

    expect(await screen.findByRole('button', { name: 'Confirm planned control' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Deny control' }));
    const save = await screen.findByRole('button', { name: 'Deny with reason' });
    expect(save).toBeDisabled(); // a reason is needed (rule 6)
    expect(screen.getByText(/never scored, so the score does not change/)).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: /Reason/ }), REASON);
    await userEvent.click(save);

    await vi.waitFor(() => expect(posted).toMatchObject({ decision: 'remove', reason: REASON }));
  });
});

describe('the Review & submit tab', () => {
  it('says when a value has other readings', async () => {
    resetMockState('submitted');
    const review = PO.poReview(DEMO);
    if (!review) throw new Error('no review view');
    const rows = review.rows.map((r, i) =>
      i === 0 && r.value ? { ...r, alternatives: [{ value: 'Another reading', source: { label: 'business_case.pdf p.2 l.4' } }] } : r,
    );
    server.use(http.get(paths.cases.review(DEMO), () => HttpResponse.json({ ...review, rows })));
    renderApp(`/case/${DEMO}/review`, { as: USERS.priya });

    expect(await screen.findByText('1 other value found. Edit to switch.')).toBeInTheDocument();
  });
});
