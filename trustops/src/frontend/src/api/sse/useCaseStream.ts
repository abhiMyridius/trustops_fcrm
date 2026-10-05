/**
 * One EventSource per case, shared through a module-level registry so tabs don't multiply connections.
 * Events invalidate query keys; they never patch the cache.
 *
 * EventSource cannot set headers, so the current access token rides in `?access_token=` (the gateway
 * accepts it on stream URLs only, forwards it as `Authorization` and never logs the query string). The
 * token lives 15 minutes, so after a failure the session is refreshed before the stream is reopened.
 *
 * `useCaseStream` opens (and ref-counts) the stream for a case; `useStreamStatus` only reads its status,
 * so any number of components can ask "is it live?" without opening a second connection. Screens poll
 * only while the status is not `live`.
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { absolute, getAccessToken, refreshSession } from '../client';
import { paths } from '../endpoints';
import { invalidationKeys, parseStreamEvent } from './eventSchemas';
import { MAX_FAILURES_BEFORE_POLLING, POLLING_RETRY_MS, backoffMs } from './reconnect';

export type StreamStatus = 'live' | 'reconnecting' | 'polling';

interface Entry {
  source: EventSource | null;
  refs: number;
  failures: number;
  lastEventId: string;
  status: StreamStatus;
  timer: ReturnType<typeof setTimeout> | null;
  /** Set when the last subscriber leaves, so a pending reopen never resurrects the stream. */
  disposed: boolean;
}

const registry = new Map<string, Entry>();
const statusListeners = new Map<string, Set<() => void>>();

function notify(caseId: string): void {
  statusListeners.get(caseId)?.forEach((listener) => listener());
}

function setStatus(caseId: string, entry: Entry, status: StreamStatus): void {
  if (entry.status === status) return;
  entry.status = status;
  notify(caseId);
}

function connect(caseId: string, entry: Entry, qc: QueryClient): void {
  if (entry.disposed || entry.source) return;
  const url = new URL(absolute(paths.cases.stream(caseId)));
  const token = getAccessToken();
  if (token) url.searchParams.set('access_token', token);
  if (entry.lastEventId) url.searchParams.set('last_event_id', entry.lastEventId);
  const src = new EventSource(url.toString(), { withCredentials: true });
  entry.source = src;

  src.onopen = () => {
    entry.failures = 0;
    setStatus(caseId, entry, 'live');
  };
  src.onmessage = (msg) => {
    const ev = parseStreamEvent(String(msg.data));
    if (!ev) return;
    entry.lastEventId = msg.lastEventId || String(ev.seq);
    invalidationKeys(ev.type, caseId).forEach((queryKey) => void qc.invalidateQueries({ queryKey }));
  };
  src.onerror = () => {
    src.close();
    entry.source = null;
    entry.failures += 1;
    if (entry.failures >= MAX_FAILURES_BEFORE_POLLING) {
      setStatus(caseId, entry, 'polling');
      scheduleReopen(caseId, entry, qc, POLLING_RETRY_MS);
      return;
    }
    setStatus(caseId, entry, 'reconnecting');
    scheduleReopen(caseId, entry, qc, backoffMs(entry.failures));
  };
}

function scheduleReopen(caseId: string, entry: Entry, qc: QueryClient, delayMs: number): void {
  entry.timer = setTimeout(() => {
    entry.timer = null;
    // The access token the failed connection used may have expired: renew it first, then reconnect.
    void refreshSession().then(() => connect(caseId, entry, qc));
  }, delayMs);
}

function acquire(caseId: string, qc: QueryClient): Entry {
  let entry = registry.get(caseId);
  if (!entry) {
    entry = { source: null, refs: 0, failures: 0, lastEventId: '', status: 'reconnecting', timer: null, disposed: false };
    registry.set(caseId, entry);
    notify(caseId);
    connect(caseId, entry, qc);
  }
  entry.refs += 1;
  return entry;
}

function release(caseId: string, entry: Entry): void {
  entry.refs -= 1;
  if (entry.refs > 0) return;
  entry.disposed = true;
  if (entry.timer) clearTimeout(entry.timer);
  entry.source?.close();
  registry.delete(caseId);
  notify(caseId);
}

/**
 * The stream's status for a case: `live` (events are pushed), `reconnecting` (a failure, retrying) or
 * `polling` (no stream, or it failed three times). Reads the shared registry; never opens a connection.
 */
export function useStreamStatus(caseId: string | undefined): StreamStatus {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (!caseId) return () => undefined;
      let listeners = statusListeners.get(caseId);
      if (!listeners) {
        listeners = new Set();
        statusListeners.set(caseId, listeners);
      }
      listeners.add(onChange);
      return () => {
        listeners.delete(onChange);
        if (listeners.size === 0) statusListeners.delete(caseId);
      };
    },
    [caseId],
  );
  return useSyncExternalStore(
    subscribe,
    () => (caseId ? registry.get(caseId)?.status : undefined) ?? 'polling',
    () => 'polling',
  );
}

/** Opens the case's stream for as long as the caller is mounted and returns its status. */
export function useCaseStream(caseId: string | undefined): StreamStatus {
  const qc = useQueryClient();
  useEffect(() => {
    // jsdom has no EventSource: there the status stays `polling` and screens poll.
    if (!caseId || typeof EventSource === 'undefined') return;
    const entry = acquire(caseId, qc);
    return () => release(caseId, entry);
  }, [caseId, qc]);
  return useStreamStatus(caseId);
}
