import { useEffect, useState } from 'react';
import type { MaterialClarification, RecommendationNarrative, ScoringResult } from '@/types/analyst';
import { editRecommendationNarrative, getAnalystCase, setMaterialClarifications, setRecommendationDecision, unlockRecommendationDecision } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Textarea } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';
import { EmptyState } from '@/components/ui/EmptyState';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { MATERIAL_CLARIFICATIONS } from '@/mocks/scripts/analystScript';
import { DEMO_CASE_ID } from '@/mocks/scripts/analystScript';
import { BAND_RANGES } from '@/pages/analyst/shared/ScoreBadge';

function routedToCommitteeFor(decision: 'Approve' | 'Reject' | null, scoring: ScoringResult | null): boolean {
  if (decision === 'Reject') return true;
  return (scoring?.band ?? 3) >= 3;
}

function ConfirmDecisionModal({
  open,
  decision,
  scoring,
  onCancel,
  onConfirm,
  confirming,
}: {
  open: boolean;
  decision: 'Approve' | 'Reject' | null;
  scoring: ScoringResult | null;
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  if (!decision) return null;
  const routes = routedToCommitteeFor(decision, scoring);
  const consequence =
    decision === 'Reject'
      ? 'Rejection routes to committee regardless of band.'
      : routes && scoring
        ? `Composite residual ${scoring.compositeResidual}, Band ${scoring.band} of 4 (${BAND_RANGES[scoring.band]}), so this routes to committee review. The committee pack will be generated from the current assessment.`
        : 'Eligible for delegated close under analyst authority - no committee pack is generated for this route.';
  return (
    <Modal open={open} onClose={onCancel} title={`Recommending ${decision}`} width={480}>
      <div className="flex flex-col gap-5">
        <p className="text-[13.5px] text-text-primary leading-relaxed">{consequence}</p>
        <p className="text-[12px] text-text-faint">
          Confirming locks the decision. Changing it afterwards requires an explicit unlock with a logged reason, and regenerates the pack.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant={decision === 'Reject' ? 'destructive' : 'primary'} onClick={onConfirm} loading={confirming}>
            Confirm {decision}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function RecommendationTab({
  caseId,
  hasRun,
  canWrite,
  onRunAnalysis,
  onReload,
}: {
  caseId: string;
  hasRun: boolean;
  canWrite: boolean;
  onRunAnalysis: () => void;
  onReload: () => void;
}) {
  const [narratives, setNarratives] = useState<RecommendationNarrative[] | null>(null);
  const [decision, setDecision] = useState<'Approve' | 'Reject' | null>(null);
  const [locked, setLocked] = useState(false);
  const [scoring, setScoring] = useState<ScoringResult | null>(null);
  const [materialClarifications, setMaterialClarificationsState] = useState<MaterialClarification[]>([]);
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [confirmDecision, setConfirmDecision] = useState<'Approve' | 'Reject' | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [unlocking, setUnlocking] = useState(false);
  const [unlockReason, setUnlockReason] = useState('');
  const toast = useToast();

  async function reload() {
    const a = await getAnalystCase(caseId);
    setNarratives(a.recommendation.narratives);
    setDecision(a.recommendation.decision);
    setLocked(!!a.recommendation.locked);
    setScoring(a.scoring);
    if (a.recommendation.materialClarifications.length === 0 && caseId === DEMO_CASE_ID) {
      await setMaterialClarifications(caseId, MATERIAL_CLARIFICATIONS);
      setMaterialClarificationsState(MATERIAL_CLARIFICATIONS);
    } else {
      setMaterialClarificationsState(a.recommendation.materialClarifications);
    }
  }

  useEffect(() => {
    if (hasRun) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, hasRun]);

  if (!hasRun) {
    return (
      <EmptyState
        icon="document"
        title="Recommendation will populate after the analysis run"
        description="A drafted rationale narrative appears here once you run the analysis."
        action={
          <Button variant="primary" onClick={onRunAnalysis}>
            Run analysis
          </Button>
        }
      />
    );
  }

  if (!narratives) return <Skeleton className="h-64 rounded-md" />;

  const overall = narratives.find((n) => n.category === 'overall');
  const perCategory = narratives.filter((n) => n.category !== 'overall');

  async function saveEdit(category: string) {
    await editRecommendationNarrative(caseId, category, draft);
    setEditingCategory(null);
    reload();
  }

  async function confirmDecisionNow() {
    if (!confirmDecision) return;
    setConfirming(true);
    await setRecommendationDecision(caseId, confirmDecision);
    setConfirming(false);
    const d = confirmDecision;
    setConfirmDecision(null);
    onReload();
    reload();
    if (d === 'Reject') toast('Rejection always routes to committee, regardless of band.', 'default');
  }

  async function handleUnlock() {
    if (!unlockReason.trim()) return;
    await unlockRecommendationDecision(caseId, unlockReason.trim());
    setUnlocking(false);
    setUnlockReason('');
    toast('Decision unlocked. The pack will need to be regenerated.', 'default');
    onReload();
    reload();
  }

  const routedToCommittee = routedToCommitteeFor(decision, scoring);

  return (
    <div className="flex flex-col gap-5">
      {perCategory.map((n) => (
        <Card key={n.category} material="flat" className="p-5 flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-bold text-text-primary">{n.category}</span>
            {n.edited && <Badge tone="info">Edited</Badge>}
            {editingCategory !== n.category && canWrite && (
              <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={() => { setEditingCategory(n.category); setDraft(n.editedText); }}>
                Edit
              </Button>
            )}
          </div>
          {editingCategory === n.category ? (
            <div className="flex flex-col gap-2">
              <Textarea rows={4} value={draft} onChange={(e) => setDraft(e.target.value)} />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setEditingCategory(null)}>
                  Cancel
                </Button>
                <Button size="sm" variant="primary" onClick={() => saveEdit(n.category)}>
                  Save
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-[13.5px] text-text-primary leading-relaxed">{n.editedText}</p>
          )}
        </Card>
      ))}

      {overall && (
        <Card material="flat" className="p-5 flex flex-col gap-2 border-2 border-carbon-moss">
          <div className="flex items-center justify-between">
            <span className="text-sm font-bold text-text-primary">Overall recommendation</span>
            {editingCategory !== 'overall' && canWrite && (
              <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={() => { setEditingCategory('overall'); setDraft(overall.editedText); }}>
                Edit
              </Button>
            )}
          </div>
          {editingCategory === 'overall' ? (
            <div className="flex flex-col gap-2">
              <Textarea rows={4} value={draft} onChange={(e) => setDraft(e.target.value)} />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setEditingCategory(null)}>
                  Cancel
                </Button>
                <Button size="sm" variant="primary" onClick={() => saveEdit('overall')}>
                  Save
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-[13.5px] text-text-primary leading-relaxed">{overall.editedText}</p>
          )}
        </Card>
      )}

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary">Material clarifications</span>
        {materialClarifications.length === 0 ? (
          <p className="text-[13px] text-text-faint">No material clarifications on this case.</p>
        ) : (
          materialClarifications.map((m) => (
            <div key={m.round} className="flex items-start gap-2.5">
              <Badge tone="default">Round {m.round}</Badge>
              <p className="text-[13px] text-text-secondary">{m.summary}</p>
            </div>
          ))
        )}
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Recommendation decision</span>
          {locked && <Badge tone="info">Locked</Badge>}
        </div>
        {canWrite && !locked && (
          <div className="flex gap-3">
            <Button variant={decision === 'Approve' ? 'primary' : 'secondary'} onClick={() => setConfirmDecision('Approve')}>
              Approve
            </Button>
            <Button variant={decision === 'Reject' ? 'destructive' : 'secondary'} onClick={() => setConfirmDecision('Reject')}>
              Reject
            </Button>
          </div>
        )}
        {!canWrite && !decision && <p className="text-[13px] text-text-faint">No decision recorded.</p>}
        {decision && (
          <p className="text-[12.5px] text-text-muted">
            {decision === 'Reject'
              ? 'Rejection is not eligible for delegated close - this routes to committee regardless of band.'
              : routedToCommittee && scoring
                ? `Band ${scoring.band} of 4 (${BAND_RANGES[scoring.band]}) · ${scoring.route}`
                : scoring
                  ? `Band ${scoring.band} of 4 (${BAND_RANGES[scoring.band]}) - eligible for delegated close.`
                  : 'Eligible for delegated close.'}
          </p>
        )}
        {canWrite && locked && !unlocking && (
          <Button size="sm" variant="ghost" icon={<Icon name="lock" size={13} />} className="self-start" onClick={() => setUnlocking(true)}>
            Unlock decision
          </Button>
        )}
        {canWrite && locked && unlocking && (
          <div className="flex flex-col gap-2 bg-surface-tertiary rounded-md p-3">
            <p className="text-[12px] text-text-secondary">Unlocking requires a logged reason, and the committee pack will need to be regenerated.</p>
            <div className="flex items-center gap-2">
              <Input value={unlockReason} onChange={(e) => setUnlockReason(e.target.value)} placeholder="Why is this being unlocked?" className="text-[12.5px] py-1.5" />
              <Button size="sm" variant="ghost" onClick={() => { setUnlocking(false); setUnlockReason(''); }}>
                Cancel
              </Button>
              <Button size="sm" variant="destructive" onClick={handleUnlock} disabled={!unlockReason.trim()}>
                Unlock
              </Button>
            </div>
          </div>
        )}
      </Card>

      <ConfirmDecisionModal
        open={!!confirmDecision}
        decision={confirmDecision}
        scoring={scoring}
        onCancel={() => setConfirmDecision(null)}
        onConfirm={confirmDecisionNow}
        confirming={confirming}
      />
    </div>
  );
}
