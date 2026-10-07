/**
 * Analyst Activity tab, Verify chain (D-105). S4 answers {case_id, record_count, ok, first_break_seq};
 * the page once read `count` and `head_hash`, and shortHash(undefined) took the whole route down with
 * "Something went wrong". These specs render the banner from the real shape and prove that an answer
 * with fields missing cannot crash the page.
 */
import { configure, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { resetMockState, server } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

// The route chunks load lazily; with the whole suite running in parallel the first render can take longer than 1 s.
configure({ asyncUtilTimeout: 5000 });

const CASE = 'FCRM-2026-0151';
const ROUTE = `/analyst/case/${CASE}/activity`;

async function openActivityAndVerify(): Promise<void> {
  renderApp(ROUTE, { as: USERS.rahul });
  await userEvent.click(await screen.findByRole('button', { name: 'Verify chain' }));
}

describe('Verify chain on the analyst Activity tab', () => {
  it('shows the record count and that the chain verified, from the mock backend', async () => {
    resetMockState('closed');
    await openActivityAndVerify();
    expect(await screen.findByText('67 records, chain verified')).toBeInTheDocument();
    expect(screen.queryByText(/Something went wrong/)).not.toBeInTheDocument();
  });

  it('shows the count and a verified chain from the exact shape S4 answers', async () => {
    resetMockState('closed');
    server.use(http.get(paths.audit.verify(CASE), () => HttpResponse.json({ case_id: CASE, record_count: 9, ok: true, first_break_seq: null })));
    await openActivityAndVerify();
    expect(await screen.findByText('9 records, chain verified')).toBeInTheDocument();
  });

  it('names the first broken record when the chain is broken', async () => {
    resetMockState('closed');
    server.use(http.get(paths.audit.verify(CASE), () => HttpResponse.json({ case_id: CASE, record_count: 9, ok: false, first_break_seq: 4 })));
    await openActivityAndVerify();
    expect(await screen.findByText('9 records, chain broken at record 4')).toBeInTheDocument();
  });

  it.each([
    ['an empty object', {}],
    ['only ok', { ok: true }],
    ['only a count', { record_count: 3 }],
    ['the old field names', { ok: true, count: 7, head_hash: 'abc', first_broken_seq: null, verified_at: '2026-10-07T00:00:00Z' }],
    ['null fields', { case_id: null, record_count: null, ok: null, first_break_seq: null }],
  ])('does not crash the page when the answer is %s', async (_name, body) => {
    resetMockState('closed');
    server.use(http.get(paths.audit.verify(CASE), () => HttpResponse.json(body)));
    await openActivityAndVerify();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Verify chain' })).toBeEnabled());
    expect(screen.queryByText(/Something went wrong/)).not.toBeInTheDocument();
    // The activity list is still there: the route did not fall over.
    expect(screen.getByRole('list', { name: /Activity, newest first/ })).toBeInTheDocument();
  });
});
