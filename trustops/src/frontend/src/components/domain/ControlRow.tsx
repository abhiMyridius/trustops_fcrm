/** One matched control: weight, proposed rating, evidence + date, stale flag, Confirm / Review. */
import { useEffect, useRef, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { Check, PenLine, TriangleAlert } from 'lucide-react';
import type { ControlItem } from '@/api/types/analyst';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { formatDate, formatFixed } from '@/lib/format';

export function ControlRow({
  control,
  onConfirm,
  onOverride,
  readOnly,
  busy,
}: {
  control: ControlItem;
  onConfirm?: () => void;
  onOverride?: () => void;
  readOnly?: boolean;
  busy?: boolean;
}) {
  // `#control-<id>` (the critic review's "Change rating") scrolls to and focuses this row.
  const anchor = `control-${control.id}`;
  const { hash } = useLocation();
  const row = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    if (hash !== `#${anchor}`) return;
    row.current?.scrollIntoView?.({ block: 'center' });
    row.current?.focus({ preventScroll: true });
  }, [hash, anchor]);
  return (
    <tr ref={row} id={anchor} tabIndex={-1} className="border-t border-border-hairline align-top focus:outline-2 focus:outline-info">
      <th scope="row" className="px-3 py-3 text-left">
        <span className="font-mono text-[11px] text-text-muted">{control.id}</span>
        <span className="block text-[13px] font-semibold text-text-primary">{control.name}</span>
        {control.critic && (
          <span className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-danger">
            <TriangleAlert aria-hidden size={11} /> Critic finding
          </span>
        )}
      </th>
      <td className="px-3 py-3 text-right tabular-nums">{formatFixed(control.weight_norm, 2)}</td>
      <td className="px-3 py-3 text-right tabular-nums">
        <span className="font-bold">{formatFixed(control.rating)}</span>
        {control.override && (
          <span className="block text-[11px] text-text-muted">
            proposed <span className="line-through">{formatFixed(control.override.from)}</span>
          </span>
        )}
      </td>
      <td className="px-3 py-3 text-[13px]">
        {control.evidence}
        <span className="block text-[11px] text-text-muted">{formatDate(control.evidence_date)}</span>
        {control.stale && (
          <Badge tone="warning" className="mt-1">
            Stale evidence
          </Badge>
        )}
      </td>
      <td className="px-3 py-3">
        {control.decision === 'confirmed' && (
          <Badge tone="success" icon={<Check aria-hidden size={12} />}>
            Confirmed
          </Badge>
        )}
        {control.decision === 'overridden' && (
          <Badge tone="danger" icon={<PenLine aria-hidden size={12} />}>
            Overridden
          </Badge>
        )}
        {control.decision === 'pending' && !readOnly && (
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" variant="primary" onClick={onConfirm} loading={busy} aria-label={`Confirm ${control.id} rating ${formatFixed(control.rating)}`}>
              Confirm
            </Button>
            <Button size="sm" variant="secondary" onClick={onOverride} disabled={busy} aria-label={`Review or override ${control.id}`}>
              Review…
            </Button>
          </div>
        )}
        {control.decision === 'pending' && readOnly && <Badge tone="warning">Pending</Badge>}
      </td>
    </tr>
  );
}

/** Table wrapper so ControlRow is always rendered inside correct table semantics. */
export function ControlTable({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-tertiary text-[11px] font-semibold uppercase tracking-overline text-text-muted">
          <tr>
            <th scope="col" className="px-3 py-2 text-left">
              Control
            </th>
            <th scope="col" className="px-3 py-2 text-right">
              Weight
            </th>
            <th scope="col" className="px-3 py-2 text-right">
              Rating
            </th>
            <th scope="col" className="px-3 py-2 text-left">
              Evidence
            </th>
            <th scope="col" className="px-3 py-2 text-left">
              Your decision
            </th>
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
