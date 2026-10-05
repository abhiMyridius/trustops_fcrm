/**
 * One committee member's vote. Before reveal the server sends no choice for other members, so this
 * card can only show "voted · hidden" or "not yet voted"; after reveal it shows choice + rationale.
 */
import { Check, Clock, EyeOff } from 'lucide-react';
import type { VoteMember } from '@/api/types/committee';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { OUTCOME_LABEL, formatDateTime } from '@/lib/format';

export function VoteCard({ member, revealed }: { member: VoteMember; revealed: boolean }) {
  return (
    <article aria-label={`Vote of ${member.name}`} className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-4 shadow-sm">
      <header className="flex items-center gap-2.5">
        <Avatar name={member.name} size="sm" decorative />
        <span className="text-sm font-semibold">
          {member.name}
          {member.is_you ? ' (you)' : ''}
        </span>
      </header>
      {member.status === 'not_voted' && (
        <Badge tone="neutral" icon={<Clock aria-hidden size={12} />}>
          Not yet voted
        </Badge>
      )}
      {member.status === 'voted' && (!revealed || !member.choice) && (
        <p className="flex items-center gap-2 text-[13px] font-semibold text-text-secondary">
          <EyeOff aria-hidden size={14} />
          Voted · hidden
          <span aria-hidden className="tracking-[0.2em] text-text-faint">
            ●●●
          </span>
        </p>
      )}
      {member.status === 'voted' && revealed && member.choice && (
        <>
          <Badge tone={member.choice === 'reject' ? 'danger' : member.choice === 'defer' ? 'warning' : 'success'} icon={<Check aria-hidden size={12} />}>
            {OUTCOME_LABEL[member.choice]}
          </Badge>
          {member.rationale && <p className="text-[13px] leading-relaxed text-text-secondary">“{member.rationale}”</p>}
          {member.cast_at && <p className="text-[11px] text-text-muted">Cast {formatDateTime(member.cast_at)}</p>}
        </>
      )}
    </article>
  );
}
