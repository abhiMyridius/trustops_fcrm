/**
 * 2.26 Committee › Vote: "n of 3 votes in", VoteCards (hidden until all three are cast), a required
 * rationale and the four outcomes. A vote cannot be changed once cast (409). Counted by code (S5).
 */
import { useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useCastVote, useVotes } from '@/api/queries/committeeQueries';
import { userMessage } from '@/api/errors';
import type { Outcome } from '@/api/types/common';
import type { PackView } from '@/api/types/committee';
import { VoteCard } from '@/components/domain/VoteCard';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { REASON_MIN, reasonSchema } from '@/components/ui/Form';
import { QueryState } from '@/components/ui/QueryState';
import { RadioGroup } from '@/components/ui/Radio';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { OUTCOME_LABEL } from '@/lib/format';

const OPTIONS: Outcome[] = ['approve', 'approve_with_conditions', 'defer', 'reject'];

export function VoteTab() {
  const p = useOutletContext<PackView>();
  useDocumentTitle('Vote', p.case_id);
  const q = useVotes(p.pack_id);
  const cast = useCastVote(p.pack_id);
  const toast = useToast();
  const [choice, setChoice] = useState<Outcome | null>(null);
  const [rationale, setRationale] = useState('');
  const [touched, setTouched] = useState(false);
  const parsed = reasonSchema.safeParse(rationale);

  return (
    <QueryState query={q} label="Loading votes">
      {(v) => (
        <div className="flex max-w-4xl flex-col gap-5">
          <p className="text-lg font-bold" aria-live="polite">
            {v.cast_count} of {v.quorum} votes in{v.round > 1 ? ` · round ${v.round}` : ''}
          </p>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {v.members.map((m) => (
              <VoteCard key={m.name} member={m} revealed={v.revealed} />
            ))}
          </div>
          {v.revealed && v.tally && (
            <Banner tone={v.outcome === 'no_majority' ? 'warning' : 'success'} live="polite" title={`Votes: ${Object.entries(v.tally).map(([k, n]) => `${OUTCOME_LABEL[k as Outcome].toLowerCase()} ×${n}`).join(' · ')}`}>
              {v.outcome === 'no_majority' ? 'No majority. A re-vote round is requested.' : `Majority: ${v.outcome_label?.toLowerCase()}.`}
            </Banner>
          )}
          {v.revealed && !v.tally && <Banner tone="info">{`Decided: ${v.outcome_label ?? ''}. Individual votes are not available for this case.`}</Banner>}
          {v.mine && !v.revealed && <Banner tone="success">{`You voted ${OUTCOME_LABEL[v.mine.choice].toLowerCase()}. Votes are shown when all three are in.`}</Banner>}
          {!v.mine && !v.revealed && p.status !== 'decided' && (
            <Card title="Cast your vote">
              <form
                className="flex flex-col gap-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  setTouched(true);
                  if (!choice || !parsed.success) return;
                  cast.mutate(
                    { vote: choice, rationale: parsed.data },
                    { onSuccess: () => toast.success('Vote cast'), onError: (er) => toast.error('Not cast', userMessage(er)) },
                  );
                }}
              >
                <RadioGroup legend="Your vote" name="vote" value={choice} onChange={setChoice} options={OPTIONS.map((o) => ({ value: o, label: OUTCOME_LABEL[o] }))} orientation="horizontal" />
                <Textarea
                  label="Rationale (required)"
                  value={rationale}
                  onChange={(e) => setRationale(e.target.value)}
                  onBlur={() => setTouched(true)}
                  rows={4}
                  hint={`At least ${REASON_MIN} characters. ${rationale.trim().length} so far.`}
                  error={touched && !parsed.success ? parsed.error.issues[0]?.message : undefined}
                />
                <div>
                  <Button type="submit" variant="primary" disabled={!choice || !parsed.success} loading={cast.isPending}>
                    Cast vote
                  </Button>
                </div>
              </form>
            </Card>
          )}
        </div>
      )}
    </QueryState>
  );
}
