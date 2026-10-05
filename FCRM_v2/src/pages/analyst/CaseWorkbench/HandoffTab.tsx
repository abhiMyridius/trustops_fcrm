import { useEffect, useState } from 'react';
import type { CaseDetail } from '@/types';
import type { AnalystCaseData } from '@/types/analyst';
import {
  approveHandoffPack,
  editRecommendationDecisionSummary,
  editRecommendationNarrative,
  ensureDecisionSummaryDrafted,
  generateCommitteePack,
  getAnalystCase,
  setHandoffNote,
  signOffDelegatedClose,
} from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { ScoreBadge } from '@/pages/analyst/shared/ScoreBadge';
import { CommitteePackView, downloadCommitteePack } from './CommitteePackView';
import { HANDOFF_NOTE } from '@/mocks/scripts/analystScript';
import { DEMO_CASE_ID } from '@/mocks/scripts/analystScript';

function DecisionSummaryCard({
  text,
  editing,
  canWrite,
  draft,
  onEdit,
  onDraftChange,
  onCancel,
  onSave,
}: {
  text: string | undefined;
  editing: boolean;
  canWrite: boolean;
  draft: string;
  onEdit: () => void;
  onDraftChange: (v: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <Card material="flat" className="p-5 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-text-primary">Decision summary for the PO</span>
        {!editing && canWrite && (
          <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={onEdit}>
            Edit
          </Button>
        )}
      </div>
      <p className="text-[11.5px] text-text-faint">
        A separate artifact from the narrative above - business register, no composite residual, band, control CE or policy clause. This is what the PO sees at closure.
      </p>
      {editing ? (
        <div className="flex flex-col gap-2">
          <Textarea rows={3} value={draft} onChange={(e) => onDraftChange(e.target.value)} />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={onSave}>
              Save
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-[13px] text-text-primary leading-relaxed">{text || 'Not yet drafted.'}</p>
      )}
    </Card>
  );
}

export function HandoffTab({
  caseData,
  hasRun,
  canWrite,
  onRunAnalysis,
  onReload,
}: {
  caseData: CaseDetail;
  hasRun: boolean;
  canWrite: boolean;
  onRunAnalysis: () => void;
  onReload: () => void;
}) {
  const [a, setA] = useState<AnalystCaseData | null>(null);
  const [note, setNote] = useState('');
  const [editingSummary, setEditingSummary] = useState(false);
  const [summaryDraft, setSummaryDraft] = useState('');
  const [editingDecisionSummary, setEditingDecisionSummary] = useState(false);
  const [decisionSummaryDraft, setDecisionSummaryDraft] = useState('');
  const toast = useToast();

  async function reload() {
    await ensureDecisionSummaryDrafted(caseData.id);
    const data = await getAnalystCase(caseData.id);
    setA(data);
    setNote(data.handoff.analystNote ?? '');
  }

  useEffect(() => {
    if (hasRun) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseData.id, hasRun]);

  if (!hasRun) {
    return (
      <EmptyState
        icon="folder"
        title="Handoff becomes available after the analysis run and a recommendation"
        description="Run the analysis and set a recommendation decision first."
        action={
          <Button variant="primary" onClick={onRunAnalysis}>
            Run analysis
          </Button>
        }
      />
    );
  }

  if (!a) return <Skeleton className="h-64 rounded-md" />;

  if (!a.recommendation.decision) {
    return <EmptyState icon="document" title="Set a recommendation decision first" description="Handoff routes based on the decision made in the Recommendation tab." />;
  }

  const committeeRoute = a.recommendation.decision === 'Reject' || (a.scoring?.band ?? 3) >= 3;

  async function handleGeneratePack() {
    await generateCommitteePack(caseData.id);
    reload();
  }

  async function handleSaveNote() {
    await setHandoffNote(caseData.id, note || (caseData.id === DEMO_CASE_ID ? HANDOFF_NOTE : ''));
    toast('Note saved.', 'success');
    reload();
  }

  async function handleApprovePack() {
    await approveHandoffPack(caseData.id);
    toast('Committee pack approved and shared. Case moved to Committee Review.', 'success');
    onReload();
    reload();
  }

  function startEditDecisionSummary() {
    setDecisionSummaryDraft(a?.recommendation.decisionSummary ?? '');
    setEditingDecisionSummary(true);
  }

  async function saveDecisionSummary() {
    await editRecommendationDecisionSummary(caseData.id, decisionSummaryDraft);
    setEditingDecisionSummary(false);
    reload();
  }

  async function handleSignOff() {
    await signOffDelegatedClose(caseData.id);
    toast('Case closed under delegated authority.', 'success');
    onReload();
    reload();
  }

  if (!committeeRoute) {
    return (
      <div className="flex flex-col gap-5 max-w-[640px]">
        {a.scoring?.band === 2 && (
          <div className="flex items-start gap-2.5 bg-[#FBF3E4] border border-warning rounded-md px-4 py-3 text-[12.5px] text-[#5c4110] leading-relaxed">
            <Icon name="warning" size={15} className="text-warning shrink-0 mt-0.5" />
            <span>
              <strong>No independent challenge on this delegated close.</strong> This Band 2 case closes under delegated authority with no review by anyone other than the assigned analyst, at
              any point in the process. Combined with the removed approval queue and the absent lead role, nothing in the built system provides independent challenge here. That is a
              deliberate scoping decision, consistent with the others in this phase - the next step remains a lead or FCRM-owner role.
            </span>
          </div>
        )}
        <Card material="flat" className="p-6 flex flex-col gap-5">
          <span className="text-sm font-bold text-text-primary">Delegated close</span>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Composite residual</span>
              {a.scoring && <ScoreBadge scoring={a.scoring} compact />}
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Recommendation</span>
              <Badge tone={a.recommendation.decision === 'Approve' ? 'success' : 'danger'}>{a.recommendation.decision}</Badge>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Model version</span>
              <span className="text-[13px] font-mono text-text-primary">{a.scoring?.modelVersion}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Confirmations</span>
              <span className="text-[13px] text-text-primary">{a.confirmedComponentIds.length} components confirmed</span>
            </div>
          </div>
        </Card>

        <DecisionSummaryCard
          text={a.recommendation.decisionSummary}
          editing={editingDecisionSummary}
          canWrite={canWrite}
          draft={decisionSummaryDraft}
          onEdit={startEditDecisionSummary}
          onDraftChange={setDecisionSummaryDraft}
          onCancel={() => setEditingDecisionSummary(false)}
          onSave={saveDecisionSummary}
        />

        {canWrite && (
          <>
            <Button variant="primary" onClick={handleSignOff} disabled={!!a.handoff.packApprovedAt || !a.recommendation.decisionSummary}>
              {a.handoff.packApprovedAt ? 'Closed' : 'Confirm sign-off and close case'}
            </Button>
            {!a.recommendation.decisionSummary && <p className="text-[12px] text-danger">The decision summary above must be drafted before sign-off.</p>}
          </>
        )}
        <p className="text-[11.5px] text-text-faint">
          The PO's stage tracker will render Committee Review as a visibly skipped stage, labelled "not required, closed under delegated authority." Closed delegated cases appear in a
          periodic digest for committee spot-check.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug">
        <Icon name="lock" size={14} className="text-teal shrink-0 mt-0.5" />
        Deterministically generated pack, no AI. Structured numbers, composite residuals, bands, control CEs and the matrix are locked - narrative and note sections are editable.
      </div>

      {!a.handoff.kind || a.handoff.kind !== 'committee' ? (
        canWrite ? (
          <Button variant="primary" onClick={handleGeneratePack}>
            Generate committee pack
          </Button>
        ) : (
          <p className="text-[13px] text-text-faint">No committee pack generated yet.</p>
        )
      ) : (
        <>
          <div className="flex items-center justify-between">
            <span className="text-sm font-bold text-text-primary">Full committee pack</span>
            <Button size="sm" variant="secondary" icon={<Icon name="download" size={14} />} onClick={() => downloadCommitteePack(caseData, a)}>
              Export pack
            </Button>
          </div>
          <CommitteePackView caseData={caseData} a={a} />

          <div className="flex items-center gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug">
            <Icon name="edit" size={14} className="text-teal shrink-0" />
            The sections below are still editable - narrative, decision summary and analyst notes - and feed the pack above.
          </div>

          <Card material="flat" className="p-5 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-bold text-text-primary">Narrative summary (editable)</span>
              {!editingSummary && canWrite && (
                <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={() => { setEditingSummary(true); setSummaryDraft(a.recommendation.narratives.find((n) => n.category === 'overall')?.editedText ?? ''); }}>
                  Edit
                </Button>
              )}
            </div>
            {editingSummary && canWrite ? (
              <div className="flex flex-col gap-2">
                <Textarea rows={4} value={summaryDraft} onChange={(e) => setSummaryDraft(e.target.value)} />
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setEditingSummary(false)}>
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={async () => {
                      await editRecommendationNarrative(caseData.id, 'overall', summaryDraft);
                      setEditingSummary(false);
                      reload();
                    }}
                  >
                    Save
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-[13px] text-text-primary leading-relaxed">{a.recommendation.narratives.find((n) => n.category === 'overall')?.editedText}</p>
            )}
          </Card>

          <DecisionSummaryCard
            text={a.recommendation.decisionSummary}
            editing={editingDecisionSummary}
            canWrite={canWrite}
            draft={decisionSummaryDraft}
            onEdit={startEditDecisionSummary}
            onDraftChange={setDecisionSummaryDraft}
            onCancel={() => setEditingDecisionSummary(false)}
            onSave={saveDecisionSummary}
          />

          {(canWrite || note) && (
            <Card material="flat" className="p-5 flex flex-col gap-2">
              <span className="text-sm font-bold text-text-primary flex items-center gap-2">
                Optional analyst notes <Badge tone="info">Distinguished from generated content</Badge>
              </span>
              {canWrite ? (
                <>
                  <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder={caseData.id === DEMO_CASE_ID ? HANDOFF_NOTE : 'Add a note for committee attention...'} />
                  <Button size="sm" variant="secondary" className="self-start" onClick={handleSaveNote}>
                    Save note
                  </Button>
                </>
              ) : (
                <p className="text-[13px] text-text-primary leading-relaxed">{note}</p>
              )}
            </Card>
          )}

          {canWrite && (
            <>
              <Button variant="primary" onClick={handleApprovePack} disabled={!!a.handoff.packApprovedAt || !a.recommendation.decisionSummary}>
                {a.handoff.packApprovedAt ? 'Pack approved' : 'Approve pack and share with committee'}
              </Button>
              {!a.recommendation.decisionSummary && <p className="text-[12px] text-danger mt-1.5">The decision summary above must be drafted before the pack can be approved.</p>}
            </>
          )}
          {a.handoff.packApprovedAt && (
            <p className="text-[12.5px] text-teal font-semibold flex items-center gap-1.5">
              <Icon name="check" size={14} /> Case moved to Committee Review. The Risk Committee votes from here.
            </p>
          )}
        </>
      )}
    </div>
  );
}
