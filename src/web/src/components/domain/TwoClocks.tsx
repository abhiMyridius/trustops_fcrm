import { Clock, PauseCircle } from 'lucide-react';

/** Analyst-active and total elapsed clocks, both computed by S1 (no client arithmetic). */
export function TwoClocks({ active, total, paused }: { active: string; total: string; paused: boolean }) {
  return (
    <p className="inline-flex flex-wrap items-center gap-3 text-[12px] text-text-secondary">
      <span className="inline-flex items-center gap-1">
        {paused ? <PauseCircle aria-hidden size={13} className="text-warning" /> : <Clock aria-hidden size={13} />}
        Analyst {active}
        {paused ? ' (paused)' : ''}
      </span>
      <span className="inline-flex items-center gap-1">
        <Clock aria-hidden size={13} />
        Total {total}
      </span>
    </p>
  );
}
