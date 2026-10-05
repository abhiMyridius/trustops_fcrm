/**
 * The case stream: one shared EventSource per case, the access token on its URL, a session refresh
 * before every reopen, a status other components can read, and cache invalidation from events.
 * jsdom has no EventSource, so a fake stands in.
 */
import type { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type * as ClientModule from '@/api/client';
import { setAccessToken } from '@/api/client';
import { POLLING_RETRY_MS, backoffMs } from '@/api/sse/reconnect';
import { useCaseStream, useStreamStatus } from '@/api/sse/useCaseStream';

const refreshSession = vi.hoisted(() => vi.fn<() => Promise<boolean>>());

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof ClientModule>()),
  refreshSession,
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string; lastEventId: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(
    readonly url: string,
    readonly init?: EventSourceInit,
  ) {
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.();
  }

  send(event: Record<string, unknown>, lastEventId = ''): void {
    this.onmessage?.({ data: JSON.stringify(event), lastEventId });
  }

  fail(): void {
    this.onerror?.();
  }
}

const CASE = 'FCRM-2026-0151';
const open = () => FakeEventSource.instances.filter((s) => !s.closed);
const latest = () => FakeEventSource.instances[FakeEventSource.instances.length - 1]!;
const tokenOf = (s: FakeEventSource) => new URL(s.url).searchParams.get('access_token');

function setup() {
  const qc = new QueryClient();
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { invalidate, wrapper };
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  refreshSession.mockReset();
  refreshSession.mockResolvedValue(true);
  vi.stubGlobal('EventSource', FakeEventSource);
  setAccessToken('token-1');
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('useCaseStream', () => {
  it('opens one stream with the current access token on its URL and goes live when it opens', () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useCaseStream(CASE), { wrapper });
    expect(open()).toHaveLength(1);
    const url = new URL(latest().url);
    expect(url.pathname).toBe(`/api/fcrm/cases/${CASE}/stream`);
    expect(tokenOf(latest())).toBe('token-1');
    expect(result.current).toBe('reconnecting');
    act(() => latest().open());
    expect(result.current).toBe('live');
  });

  it('shares one stream between every component of the case and closes it with the last one', () => {
    const { wrapper } = setup();
    const a = renderHook(() => useCaseStream(CASE), { wrapper });
    const b = renderHook(() => useCaseStream(CASE), { wrapper });
    const reader = renderHook(() => useStreamStatus(CASE), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(1);
    act(() => latest().open());
    expect(reader.result.current).toBe('live');
    a.unmount();
    expect(open()).toHaveLength(1);
    b.unmount();
    expect(open()).toHaveLength(0);
    expect(reader.result.current).toBe('polling');
  });

  it('opens a separate stream per case', () => {
    const { wrapper } = setup();
    renderHook(() => useCaseStream('FCRM-2026-0150'), { wrapper });
    renderHook(() => useCaseStream(CASE), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it('invalidates the query keys of each event and the harness key for agent events', () => {
    const { wrapper, invalidate } = setup();
    renderHook(() => useCaseStream(CASE), { wrapper });
    act(() => latest().open());
    act(() => latest().send({ type: 'document.ingested', caseId: CASE, seq: 1, at: '2026-10-05T10:00:00Z' }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['case', CASE, 'documents'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['case', CASE, 'intake'] });
    invalidate.mockClear();
    act(() => latest().send({ type: 'agent.done', caseId: CASE, seq: 2, at: '2026-10-05T10:00:01Z' }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['harness', CASE] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['case', CASE, 'assessment'] });
  });

  it('ignores a frame it cannot parse and an event type it does not know', () => {
    const { wrapper, invalidate } = setup();
    renderHook(() => useCaseStream(CASE), { wrapper });
    act(() => latest().onmessage?.({ data: '{not json', lastEventId: '' }));
    act(() => latest().send({ type: 'made.up', caseId: CASE, seq: 1, at: 'x' }));
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('refreshes the session before reopening after a failure, and reopens with the new token and last event id', async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useCaseStream(CASE), { wrapper });
    const first = latest();
    act(() => first.open());
    act(() => first.send({ type: 'case.updated', caseId: CASE, seq: 7, at: 'x' }, '01HXULID'));
    refreshSession.mockImplementation(async () => {
      setAccessToken('token-2');
      return true;
    });

    act(() => first.fail());
    expect(first.closed).toBe(true);
    expect(result.current).toBe('reconnecting');
    expect(refreshSession).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(backoffMs(1));
    });
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(tokenOf(latest())).toBe('token-2');
    expect(new URL(latest().url).searchParams.get('last_event_id')).toBe('01HXULID');
    act(() => latest().open());
    expect(result.current).toBe('live');
  });

  it('falls back to the event seq as the resume point when a frame carries no id', () => {
    const { wrapper } = setup();
    renderHook(() => useCaseStream(CASE), { wrapper });
    const first = latest();
    act(() => first.send({ type: 'case.updated', caseId: CASE, seq: 9, at: 'x' }));
    act(() => first.fail());
    return act(async () => {
      await vi.advanceTimersByTimeAsync(backoffMs(1));
      expect(new URL(latest().url).searchParams.get('last_event_id')).toBe('9');
    });
  });

  it('reports polling after three failures in a row, then retries the stream on a slow timer', async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useCaseStream(CASE), { wrapper });
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      act(() => latest().fail());
      expect(result.current).toBe('reconnecting');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(backoffMs(attempt));
      });
    }
    act(() => latest().fail());
    expect(result.current).toBe('polling');
    expect(FakeEventSource.instances).toHaveLength(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLLING_RETRY_MS);
    });
    expect(FakeEventSource.instances).toHaveLength(4);
    expect(result.current).toBe('polling');
    act(() => latest().open());
    expect(result.current).toBe('live');
  });

  it('does not reopen a stream whose last component has unmounted while a retry was pending', async () => {
    const { wrapper } = setup();
    const { unmount } = renderHook(() => useCaseStream(CASE), { wrapper });
    act(() => latest().fail());
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLLING_RETRY_MS);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(open()).toHaveLength(0);
  });

  it('does not open a second stream when the session refresh finishes after the component is gone', async () => {
    let finishRefresh: (ok: boolean) => void = () => undefined;
    refreshSession.mockReturnValue(new Promise<boolean>((resolve) => (finishRefresh = resolve)));
    const { wrapper } = setup();
    const { unmount } = renderHook(() => useCaseStream(CASE), { wrapper });
    act(() => latest().fail());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(backoffMs(1));
    });
    unmount();
    await act(async () => {
      finishRefresh(true);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('opens nothing without a case id', () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useCaseStream(undefined), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(result.current).toBe('polling');
  });
});

describe('useStreamStatus', () => {
  it('is polling when nothing has opened a stream, and never opens one itself', () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useStreamStatus(CASE), { wrapper });
    expect(result.current).toBe('polling');
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it('follows a stream opened later by another component', () => {
    const { wrapper } = setup();
    const reader = renderHook(() => useStreamStatus(CASE), { wrapper });
    renderHook(() => useCaseStream(CASE), { wrapper });
    expect(reader.result.current).toBe('reconnecting');
    act(() => latest().open());
    expect(reader.result.current).toBe('live');
  });
});

describe('without EventSource (jsdom)', () => {
  it('stays on polling and opens nothing', () => {
    vi.unstubAllGlobals();
    const { wrapper } = setup();
    const { result } = renderHook(() => useCaseStream(CASE), { wrapper });
    expect(result.current).toBe('polling');
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
