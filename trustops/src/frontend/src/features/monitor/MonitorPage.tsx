/**
 * 2.27 / §1.10 Harness monitor: header (case, trace id, status, current core), four counters, the
 * seven-guard strip, and a scrolling log (ring buffer of 200 lines) with filters, pause and copy.
 * Snapshot from GET cases/{id}/harness, refreshed by the case stream and polled only while the
 * stream is not live. The PO gets 403 (ADR-19).
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { Copy, ExternalLink, Pause, Play } from 'lucide-react';
import { useHarness } from '@/api/queries/harnessQueries';
import { useCaseStream, type StreamStatus } from '@/api/sse/useCaseStream';
import type { HarnessLine } from '@/api/types/harness';
import { AiLabel } from '@/components/ui/AiMark';
import { Badge, StatusDot } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { ErrorState } from '@/components/ui/EmptyState';
import { ProgressBar } from '@/components/ui/ProgressRing';
import { Select } from '@/components/ui/Select';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { WaveOrnament } from '@/components/ui/WaveOrnament';
import { cn } from '@/lib/cn';
import { useDocumentTitle } from '@/lib/a11y';
import { formatInt } from '@/lib/format';

const STREAM_LABEL: Record<StreamStatus, string> = { live: 'live stream', reconnecting: 'reconnecting', polling: 'polling' };

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function Counter({ label, value, hint, children }: { label: string; value: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface px-4 py-3 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-overline text-text-muted">{label}</p>
      <p className="text-2xl font-extrabold tracking-tight text-virtual-tide tabular-nums">{value}</p>
      {hint && <p className="text-[12px] text-text-secondary">{hint}</p>}
      {children}
    </div>
  );
}

export function MonitorPage() {
  const { caseId = '' } = useParams();
  useDocumentTitle('Harness monitor', caseId);
  const stream = useCaseStream(caseId);
  const [paused, setPaused] = useState(false);
  const q = useHarness(caseId, { poll: !paused && stream !== 'live' });
  const toast = useToast();
  const [who, setWho] = useState('');
  const [guard, setGuard] = useState('');
  const [errorsOnly, setErrorsOnly] = useState(false);
  const logRef = useRef<HTMLOListElement>(null);

  const lines = useMemo(() => {
    const all = q.data?.log ?? [];
    return all.filter((l) => (!who || l.who === who) && (!guard || String(l.guard) === guard) && (!errorsOnly || l.level !== 'info'));
  }, [q.data, who, guard, errorsOnly]);
  const whos = useMemo(() => [...new Set((q.data?.log ?? []).map((l) => l.who))].sort(), [q.data]);

  useEffect(() => {
    if (paused || prefersReducedMotion() || lines.length === 0) return;
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, paused]);

  if (q.isPending) return <SkeletonCard lines={6} label="Loading harness" />;
  // The loaded page's h1 sits in its header; the error and "no run recorded" views keep a heading of their own.
  if (q.isError) {
    return (
      <>
        <h1 className="sr-only">Harness monitor · {caseId}</h1>
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      </>
    );
  }
  const h = q.data;
  const ratio = (h.counters.tokens_used / h.counters.tokens_budget) * 100;

  const copy = async (rows: HarnessLine[]) => {
    const text = rows.map((l) => `${l.at}  ${l.who}  ${l.text}${l.sub ? `  (${l.sub})` : ''}`).join('\n');
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Log copied', `${rows.length} lines`);
    } catch {
      toast.error('Could not copy', 'Clipboard access was refused.');
    }
  };

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <header className="surface-dark-card relative flex flex-wrap items-center justify-between gap-3 overflow-hidden px-5 py-4 text-text-on-dark">
        <WaveOrnament tone="dark" lines={80} className="no-print opacity-70" />
        <div className="relative z-10">
          <h1 className="text-lg font-bold">
            <AiLabel size={20}>Harness monitor · {h.case_id}</AiLabel>
          </h1>
          <p className="font-mono text-[12px] text-text-on-dark-muted">trace {h.trace_id}</p>
        </div>
        <div className="relative z-10 flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-surface px-2.5 py-1">
            <StatusDot
              tone={h.status === 'running' ? 'success' : h.status === 'waiting_on_person' ? 'warning' : 'neutral'}
              label={h.status === 'running' ? 'running' : h.status === 'waiting_on_person' ? 'waiting on a person' : 'idle'}
              pulse={h.status === 'running'}
            />
          </span>
          <Badge tone="info">{h.core}</Badge>
          <Badge tone="neutral">{STREAM_LABEL[stream]}</Badge>
          <a href={h.trace_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[13px] font-semibold text-pulse-green">
            Trace in OpenObserve <ExternalLink aria-hidden size={12} />
          </a>
        </div>
      </header>

      <section aria-label="Counters" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Counter label="Agents run" value={`${h.counters.agents_run} / ${h.counters.agents_total}`} />
        <Counter label="Guards" value={`${h.counters.guards_passed} passed`} hint={`${h.counters.guards_blocked} blocked`} />
        <Counter label="Audit records" value={formatInt(h.counters.audit_records)} hint={h.counters.chain_verified ? 'chain verified' : 'chain not verified'} />
        <Counter label="Tokens" value={`${Math.round(h.counters.tokens_used / 1000)}k / ${Math.round(h.counters.tokens_budget / 1000)}k`} hint={`${h.counters.tokens_target ? `Target ${Math.round(h.counters.tokens_target / 1000)}k. ` : ''}${h.tokens_note}`}>
          <ProgressBar value={ratio} label="Tokens used of the case budget" tone={ratio >= 80 ? 'warning' : 'teal'} showValue={false} />
        </Counter>
      </section>

      <section aria-label="Guards" className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        {h.guards.map((g) => (
          <div
            key={g.n}
            className={cn(
              'flex flex-col gap-1 rounded-md border px-3 py-2',
              g.last === 'block' ? 'border-warning bg-warning-bg' : g.last === 'pass' ? 'border-virtual-tide bg-success-bg' : 'border-border bg-surface',
            )}
          >
            <p className="text-[12px] font-bold">
              Guard {g.n} · {g.name}
            </p>
            <p className="text-[11px] font-semibold uppercase">{g.last === 'block' ? 'last: blocked' : g.last === 'pass' ? 'last: passed' : 'idle'}</p>
            <p className="line-clamp-2 text-[11px] text-text-secondary" title={g.detail}>
              {g.detail}
            </p>
          </div>
        ))}
      </section>

      <section aria-labelledby="log-h" className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-4 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="log-h" className="text-sm font-bold">
            Log · {lines.length} of {h.log.length} lines
          </h2>
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Who" value={who} onChange={(e) => setWho(e.target.value)} options={[{ value: '', label: 'Everyone' }, ...whos.map((w) => ({ value: w, label: w }))]} wrapperClassName="w-44" />
            <Select
              label="Guard"
              value={guard}
              onChange={(e) => setGuard(e.target.value)}
              options={[{ value: '', label: 'Any guard' }, ...h.guards.map((g) => ({ value: String(g.n), label: `Guard ${g.n} · ${g.name}` }))]}
              wrapperClassName="w-48"
            />
            <Checkbox label="Warnings and errors only" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} />
            <Button size="sm" variant="secondary" icon={paused ? <Play aria-hidden size={13} /> : <Pause aria-hidden size={13} />} onClick={() => setPaused((p) => !p)}>
              {paused ? 'Resume' : 'Pause scroll'}
            </Button>
            <Button size="sm" variant="ghost" icon={<Copy aria-hidden size={13} />} onClick={() => void copy(lines)}>
              Copy log
            </Button>
          </div>
        </div>
        <ol ref={logRef} aria-live={paused ? 'off' : 'polite'} aria-relevant="additions" className="max-h-[52vh] overflow-y-auto rounded-md bg-ink-10 p-3 font-mono text-[12px] leading-relaxed text-text-on-dark">
          {lines.length === 0 && <li className="text-text-on-dark-muted">No lines yet.</li>}
          {lines.map((l) => (
            <li key={l.seq} className={cn('flex gap-3 border-b border-ink-9 py-1', l.level !== 'info' && 'text-neural-beam')}>
              <span className="w-12 shrink-0 text-text-on-dark-muted">{l.at.slice(11, 16)}</span>
              <span className="w-32 shrink-0 font-semibold text-pulse-green">{l.who}</span>
              <span className="min-w-0 flex-1">
                {l.text}
                {l.sub && <span className="block text-[11px] text-text-on-dark-muted">{l.sub}</span>}
              </span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
