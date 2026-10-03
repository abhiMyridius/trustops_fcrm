import type { Origin } from '@/api/types/common';
import { cn } from '@/lib/cn';

const LABEL: Record<Origin, string> = {
  extracted: 'Extracted',
  reused: 'Reused',
  answered: 'Owner answer',
  owner_declared: 'Owner declared',
  analyst_override: 'Analyst override',
  jurisdiction_list: 'Jurisdiction list',
  open: 'Open',
};

const TONE: Record<Origin, string> = {
  extracted: 'bg-info-bg text-chart-deep-1',
  reused: 'bg-quantum-leaf-tint-3 text-carbon-moss',
  answered: 'bg-surface-secondary text-text-secondary',
  owner_declared: 'bg-warning-bg text-warning-text',
  analyst_override: 'bg-danger-bg text-danger',
  jurisdiction_list: 'bg-surface-tertiary text-text-secondary',
  open: 'bg-surface-tertiary text-text-muted',
};

/** Where a value came from. Always a visible label, never a tooltip (design C §1.6). */
export function ProvenanceTag({ origin, detail }: { origin: Origin; detail?: string | null }) {
  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap', TONE[origin])}>
      {LABEL[origin]}
      {detail ? ` · ${detail}` : ''}
    </span>
  );
}
