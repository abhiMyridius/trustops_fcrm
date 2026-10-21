import type { Completion } from '@/api/types/common';
import { ProgressRing } from '@/components/ui/ProgressRing';

/**
 * Completion from the server (pinned field set); threshold tick at the submit threshold. The server
 * counts only confirmed change types, so `typesPending` adds a line saying the total can still grow.
 */
export function CompletionRing({ completion, size = 96, typesPending = false }: { completion: Completion; size?: number; typesPending?: boolean }) {
  const below = completion.pct < completion.threshold_pct;
  return (
    <div className="flex items-center gap-4">
      <ProgressRing value={completion.pct} threshold={completion.threshold_pct} label="Required fields complete" size={size} tone={below ? 'warning' : 'teal'} />
      <div className="text-[13px] text-text-secondary">
        <p className="font-semibold text-text-primary">
          {completion.filled} of {completion.required} fields
        </p>
        <p>
          Always-required {completion.always_required_filled} of {completion.always_required_total}
        </p>
        <p className="text-text-muted">Submit threshold {completion.threshold_pct}%</p>
        {typesPending && <p className="mt-1 font-semibold text-warning-text">Confirm change types to see the full total</p>}
      </div>
    </div>
  );
}
