/** The HTTP client: list unwrapping and the in-memory access token. */
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { apiList, getAccessToken, setAccessToken } from '@/api/client';
import { server } from '@/mocks/server';

const LIST = '/api/fcrm/__test/list';

describe('apiList', () => {
  it('returns the items of a Page{items,next_cursor,total}', async () => {
    server.use(http.get(LIST, () => HttpResponse.json({ items: [{ id: 1 }, { id: 2 }], next_cursor: null, total: 2 })));
    expect(await apiList<{ id: number }>(LIST)).toEqual([{ id: 1 }, { id: 2 }]);
  });

  it('returns an empty page as an empty array', async () => {
    server.use(http.get(LIST, () => HttpResponse.json({ items: [], next_cursor: null, total: 0 })));
    expect(await apiList(LIST)).toEqual([]);
  });

  it('passes a bare array through unchanged', async () => {
    server.use(http.get(LIST, () => HttpResponse.json([{ id: 7 }])));
    expect(await apiList<{ id: number }>(LIST)).toEqual([{ id: 7 }]);
  });

  it('sends the query string', async () => {
    let seen = '';
    server.use(
      http.get(LIST, ({ request }) => {
        seen = new URL(request.url).search;
        return HttpResponse.json({ items: [], next_cursor: null, total: 0 });
      }),
    );
    await apiList(LIST, { query: { stage: 'review', scope: undefined } });
    expect(seen).toBe('?stage=review');
  });

  it('refuses a body that is neither a page nor an array, rather than render it empty', async () => {
    server.use(http.get(LIST, () => HttpResponse.json({ rows: [] })));
    await expect(apiList(LIST)).rejects.toThrow(/Expected a list/);
  });

  it('surfaces a problem response as an ApiError, not a list', async () => {
    server.use(http.get(LIST, () => HttpResponse.json({ title: 'Forbidden', status: 403 }, { status: 403 })));
    await expect(apiList(LIST)).rejects.toMatchObject({ status: 403 });
  });
});

describe('getAccessToken', () => {
  it('reads what setAccessToken stored, and null once cleared', () => {
    setAccessToken('abc');
    expect(getAccessToken()).toBe('abc');
    setAccessToken(null);
    expect(getAccessToken()).toBeNull();
  });
});
