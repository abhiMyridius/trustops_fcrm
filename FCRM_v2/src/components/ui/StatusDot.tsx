import type { CaseStatus } from '@/types';
import clsx from 'clsx';

// Table/case status = dot + label, never a filled pill (Design System rule).

const STATUS_STYLE: Record<CaseStatus, { color: string; label: string; outline?: boolean }> = {
  Draft: { color: '#9caaab', label: 'Draft', outline: true },
  Submitted: { color: '#337077', label: 'Submitted' },
  'Under Assessment': { color: '#b98a1e', label: 'Under Assessment' },
  'Committee Review': { color: '#194247', label: 'Committee Review' },
  Approved: { color: '#09A78D', label: 'Approved' },
  Rejected: { color: '#8a3324', label: 'Rejected' },
  Closed: { color: '#6b7f80', label: 'Closed' },
};

/** Once a case is Closed, its outcome is the single most important fact about it - shown as
 * a distinct dot colour and label ("Closed - Approved" / "Closed - Rejected") rather than a
 * bare "Closed" that leaves the PO guessing. */
const CLOSED_OUTCOME_STYLE: Record<'Approved' | 'Rejected', { color: string; label: string }> = {
  Approved: { color: '#09A78D', label: 'Closed · Approved' },
  Rejected: { color: '#8a3324', label: 'Closed · Rejected' },
};

export function StatusDot({ status, outcome, className }: { status: CaseStatus; outcome?: 'Approved' | 'Rejected'; className?: string }) {
  const s = status === 'Closed' && outcome ? CLOSED_OUTCOME_STYLE[outcome] : STATUS_STYLE[status];
  return (
    <span className={clsx('inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-[#133338]', className)}>
      <span
        className="w-[7px] h-[7px] rounded-full shrink-0"
        style={'outline' in s && s.outline ? { border: `1.5px solid ${s.color}` } : { background: s.color }}
      />
      {s.label}
    </span>
  );
}
