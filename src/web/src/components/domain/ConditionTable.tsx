/**
 * Conditions table. The PO variant gets ConditionPO rows (no `from` provenance to controls or
 * clauses); the analyst variant shows where each condition came from.
 */
import type { ReactNode } from 'react';
import { Badge, type Tone } from '@/components/ui/Badge';

export interface ConditionRowData {
  id: string;
  text: string;
  owner: string;
  due: string;
  status: 'open' | 'scheduled' | 'evidence_submitted' | 'closed';
  from?: string;
}

const STATUS: Record<ConditionRowData['status'], { label: string; tone: Tone }> = {
  open: { label: 'Open', tone: 'warning' },
  scheduled: { label: 'Scheduled', tone: 'info' },
  evidence_submitted: { label: 'Evidence submitted', tone: 'success' },
  closed: { label: 'Closed', tone: 'neutral' },
};

export function ConditionTable({
  rows,
  showSource,
  action,
  caption = 'Conditions',
}: {
  rows: ConditionRowData[];
  showSource?: boolean;
  action?: (row: ConditionRowData) => ReactNode;
  caption?: string;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-tertiary text-left text-[11px] uppercase tracking-wide text-text-muted">
          <tr>
            <th scope="col" className="px-4 py-2.5">
              Condition
            </th>
            <th scope="col" className="px-4 py-2.5">
              Owner
            </th>
            <th scope="col" className="px-4 py-2.5">
              Due
            </th>
            <th scope="col" className="px-4 py-2.5">
              Status
            </th>
            {showSource && (
              <th scope="col" className="px-4 py-2.5">
                From
              </th>
            )}
            {action && (
              <th scope="col" className="px-4 py-2.5">
                <span className="sr-only">Action</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-border-hairline align-top">
              <td className="px-4 py-3">
                <span className="font-mono text-[11px] text-text-muted">{r.id}</span>
                <span className="block">{r.text}</span>
              </td>
              <td className="px-4 py-3">{r.owner}</td>
              <td className="px-4 py-3">{r.due}</td>
              <td className="px-4 py-3">
                <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>
              </td>
              {showSource && <td className="px-4 py-3 text-[12px] text-text-secondary">{r.from}</td>}
              {action && <td className="px-4 py-3">{action(r)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
