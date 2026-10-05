/**
 * One EventSource per case, shared through a module-level registry so tabs don't multiply connections.
 * Events invalidate query keys; they never patch the cache. In MSW mode there is no stream endpoint,
 * so the hook reports `polling` straight away and the screens' own refetch intervals keep data fresh.
 */
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { absolute } from '../client';
import { paths } from '../endpoints';
import { invalidationMap, parseStreamEvent } from './eventSchemas';
import { MAX_FAILURES_BEFORE_POLLING, backoffMs } from './reconnect';

export type StreamStatus = 'live' | 'reconnecting' | 'polling' | 'off';

interface Entry {
  source: EventSource | null;
  refs: number;
  failures: number;
  lastSeq: number;
  listeners: Set<(s: StreamStatus) => void>;
  status: StreamStatus;
}

const registry = new Map<string, Entry>();

const apiMode = (import.meta.env.VITE_API_MODE as string | undefined) ?? 'msw';

export function useCaseStream(caseId: string | undefined): StreamStatus {
  const qc = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>(apiMode === 'live' ? 'reconnecting' : 'polling');

  useEffect(() => {
    if (!caseId || apiMode !== 'live' || typeof EventSource === 'undefined') return;
    let entry = registry.get(caseId);
    if (!entry) {
      entry = { source: null, refs: 0, failures: 0, lastSeq: 0, listeners: new Set(), status: 'reconnecting' };
      registry.set(caseId, entry);
    }
    const e = entry;
    e.refs += 1;
    e.listeners.add(setStatus);

    const emit = (s: StreamStatus) => {
      e.status = s;
      e.listeners.forEach((l) => l(s));
    };

    const open = () => {
      if (e.source) return;
      const url = new URL(absolute(paths.cases.stream(caseId)));
      if (e.lastSeq) url.searchParams.set('last_event_id', String(e.lastSeq));
      const src = new EventSource(url.toString(), { withCredentials: true });
      e.source = src;
      src.onopen = () => {
        e.failures = 0;
        emit('live');
      };
      src.onmessage = (msg) => {
        const ev = parseStreamEvent(String(msg.data));
        if (!ev) return;
        e.lastSeq = Math.max(e.lastSeq, ev.seq);
        const subs = invalidationMap[ev.type] ?? [];
        subs.forEach((sub) => void qc.invalidateQueries({ queryKey: sub ? ['case', caseId, sub] : ['case', caseId] }));
        if (ev.type.startsWith('harness.') || ev.type.startsWith('agent.') || ev.type.startsWith('guard.')) {
          void qc.invalidateQueries({ queryKey: ['harness', caseId] });
        }
      };
      src.onerror = () => {
        src.close();
        e.source = null;
        e.failures += 1;
        if (e.failures >= MAX_FAILURES_BEFORE_POLLING) {
          emit('polling');
          return;
        }
        emit('reconnecting');
        setTimeout(open, backoffMs(e.failures));
      };
    };
    open();

    return () => {
      e.refs -= 1;
      e.listeners.delete(setStatus);
      if (e.refs <= 0) {
        e.source?.close();
        registry.delete(caseId);
      }
    };
  }, [caseId, qc]);

  return status;
}
