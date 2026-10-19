/**
 * ADR-73: "Ask the owner" names the field and the control apart. Two optional dropdowns, filled from
 * the case (its submission's fields and its assessed controls), may both be picked; the body is
 * `{ text, field_id, control_id }`, and the analyst's thread list shows the control beside the anchor.
 */
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import * as A from '@/mocks/actions';
import { getDB } from '@/mocks/db';
import { resetMockState, server } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const C = 'FCRM-2026-0151';
const LICENCE = 'Which licence does the payout partner hold in Mexico?';

function capture(url: string): unknown[] {
  const bodies: unknown[] = [];
  server.use(
    http.post(url, async ({ request }) => {
      bodies.push(await request.clone().json());
    }),
  );
  return bodies;
}

async function askCard(): Promise<HTMLElement> {
  // The first render of a file waits on a cold transform when files run in parallel.
  return (await screen.findByRole('heading', { name: 'Ask the owner' }, { timeout: 5000 })).closest('section') as HTMLElement;
}

describe('Ask the owner: a field and a control (ADR-73)', () => {
  it('offers the case fields and controls as two optional dropdowns, and keeps the helper text', async () => {
    resetMockState('submitted');
    renderApp(`/analyst/case/${C}/inbox`, { as: USERS.rahul });
    const card = await askCard();

    const field = within(card).getByLabelText(/About a field/);
    const control = within(card).getByLabelText(/About a control/);
    expect(within(card).queryByLabelText(/About field or control/)).not.toBeInTheDocument();
    expect(field).toHaveValue('');
    expect(control).toHaveValue('');
    expect(await within(field).findByRole('option', { name: /^F11 · / })).toBeInTheDocument();
    expect((await within(control).findAllByRole('option', { name: /^CTL-[A-Z]\d{2} · / })).length).toBeGreaterThan(0);
    expect(within(card).getByText(/Picking a field lets the owner set its new value with the answer/)).toBeInTheDocument();
  });

  it('posts both ids and lists the control next to the field', async () => {
    resetMockState('submitted');
    const posted = capture(paths.cases.questions(C));
    renderApp(`/analyst/case/${C}/inbox`, { as: USERS.rahul });
    const card = await askCard();

    const control = within(card).getByLabelText(/About a control/);
    const option = (await within(control).findAllByRole('option', { name: /^CTL-/ }))[0] as HTMLOptionElement;
    const controlId = option.value;
    await userEvent.type(within(card).getByLabelText('Question 1'), LICENCE);
    await userEvent.selectOptions(within(card).getByLabelText(/About a field/), await within(card).findByRole('option', { name: /^F11 · / }));
    await userEvent.selectOptions(control, option);
    await userEvent.click(within(card).getByRole('button', { name: /Send · 1 question/ }));

    expect(await screen.findByText(/Questions to Priya Nair/)).toBeInTheDocument();
    expect(posted).toEqual([{ questions: [{ text: LICENCE, field_id: 'F11', control_id: controlId }] }]);
    const [q] = getDB().demo.adhoc;
    expect(q).toMatchObject({ anchor: 'F11', field_id: 'F11', control_id: controlId });
    expect(screen.getByText('Anchor: F11')).toBeInTheDocument();
    expect(screen.getByText(`Control: ${controlId}`)).toBeInTheDocument();
  });

  it('sends nulls when neither is picked', async () => {
    resetMockState('submitted');
    const posted = capture(paths.cases.questions(C));
    renderApp(`/analyst/case/${C}/inbox`, { as: USERS.rahul });
    const card = await askCard();

    await userEvent.type(within(card).getByLabelText('Question 1'), 'Anything else we should know?');
    await userEvent.click(within(card).getByRole('button', { name: /Send · 1 question/ }));

    expect(await screen.findByText(/Questions to Priya Nair/)).toBeInTheDocument();
    expect(posted).toEqual([{ questions: [{ text: 'Anything else we should know?', field_id: null, control_id: null }] }]);
  });

  it('the owner reads the field, never the control', async () => {
    resetMockState('submitted');
    A.askOwner([{ text: LICENCE, field_id: 'F11', control_id: 'CTL-T01' }]);
    renderApp(`/case/${C}/inbox`, { as: USERS.priya });

    const card = (await screen.findByRole('heading', { name: 'Questions from FCRM' })).closest('section') as HTMLElement;
    expect(within(card).getByText(LICENCE)).toBeInTheDocument();
    expect(within(card).getByLabelText(/New value for/)).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/CTL-/);
  });

  it('the mock still takes an older client anchor', () => {
    resetMockState('submitted');
    A.askOwner([{ text: LICENCE, anchor: 'F11' }]);

    expect(getDB().demo.adhoc[0]).toMatchObject({ anchor: 'F11', field_id: 'F11', control_id: null });
  });
});
