/**
 * ADR-73 and D-120 through the real router, guards and MSW handlers: the analyst asks the owner a
 * question at any time from the Inbox (and reaches it from the Summary as soon as the case is
 * assigned), composes the clarification round before its send decides the gate, and the owner answers
 * an ad-hoc question with words and, for a question about a field, its new value.
 */
import { screen, waitFor, within } from '@testing-library/react';
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

/** Capture the JSON body of every request to `url` and let the mock handler answer it. */
function capture(url: string): unknown[] {
  const bodies: unknown[] = [];
  server.use(
    http.post(url, async ({ request }) => {
      bodies.push(await request.clone().json());
    }),
  );
  return bodies;
}

describe('the analyst asks the owner at any time (ADR-73)', () => {
  it('sends a question from the Inbox and lists it with its status', async () => {
    resetMockState('submitted');
    const posted = capture(paths.cases.questions(C));
    renderApp(`/analyst/case/${C}/inbox`, { as: USERS.rahul });

    const card = (await screen.findByRole('heading', { name: 'Ask the owner' })).closest('section') as HTMLElement;
    await userEvent.type(within(card).getByLabelText('Question 1'), LICENCE);
    await userEvent.type(within(card).getByLabelText(/About field or control/), 'F11');
    await userEvent.click(within(card).getByRole('button', { name: /Send · 1 question/ }));

    expect(await screen.findByText(/Questions to Priya Nair/)).toBeInTheDocument();
    expect(screen.getByText(LICENCE)).toBeInTheDocument();
    expect(posted).toEqual([{ questions: [{ text: LICENCE, anchor: 'F11' }] }]);
    expect(getDB().demo.adhoc).toHaveLength(1);
    // The one round is untouched: no decision, nothing sent.
    expect(getDB().demo.completeness).toBeNull();
    expect(getDB().demo.roundSent).toBe(false);
  });

  it('refuses a question that names a reserved word and says which', async () => {
    resetMockState('submitted');
    renderApp(`/analyst/case/${C}/inbox`, { as: USERS.rahul });

    const card = (await screen.findByRole('heading', { name: 'Ask the owner' })).closest('section') as HTMLElement;
    await userEvent.type(within(card).getByLabelText('Question 1'), 'What band would you expect?');
    await userEvent.click(within(card).getByRole('button', { name: /Send · 1 question/ }));

    expect(await screen.findByText(/reserves \(band\)/)).toBeInTheDocument();
    expect(getDB().demo.adhoc).toHaveLength(0);
  });

  it('says why no question can be asked once the pack is with the committee', async () => {
    resetMockState('released');
    renderApp(`/analyst/case/${C}/inbox`, { as: USERS.rahul });

    expect(await screen.findByText(/with the decision makers/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ask the owner' })).not.toBeInTheDocument();
  });

  it('reaches the Inbox from the Summary with one click', async () => {
    resetMockState('submitted');
    const { router } = renderApp(`/analyst/case/${C}/summary`, { as: USERS.rahul });

    await userEvent.click(await screen.findByRole('button', { name: 'Ask the owner a question' }));

    await waitFor(() => expect(router.state.location.pathname).toBe(`/analyst/case/${C}/inbox`));
    expect(await screen.findByRole('heading', { name: 'Ask the owner' })).toBeInTheDocument();
  });
});

describe('the clarification round is composed before it is sent (D-120)', () => {
  it('opens the composer from "Ask now" without deciding the gate, and sends the edited questions', async () => {
    resetMockState('submitted');
    const decided = capture(paths.cases.completeness(C));
    const sent = capture(paths.cases.clarifications(C));
    const { router } = renderApp(`/analyst/case/${C}/summary`, { as: USERS.rahul });

    await userEvent.click(await screen.findByRole('button', { name: /^Ask now/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/analyst/case/${C}/inbox`));
    expect(decided).toEqual([]);

    const composer = (await screen.findByRole('heading', { name: /Clarification round · draft/ })).closest('section') as HTMLElement;
    const first = within(composer).getByLabelText('Question 1');
    await userEvent.clear(first);
    await userEvent.type(first, 'Is the partner licensed by the CNBV?');
    await userEvent.click(within(composer).getByRole('button', { name: /Send round/ }));

    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0] as { questions: { id: string; text: string; keep: boolean }[] };
    expect(body.questions[0]).toMatchObject({ id: 'Q1', text: 'Is the partner licensed by the CNBV?', keep: true });
    expect(await screen.findByText(/Round 1 · sent/)).toBeInTheDocument();
    expect(getDB().demo.completeness).toBe('ask');
  });
});

describe('the owner answers an ad-hoc question (ADR-73)', () => {
  it('answers in words and sets the field the question is about', async () => {
    resetMockState('submitted');
    A.askOwner([{ text: LICENCE, anchor: 'F11' }]);
    const answered = capture(paths.cases.answerThread(C, 'aq_1'));
    renderApp(`/case/${C}/inbox`, { as: USERS.priya });

    const card = (await screen.findByRole('heading', { name: 'Questions from FCRM' })).closest('section') as HTMLElement;
    expect(within(card).getByText(LICENCE)).toBeInTheDocument();
    await userEvent.type(within(card).getByLabelText('Your answer'), 'Licence MX-114 from the CNBV.');
    await userEvent.type(within(card).getByLabelText(/New value for/), 'MX-114');
    await userEvent.click(within(card).getByRole('button', { name: 'Send answer' }));

    expect(await screen.findByText(/Your answer ·/)).toBeInTheDocument();
    expect(answered).toEqual([
      { text: 'Licence MX-114 from the CNBV.', documents: [], field_values: [{ field_id: 'F11', value: 'MX-114' }] },
    ]);
    const [q] = getDB().demo.adhoc;
    expect(q?.answer?.changed).toEqual(['F11']);
  });

  it('sends no value when the owner leaves it empty', async () => {
    resetMockState('submitted');
    A.askOwner([{ text: 'Anything else we should know?', anchor: null }]);
    const answered = capture(paths.cases.answerThread(C, 'aq_1'));
    renderApp(`/case/${C}/inbox`, { as: USERS.priya });

    await userEvent.type(await screen.findByLabelText('Your answer'), 'Nothing more.');
    expect(screen.queryByLabelText(/New value for/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Send answer' }));

    await waitFor(() => expect(answered).toHaveLength(1));
    expect(answered[0]).toEqual({ text: 'Nothing more.', documents: [], field_values: [] });
  });
});
