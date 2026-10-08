import { Check } from 'lucide-react';
import type { StageStep } from '@/api/types/common';
import { cn } from '@/lib/cn';

/** Ordered list of steps; the current one carries aria-current="step". */
export function Stepper({ steps, label, compact }: { steps: StageStep[]; label: string; compact?: boolean }) {
  return (
    <nav aria-label={label}>
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-2">
        {steps.map((s, i) => (
          <li key={s.key} aria-current={s.state === 'current' ? 'step' : undefined} className="flex items-center gap-1">
            <span
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-semibold',
                s.state === 'done' && 'border-virtual-tide bg-success-bg text-band-1-text',
                s.state === 'current' && 'border-quantum-leaf bg-quantum-leaf text-text-on-dark',
                s.state === 'upcoming' && 'border-border bg-surface text-text-muted',
              )}
            >
              {s.state === 'done' && <Check aria-hidden size={12} />}
              {s.label}
              <span className="sr-only">{s.state === 'done' ? ' (done)' : s.state === 'current' ? ' (current)' : ' (upcoming)'}</span>
            </span>
            {i < steps.length - 1 && !compact && <span aria-hidden className="h-px w-3 bg-border-strong" />}
          </li>
        ))}
      </ol>
    </nav>
  );
}
