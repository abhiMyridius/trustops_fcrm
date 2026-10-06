/**
 * The examiner answers one call in one of two ways: the answer itself, or 202 {question_id,
 * status: "pending"} once S4 has waited a minute. The web polls the stored answer instead of
 * showing the pending body as an answer.
 */
import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeQueryClient } from '@/app/providers';
import { paths } from '@/api/endpoints';
import { awaitExaminerAnswer, useExaminerAsk } from '@/api/queries/assuranceQueries';
import { server } from '@/mocks/server';

const CASE = 'FCRM-2026-0151';
const QUESTION = 'exq_1';
const answer = {
  question_id: QUESTION,
  question: 'Why was it routed to the committee?',
  case_id: CASE,
  refused: false,
  sentences: [{ t: 'The score crossed a band edge.', refs: [91] }],
  references: [],
};

function wrapper() {
  const client = makeQueryClient();
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('the examiner ask', () => {
  it('returns the answer when the ask answers at once', async () => {
    server.use(http.post(paths.audit.examinerAsk, () => HttpResponse.json(answer)));
    const { result } = renderHook(() => useExaminerAsk(), { wrapper: wrapper() });
    result.current.mutate({ question: answer.question, case_id: CASE });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.sentences).toHaveLength(1);
  });

  it('polls the stored answer after a 202 pending instead of showing the pending body', async () => {
    let polls = 0;
    server.use(
      http.post(paths.audit.examinerAsk, () =>
        HttpResponse.json({ question_id: QUESTION, case_id: CASE, status: 'pending' }, { status: 202 }),
      ),
      http.get(paths.audit.examinerAnswer(QUESTION), () => {
        polls += 1;
        return polls < 3
          ? HttpResponse.json({ question_id: QUESTION, case_id: CASE, status: 'pending' }, { status: 202 })
          : HttpResponse.json(answer);
      }),
    );
    const found = await awaitExaminerAnswer(QUESTION, 5, 10);
    expect(found.refused).toBe(false);
    expect(found.sentences[0]?.refs).toEqual([91]);
    expect(polls).toBe(3);
  });

  it('gives up with a plain message when the answer never arrives', async () => {
    server.use(
      http.get(paths.audit.examinerAnswer(QUESTION), () =>
        HttpResponse.json({ question_id: QUESTION, case_id: CASE, status: 'pending' }, { status: 202 }),
      ),
    );
    await expect(awaitExaminerAnswer(QUESTION, 1, 3)).rejects.toThrow(/has not answered/);
  });
});
