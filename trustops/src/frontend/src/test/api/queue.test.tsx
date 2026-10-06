/**
 * The queue screens do not page: the API's default page is 50 and the live seed has 66 closed
 * cases, so the newest sixteen (the demo case among them) were never listed. The hook asks for the
 * most one page can hold.
 */
import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeQueryClient } from '@/app/providers';
import { paths } from '@/api/endpoints';
import { QUEUE_PAGE_LIMIT, useQueue } from '@/api/queries/analystQueries';
import { server } from '@/mocks/server';

function wrapper() {
  const client = makeQueryClient();
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useQueue', () => {
  it('asks for a whole page of the queue, with the filters', async () => {
    let seen: URL | null = null;
    server.use(
      http.get(paths.cases.queue, ({ request }) => {
        seen = new URL(request.url);
        return HttpResponse.json({ items: [], next_cursor: null, total: 0 });
      }),
    );
    const { result } = renderHook(() => useQueue({ scope: 'all', stage: 'closed' }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(QUEUE_PAGE_LIMIT).toBe(200);
    expect(seen).not.toBeNull();
    const url = seen as unknown as URL;
    expect(url.searchParams.get('limit')).toBe('200');
    expect(url.searchParams.get('scope')).toBe('all');
    expect(url.searchParams.get('stage')).toBe('closed');
  });
});
