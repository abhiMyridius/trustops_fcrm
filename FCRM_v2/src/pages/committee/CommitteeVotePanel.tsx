import { useEffect, useState } from 'react';
import { castCommitteeVote, CURRENT_COMMITTEE_MEMBER, getCommitteeVoteView, type CommitteeVoteView } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { formatDateTime } from '@/utils/format';

/** The committee's vote screen and reveal (§8.5 mod, §C1/C2) - votes never show a running
 * tally, only a count of how many are in, until the third vote lands and all three reveal
 * together with their rationales. */
export function CommitteeVotePanel({ caseId, canVote, onDecided }: { caseId: string; canVote: boolean; onDecided: () => void }) {
  const [view, setView] = useState<CommitteeVoteView | null>(null);
  const [choosing, setChoosing] = useState<'Approve' | 'Reject' | null>(null);
  const [rationale, setRationale] = useState('');
  const [casting, setCasting] = useState(false);
  const toast = useToast();

  async function reload() {
    setView(await getCommitteeVoteView(caseId));
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  if (!view) return <Skeleton className="h-24 rounded-md" />;

  async function confirmVote() {
    if (!choosing || !rationale.trim()) return;
    setCasting(true);
    const result = await castCommitteeVote(caseId, choosing, rationale.trim(), CURRENT_COMMITTEE_MEMBER);
    setCasting(false);
    setChoosing(null);
    setRationale('');
    toast(result.revealed ? `All three votes are in - outcome: ${result.outcome}.` : 'Vote cast. Hidden until all three members have voted.', 'success');
    await reload();
    if (result.revealed) onDecided();
  }

  if (view.revealed) {
    return (
      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Committee vote - revealed</span>
          <Badge tone={view.outcome === 'Approve' ? 'success' : 'danger'}>Outcome: {view.outcome}</Badge>
        </div>
        <div className="flex flex-col gap-2">
          {view.votes.map((v) => (
            <div key={v.id} className="flex flex-col gap-0.5 border-b border-border-hairline last:border-b-0 pb-2 last:pb-0">
              <div className="flex items-center gap-2">
                <Badge tone={v.decision === 'Approve' ? 'success' : 'danger'}>{v.decision}</Badge>
                <span className="text-[13px] font-semibold text-text-primary">{v.member}</span>
                <span className="text-[11px] text-text-faint">{formatDateTime(v.castAt)}</span>
              </div>
              <p className="text-[12.5px] text-text-secondary">{v.rationale}</p>
            </div>
          ))}
        </div>
      </Card>
    );
  }

  return (
    <Card material="flat" className="p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-text-primary">Your vote</span>
        <Badge tone="default">{view.votesCastCount} of {view.totalMembers} cast</Badge>
      </div>
      {view.myVote ? (
        <p className="text-[13px] text-text-secondary">
          You voted. Votes stay hidden until all three members have cast theirs - you'll see the full reveal here once the third vote lands.
        </p>
      ) : canVote ? (
        <>
          <p className="text-[13px] text-text-secondary">Approve or Reject, with a mandatory rationale. A vote cannot be changed once cast.</p>
          <div className="flex gap-3">
            <Button variant="primary" onClick={() => setChoosing('Approve')}>
              Approve
            </Button>
            <Button variant="destructive" onClick={() => setChoosing('Reject')}>
              Reject
            </Button>
          </div>
        </>
      ) : (
        <p className="text-[13px] text-text-faint">Voting is not available on this case.</p>
      )}

      <Modal open={!!choosing} onClose={() => { setChoosing(null); setRationale(''); }} title={`Vote to ${choosing}`} width={480}>
        <div className="flex flex-col gap-4">
          <p className="text-[12.5px] text-text-faint">Rationale is required, and this vote cannot be changed once cast.</p>
          <Textarea rows={4} value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="Why are you voting this way?" />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => { setChoosing(null); setRationale(''); }}>
              Cancel
            </Button>
            <Button variant={choosing === 'Reject' ? 'destructive' : 'primary'} onClick={confirmVote} loading={casting} disabled={!rationale.trim()}>
              <Icon name="check" size={14} /> Confirm {choosing}
            </Button>
          </div>
        </div>
      </Modal>
    </Card>
  );
}
