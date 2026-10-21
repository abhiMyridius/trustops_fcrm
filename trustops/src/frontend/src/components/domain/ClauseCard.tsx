/**
 * Policy clause quoted verbatim with its source and version (rule 8). One decision per card (rule 2).
 * A clause the analyst added from a corpus search (ADR-74) says who added it and why, and can still be
 * removed with a reason.
 */
import { Check, Quote, UserPlus, X } from 'lucide-react';
import type { ClauseItem } from '@/api/types/analyst';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ClauseChip } from './ClausePreview';

export function ClauseCard({
  clause,
  onAccept,
  onRemove,
  readOnly,
  busy,
}: {
  clause: ClauseItem;
  onAccept?: () => void;
  onRemove?: () => void;
  readOnly?: boolean;
  busy?: boolean;
}) {
  const analyst = clause.origin === 'analyst';
  return (
    <article aria-labelledby={`clause-${clause.id}`} className="print-section flex flex-col gap-3 rounded-lg border border-border bg-surface p-4 shadow-sm">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={`clause-${clause.id}`} className="text-sm font-bold text-text-primary">
            {clause.title}
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-1.5 font-mono text-[11px] text-text-muted">
            <span>
              {clause.id} · corpus {clause.corpus_version}
            </span>
            <ClauseChip clauseId={clause.id} label={clause.ref} />
          </p>
        </div>
        {clause.decision === 'accepted' && (
          <Badge tone="success" icon={<Check aria-hidden size={12} />}>
            Accepted
          </Badge>
        )}
        {clause.decision === 'removed' && (
          <Badge tone="danger" icon={<X aria-hidden size={12} />}>
            Removed
          </Badge>
        )}
        {clause.decision === 'pending' && <Badge tone="warning">Awaiting your review</Badge>}
      </header>
      <blockquote className="flex gap-2 rounded-md border-l-4 border-digital-pavo bg-surface-tertiary px-3 py-2.5 text-[13px] italic leading-relaxed text-text-primary">
        <Quote aria-hidden size={14} className="mt-0.5 shrink-0 text-digital-pavo" />
        <span>{clause.quote}</span>
      </blockquote>
      {analyst && (
        <p className="flex items-start gap-1.5 text-[12px] text-text-secondary">
          <UserPlus aria-hidden size={13} className="mt-0.5 shrink-0" />
          <span>
            Added by {clause.added_by ?? 'the analyst'} from a corpus search
            {clause.added_reason ? `: ${clause.added_reason}` : ''}
          </span>
        </p>
      )}
      {clause.reason && !(analyst && clause.decision === 'accepted') && (
        <p className="text-[12px] text-text-secondary">Reason recorded: {clause.reason}</p>
      )}
      {!readOnly && analyst && clause.decision === 'accepted' && (
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={onRemove} disabled={busy} aria-label={`Remove clause ${clause.id}`}>
            Remove…
          </Button>
        </div>
      )}
      {!readOnly && clause.decision === 'pending' && (
        <div className="flex gap-2">
          <Button size="sm" variant="primary" onClick={onAccept} loading={busy} aria-label={`Accept clause ${clause.id}`}>
            Accept
          </Button>
          <Button size="sm" variant="ghost" onClick={onRemove} disabled={busy} aria-label={`Remove clause ${clause.id}`}>
            Remove…
          </Button>
        </div>
      )}
    </article>
  );
}
