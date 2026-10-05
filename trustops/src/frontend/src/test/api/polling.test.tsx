/**
 * The two screens that used to poll unconditionally now poll only when told to (`poll`), which the
 * screens set from `useStreamStatus(id) !== 'live'`: with a live case stream the events refresh them.
 */
import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http, type JsonBodyType } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeQueryClient } from '@/app/providers';
import { paths } from '@/api/endpoints';
import { useAssessment } from '@/api/queries/analystQueries';
import { useDocuments } from '@/api/queries/caseQueries';
import { server } from '@/mocks/server';

const CASE = 'FCRM-2026-0151';

function wrapper() {
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retry: false } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const settle = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Serves `body` at `path` and counts the requests. */
function serve(path: string, body: JsonBodyType): { hits: () => number } {
  let hits = 0;
  server.use(
    http.get(path, () => {
      hits += 1;
      return HttpResponse.json(body);
    }),
  );
  return { hits: () => hits };
}

describe('useAssessment', () => {
  const unfinished = { ready: false };

  it('polls an unfinished assessment while poll is true', async () => {
    const served = serve(paths.cases.assessment(CASE), unfinished);
    renderHook(() => useAssessment(CASE, { poll: true }), { wrapper: wrapper() });
    await waitFor(() => expect(served.hits()).toBeGreaterThanOrEqual(2), { timeout: 5000 });
  });

  it('fetches once and does not poll when poll is false', async () => {
    const served = serve(paths.cases.assessment(CASE), unfinished);
    const { result } = renderHook(() => useAssessment(CASE, { poll: false }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await settle(1800);
    expect(served.hits()).toBe(1);
  });

  it('stops polling once the assessment is ready', async () => {
    const served = serve(paths.cases.assessment(CASE), { ready: true });
    const { result } = renderHook(() => useAssessment(CASE, { poll: true }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await settle(1800);
    expect(served.hits()).toBe(1);
  });
});

describe('useDocuments', () => {
  const reading = { documents: [], assistant: { state: 'reading', line: 'Reading' } };

  it('polls while the assistant is reading and poll is true', async () => {
    const served = serve(paths.cases.documents(CASE), reading);
    renderHook(() => useDocuments(CASE, { poll: true }), { wrapper: wrapper() });
    await waitFor(() => expect(served.hits()).toBeGreaterThanOrEqual(2), { timeout: 5000 });
  });

  it('fetches once and does not poll when poll is false', async () => {
    const served = serve(paths.cases.documents(CASE), reading);
    const { result } = renderHook(() => useDocuments(CASE, { poll: false }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await settle(1500);
    expect(served.hits()).toBe(1);
  });
});
