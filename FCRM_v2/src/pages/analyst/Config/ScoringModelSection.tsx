import { useEffect, useState } from 'react';
import type { AmlCategory, AnchorLevel, BandDef, CaseControl, RoutingRules, ScoringModelVersion } from '@/types/analyst';
import { ALL_AML_CATEGORIES } from '@/types/analyst';
import {
  commitScoringModelChange,
  getRoutingRules,
  getScoringModelVersions,
  previewScoringModelImpact,
  type ScoringModelCandidate,
  type ScoringModelImpact,
} from '@/services/analystApi';
import { computeScoring, type CategoryFactorInput } from '@/services/scoring';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Checkbox, Textarea } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';

const SECTION_ID = 'scoring';
const SECTION_LABEL = 'Scoring Model';

// The mod prompt's own worked example, computed live from whatever weights are currently in
// effect (draft or committed) rather than hard-coded, so it stays honest if weights change.
const EXAMPLE_CATEGORIES: AmlCategory[] = ['Geography', 'Delivery channel and third party', 'Product and service'];
function normalisedExample(weights: Record<AmlCategory, number>) {
  const raw = EXAMPLE_CATEGORIES.map((c) => weights[c] ?? 0);
  const sum = raw.reduce((a, b) => a + b, 0) || 1;
  return raw.map((w) => Math.round((w / sum) * 100) / 100);
}

// -----------------------------------------------------------------------
// Impact preview - shared between this page (weight/anchor edits) and Rules & Routing's band
// table editor (§4.1/§4.2), since both kinds of score-changing edit need the identical preview.
// -----------------------------------------------------------------------
export function ScoringImpactModal({
  open,
  impact,
  pendingChangeCount,
  note,
  onNoteChange,
  rescoreIds,
  onToggleRescore,
  onCancel,
  onConfirm,
  confirming,
}: {
  open: boolean;
  impact: ScoringModelImpact | null;
  /** Omit (or pass 0) when this preview isn't part of a working set, e.g. the Rules & Routing band editor. */
  pendingChangeCount?: number;
  note: string;
  onNoteChange: (v: string) => void;
  rescoreIds: Set<string>;
  onToggleRescore: (caseId: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  return (
    <Modal open={open} onClose={onCancel} title="Impact preview - review before publishing" width={640}>
      {impact && (
        <div className="flex flex-col gap-4">
          {impact.scoreChanging ? (
            <>
              {!!pendingChangeCount && (
                <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3 py-2.5">
                  <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
                  <p className="text-[12px] text-[#133338]">
                    Reflects the full working set - {pendingChangeCount} pending change{pendingChangeCount === 1 ? '' : 's'} - not just the edit that opened this.
                  </p>
                </div>
              )}
              <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
                <span className="text-text-secondary">Cases already scored - unaffected, stay stamped to the version that scored them</span>
                <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{impact.alreadyScoredCount}</span>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Cases pending an analysis run - will score on the new version</span>
                {impact.pendingAnalysisRun.length === 0 ? (
                  <p className="text-[13px] text-teal font-medium">None.</p>
                ) : (
                  <div className="flex flex-col gap-1 max-h-[140px] overflow-y-auto">
                    {impact.pendingAnalysisRun.map((c) => (
                      <div key={c.caseId} className="text-[12.5px] text-text-secondary px-3 py-1.5 rounded-md bg-surface-tertiary">
                        {c.caseId} - {c.title}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">In-flight cases already scored on the current version</span>
                <p className="text-[11.5px] text-text-faint -mt-0.5">Continue on their current composite residual and band unless re-scored explicitly below - logged per case.</p>
                {impact.inFlightOnCurrentVersion.length === 0 ? (
                  <p className="text-[13px] text-teal font-medium">None.</p>
                ) : (
                  <div className="flex flex-col gap-1 max-h-[160px] overflow-y-auto">
                    {impact.inFlightOnCurrentVersion.map((c) => (
                      <label key={c.caseId} className="flex items-center gap-2.5 px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px] cursor-pointer">
                        <Checkbox checked={rescoreIds.has(c.caseId)} onChange={() => onToggleRescore(c.caseId)} />
                        <span className="flex-1 text-text-secondary">
                          {c.caseId} - {c.title}
                        </span>
                        <span className="text-[11px] text-text-faint shrink-0">{rescoreIds.has(c.caseId) ? 'Re-score on publish' : 'Stays as scored'}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              {impact.recomputation && (
                <div className="flex flex-col gap-1.5">
                  <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Recomputation preview - last {impact.recomputation.evaluatedCount} scored cases</span>
                  <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-verdant-1 text-[12.5px]">
                    <span className="text-text-secondary">Would land in a different band</span>
                    <span className="font-mono font-semibold text-teal">{impact.recomputation.changed.length}</span>
                  </div>
                  {impact.recomputation.changed.length > 0 && (
                    <div className="flex flex-col gap-1 max-h-[160px] overflow-y-auto">
                      {impact.recomputation.changed.map((c) => (
                        <div key={c.caseId} className="flex items-center justify-between px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px]">
                          <span className="text-text-secondary">
                            {c.caseId} - {c.title}
                          </span>
                          <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">
                            Band {c.fromBand} &rarr; Band {c.toBand}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3.5 py-3">
              <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
              <p className="text-[13px] leading-relaxed text-[#133338]">
                Anchor definitions change how analysts interpret the scale. No existing composite residual is recomputed and no case changes band. Cases scored before this version carry the anchor
                definitions in force at the time.
              </p>
            </div>
          )}

          <Textarea rows={2} placeholder="Version note (what changed and why)..." value={note} onChange={(e) => onNoteChange(e.target.value)} />

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button variant="primary" onClick={onConfirm} loading={confirming}>
              Confirm and publish new version
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function ScoringModelSection({
  onNavigateToRouting,
  refreshToken,
  onPublished,
}: {
  onNavigateToRouting: () => void;
  /** Changes identity whenever a sibling section (Rules & Routing's band editor) publishes a new
   * scoring model version, so this page knows to re-fetch instead of showing a stale version. */
  refreshToken?: unknown;
  /** Called after this page's own publish, so a sibling section holding its own copy of the
   * active version (Rules & Routing) knows to re-fetch too. */
  onPublished?: () => void;
}) {
  const { changes, addChange, clearSection, registerActions } = useWorkingSet();
  const toast = useToast();

  const [versions, setVersions] = useState<ScoringModelVersion[] | null>(null);
  const [routingRules, setRoutingRules] = useState<RoutingRules | null>(null);

  const [weights, setWeights] = useState<Record<AmlCategory, number>>({} as Record<AmlCategory, number>);
  const [likelihoodAnchors, setLikelihoodAnchors] = useState<AnchorLevel[]>([]);
  const [impactAnchors, setImpactAnchors] = useState<AnchorLevel[]>([]);
  const [activeBands, setActiveBands] = useState<BandDef[]>([]);
  const [retentionFloorPct, setRetentionFloorPct] = useState(40);
  const [initial, setInitial] = useState<ScoringModelCandidate | null>(null);

  const [weightsEditing, setWeightsEditing] = useState(false);
  const [weightsBuffer, setWeightsBuffer] = useState<Record<AmlCategory, number>>({} as Record<AmlCategory, number>);
  const [likelihoodEditing, setLikelihoodEditing] = useState(false);
  const [likelihoodBuffer, setLikelihoodBuffer] = useState<AnchorLevel[]>([]);
  const [impactEditing, setImpactEditing] = useState(false);
  const [impactBuffer, setImpactBuffer] = useState<AnchorLevel[]>([]);

  const [viewingVersion, setViewingVersion] = useState<string | null>(null);

  const [impact, setImpact] = useState<ScoringModelImpact | null>(null);
  const [impactOpen, setImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [rescoreIds, setRescoreIds] = useState<Set<string>>(new Set());
  const [publishing, setPublishing] = useState(false);

  const [testScope, setTestScope] = useState<Set<AmlCategory>>(new Set());
  const [testInputs, setTestInputs] = useState<Record<string, { likelihood: number; impact: number; ce: number }>>({});
  const [testResult, setTestResult] = useState<ReturnType<typeof computeScoring> | null>(null);

  async function reload() {
    const [vers, routing] = await Promise.all([getScoringModelVersions(), getRoutingRules()]);
    setVersions(vers);
    setRoutingRules(routing);
    const active = vers.find((v) => v.status === 'active') ?? vers[vers.length - 1];
    setWeights(active.categoryWeights);
    setLikelihoodAnchors(active.likelihoodAnchors);
    setImpactAnchors(active.impactAnchors);
    setActiveBands(active.bands);
    setRetentionFloorPct(active.retentionFloorPct);
    setInitial({ categoryWeights: active.categoryWeights, likelihoodAnchors: active.likelihoodAnchors, impactAnchors: active.impactAnchors, bands: active.bands });
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Band boundaries publish from Rules & Routing, a sibling section with its own fetch of the
  // same scoring model versions - re-sync when that happens so this page never shows a stale
  // active version. Skipped while a local edit is pending here, so an in-progress weight or
  // anchor change is never silently discarded by an unrelated band publish.
  useEffect(() => {
    if (refreshToken !== undefined && !changes.some((c) => c.sectionId === SECTION_ID)) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  function candidate(): ScoringModelCandidate {
    return { categoryWeights: weights, likelihoodAnchors, impactAnchors, bands: activeBands };
  }

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }

  async function handleReviewImpact() {
    const result = await previewScoringModelImpact(candidate());
    setImpact(result);
    setRescoreIds(new Set());
    setImpactOpen(true);
  }

  async function handlePublish() {
    setPublishing(true);
    await commitScoringModelChange(candidate(), note.trim() || 'Category weight or anchor definition update.', undefined, Array.from(rescoreIds));
    setPublishing(false);
    setImpactOpen(false);
    setNote('');
    clearSection(SECTION_ID);
    toast('New scoring model version published. In-flight drafts stay on their starting version unless re-scored.', 'success');
    reload();
    onPublished?.();
  }

  function handleDiscard() {
    if (!initial) return;
    setWeights(initial.categoryWeights);
    setLikelihoodAnchors(initial.likelihoodAnchors);
    setImpactAnchors(initial.impactAnchors);
    clearSection(SECTION_ID);
  }

  useEffect(() => {
    registerActions(SECTION_ID, { publish: handleReviewImpact, discard: handleDiscard });
  });

  if (!versions || !routingRules) return <Skeleton className="h-96 rounded-md" />;
  const routing: RoutingRules = routingRules;

  const myChanges = changes.filter((c) => c.sectionId === SECTION_ID);
  const activeVersion = versions.find((v) => v.status === 'active') ?? versions[versions.length - 1];

  function startEditWeights() {
    setWeightsBuffer(weights);
    setWeightsEditing(true);
  }
  function confirmEditWeights() {
    const prev = weights;
    setWeights(weightsBuffer);
    setWeightsEditing(false);
    if (JSON.stringify(prev) !== JSON.stringify(weightsBuffer)) log('Updated category weights', () => setWeights(prev));
  }

  function startEditLikelihood() {
    setLikelihoodBuffer(likelihoodAnchors);
    setLikelihoodEditing(true);
  }
  function confirmEditLikelihood() {
    const prev = likelihoodAnchors;
    setLikelihoodAnchors(likelihoodBuffer);
    setLikelihoodEditing(false);
    if (JSON.stringify(prev) !== JSON.stringify(likelihoodBuffer)) log('Updated likelihood anchor definitions', () => setLikelihoodAnchors(prev));
  }

  function startEditImpact() {
    setImpactBuffer(impactAnchors);
    setImpactEditing(true);
  }
  function confirmEditImpact() {
    const prev = impactAnchors;
    setImpactAnchors(impactBuffer);
    setImpactEditing(false);
    if (JSON.stringify(prev) !== JSON.stringify(impactBuffer)) log('Updated impact anchor definitions', () => setImpactAnchors(prev));
  }

  function toggleRescore(caseId: string) {
    setRescoreIds((s) => {
      const next = new Set(s);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });
  }

  function toggleTestScope(cat: AmlCategory) {
    setTestScope((s) => {
      const next = new Set(s);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  }
  function updateTestInput(cat: AmlCategory, patch: Partial<{ likelihood: number; impact: number; ce: number }>) {
    setTestInputs((t) => {
      const prev = t[cat] ?? { likelihood: 1, impact: 1, ce: 0 };
      return { ...t, [cat]: { ...prev, ...patch } };
    });
  }
  function runTest() {
    const categories = Array.from(testScope);
    const factorInputs: CategoryFactorInput[] = categories.map((cat) => ({
      category: cat,
      likelihood: testInputs[cat]?.likelihood ?? 1,
      impact: testInputs[cat]?.impact ?? 1,
      likelihoodProvenance: 'analyst-override',
      impactProvenance: 'analyst-override',
    }));
    const controls: CaseControl[] = categories.map((cat) => ({
      id: `test-${cat}`,
      controlId: `test-${cat}`,
      name: 'Test control',
      category: cat,
      weight: 1,
      effectivenessAI: testInputs[cat]?.ce ?? 0,
      effectivenessConfirmed: null,
      evidence: null,
      judgmentOnly: false,
      source: 'analyst-added',
      status: 'confirmed',
    }));
    const model: ScoringModelVersion = { ...activeVersion, categoryWeights: weights, likelihoodAnchors, impactAnchors, bands: activeBands, retentionFloorPct };
    setTestResult(computeScoring(model, categories, factorInputs, controls, routing));
  }

  const exampleValues = normalisedExample(weightsEditing ? weightsBuffer : weights);
  const sortedVersions = versions.slice().sort((a, b) => (a.effectiveDate < b.effectiveDate ? 1 : -1));

  return (
    <div className="flex flex-col gap-4">
      <Card material="flat" className="p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="text-sm font-bold text-text-primary">Category weights</span>
            <Badge tone="success">{activeVersion.version}</Badge>
          </div>
          {!weightsEditing && (
            <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={startEditWeights}>
              Edit
            </Button>
          )}
        </div>
        <p className="text-[12.5px] text-text-muted -mt-2">
          Relative weights, normalised across whichever AML categories are in scope for a given case. They are not shares of a whole and need not sum to anything.
        </p>
        <div className="flex flex-col gap-1.5 max-w-md">
          {ALL_AML_CATEGORIES.map((cat) => (
            <div key={cat} className="flex items-center gap-3">
              <span className="text-[12.5px] text-text-secondary flex-1">{cat}</span>
              {weightsEditing ? (
                <Input
                  type="number"
                  min={0}
                  step={0.05}
                  value={weightsBuffer[cat]}
                  onChange={(e) => setWeightsBuffer((w) => ({ ...w, [cat]: Number(e.target.value) }))}
                  className="w-24 text-center py-1.5"
                />
              ) : (
                <span className="font-mono text-[13px] font-semibold text-text-primary w-16 text-right">{weights[cat]?.toFixed(2)}</span>
              )}
            </div>
          ))}
        </div>
        {weightsEditing && (
          <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
            <Button size="sm" variant="ghost" onClick={() => setWeightsEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmEditWeights}>
              Confirm
            </Button>
          </div>
        )}
        <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3.5 py-3">
          <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
          <p className="text-[12px] text-[#133338] leading-relaxed">
            <strong>Worked example</strong> - Case FCRM-2026-0147 has geography, delivery channel and third party, and product and service in scope. Weights normalise to{' '}
            {exampleValues[0].toFixed(2)}, {exampleValues[1].toFixed(2)} and {exampleValues[2].toFixed(2)} over a total of 1.00. Customer / entity is not in scope and contributes nothing.
          </p>
        </div>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Likelihood anchors</span>
          {!likelihoodEditing && (
            <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={startEditLikelihood}>
              Edit
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-3">
          {(likelihoodEditing ? likelihoodBuffer : likelihoodAnchors).map((a, i) => (
            <div key={a.level} className="flex items-start gap-3">
              <span className="font-mono font-semibold text-text-primary w-5 shrink-0 pt-1.5">{a.level}</span>
              {likelihoodEditing ? (
                <div className="flex-1 flex flex-col gap-1.5">
                  <Input
                    value={a.label}
                    onChange={(e) => setLikelihoodBuffer((arr) => arr.map((x, idx) => (idx === i ? { ...x, label: e.target.value } : x)))}
                    className="py-1.5 text-[13px] font-semibold"
                    placeholder="Label"
                  />
                  <Textarea
                    rows={2}
                    value={a.definition}
                    onChange={(e) => setLikelihoodBuffer((arr) => arr.map((x, idx) => (idx === i ? { ...x, definition: e.target.value } : x)))}
                    className="text-[12.5px]"
                    placeholder="Definition"
                  />
                </div>
              ) : (
                <p className="text-[12.5px] text-text-secondary flex-1 pt-0.5">
                  <span className="font-semibold text-text-primary">{a.label}</span> - {a.definition}
                </p>
              )}
            </div>
          ))}
        </div>
        {likelihoodEditing && (
          <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
            <Button size="sm" variant="ghost" onClick={() => setLikelihoodEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmEditLikelihood}>
              Confirm
            </Button>
          </div>
        )}
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Impact anchors</span>
          {!impactEditing && (
            <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={startEditImpact}>
              Edit
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-3">
          {(impactEditing ? impactBuffer : impactAnchors).map((a, i) => (
            <div key={a.level} className="flex items-start gap-3">
              <span className="font-mono font-semibold text-text-primary w-5 shrink-0 pt-1.5">{a.level}</span>
              {impactEditing ? (
                <div className="flex-1 flex flex-col gap-1.5">
                  <Input
                    value={a.label}
                    onChange={(e) => setImpactBuffer((arr) => arr.map((x, idx) => (idx === i ? { ...x, label: e.target.value } : x)))}
                    className="py-1.5 text-[13px] font-semibold"
                    placeholder="Label"
                  />
                  <Textarea
                    rows={2}
                    value={a.definition}
                    onChange={(e) => setImpactBuffer((arr) => arr.map((x, idx) => (idx === i ? { ...x, definition: e.target.value } : x)))}
                    className="text-[12.5px]"
                    placeholder="Definition"
                  />
                </div>
              ) : (
                <p className="text-[12.5px] text-text-secondary flex-1 pt-0.5">
                  <span className="font-semibold text-text-primary">{a.label}</span> - {a.definition}
                </p>
              )}
            </div>
          ))}
        </div>
        {impactEditing && (
          <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
            <Button size="sm" variant="ghost" onClick={() => setImpactEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmEditImpact}>
              Confirm
            </Button>
          </div>
        )}
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-text-primary">Retention floor</span>
          <Icon name="lock" size={12} className="text-text-faint" />
        </div>
        <span className="text-[13.5px] text-text-primary">
          Fixed at {retentionFloorPct}% - not configurable, on this or any page. Controls mitigate risk and never eliminate it, so some share of inherent risk always survives no matter how
          strong the controls.
        </span>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary">Bands</span>
        <p className="text-[12px] text-text-muted -mt-1">
          Editable from{' '}
          <button onClick={onNavigateToRouting} className="underline text-carbon-moss hover:text-carbon-moss-hover cursor-pointer font-semibold">
            Rules &amp; Routing
          </button>{' '}
          - band boundaries decide routing, so they live there. An analyst changing bands needs the weights in view and vice versa.
        </p>
        <div className="grid grid-cols-1 gap-1.5">
          {activeBands.map((b) => (
            <div key={b.band} className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
              <span className="font-mono font-semibold text-text-primary w-14 shrink-0">Band {b.band}</span>
              <span className="font-mono text-text-faint w-24 shrink-0">
                {b.min.toFixed(1)}-{b.max.toFixed(1)}
              </span>
              <span className="text-text-secondary">{b.route}</span>
            </div>
          ))}
        </div>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Version history</span>
        <div className="flex flex-col divide-y divide-border-hairline">
          {sortedVersions.map((v) => (
            <div key={v.version} className="py-2.5 flex flex-col gap-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-[12.5px] font-semibold text-text-primary">{v.version}</span>
                {v.status === 'active' && <Badge tone="success">Active</Badge>}
                <span className="text-[11.5px] text-text-faint">{formatDate(v.effectiveDate)}</span>
                {v.changedBy && <span className="text-[11.5px] text-text-faint">{v.changedBy}</span>}
                <button
                  onClick={() => setViewingVersion((cur) => (cur === v.version ? null : v.version))}
                  className="ml-auto text-[11.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer"
                >
                  {viewingVersion === v.version ? 'Hide weights & anchors' : 'View weights & anchors'}
                </button>
              </div>
              {v.changeNote && <p className="text-[12.5px] text-text-secondary leading-relaxed">{v.changeNote}</p>}
              {viewingVersion === v.version && (
                <div className="flex flex-col gap-2 mt-1 p-3 rounded-md bg-surface-tertiary text-[12px]">
                  <div className="grid grid-cols-2 gap-1.5">
                    {ALL_AML_CATEGORIES.map((cat) => (
                      <div key={cat} className="flex justify-between">
                        <span className="text-text-faint">{cat}</span>
                        <span className="font-mono font-semibold text-text-primary">{v.categoryWeights[cat].toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                  <div className="flex flex-col gap-1 pt-1.5 border-t border-border-hairline">
                    <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Likelihood anchors</span>
                    {v.likelihoodAnchors.map((a) => (
                      <span key={a.level} className="text-text-secondary">
                        {a.level} - <strong className="text-text-primary">{a.label}</strong> - {a.definition}
                      </span>
                    ))}
                  </div>
                  <div className="flex flex-col gap-1 pt-1.5 border-t border-border-hairline">
                    <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Impact anchors</span>
                    {v.impactAnchors.map((a) => (
                      <span key={a.level} className="text-text-secondary">
                        {a.level} - <strong className="text-text-primary">{a.label}</strong> - {a.definition}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary">Test panel</span>
        <p className="text-[12.5px] text-text-muted -mt-1">Read-only - runs against the model as currently edited, including unsaved changes. Changes nothing.</p>
        <div className="flex flex-col gap-2">
          {ALL_AML_CATEGORIES.map((cat) => (
            <div key={cat} className="flex items-center gap-3 flex-wrap">
              <Checkbox checked={testScope.has(cat)} onChange={() => toggleTestScope(cat)} label={<span className="text-[12.5px] w-[210px] inline-block">{cat}</span>} />
              {testScope.has(cat) && (
                <div className="flex items-center gap-2.5">
                  <label className="text-[11px] text-text-faint">Likelihood</label>
                  <Input
                    type="number"
                    min={1}
                    max={5}
                    value={testInputs[cat]?.likelihood ?? 1}
                    onChange={(e) => updateTestInput(cat, { likelihood: Number(e.target.value) })}
                    className="w-16 py-1 text-center text-[12px]"
                  />
                  <label className="text-[11px] text-text-faint">Impact</label>
                  <Input
                    type="number"
                    min={1}
                    max={5}
                    value={testInputs[cat]?.impact ?? 1}
                    onChange={(e) => updateTestInput(cat, { impact: Number(e.target.value) })}
                    className="w-16 py-1 text-center text-[12px]"
                  />
                  <label className="text-[11px] text-text-faint">Control effectiveness</label>
                  <Input
                    type="number"
                    min={0}
                    max={10}
                    step={0.5}
                    value={testInputs[cat]?.ce ?? 0}
                    onChange={(e) => updateTestInput(cat, { ce: Number(e.target.value) })}
                    className="w-16 py-1 text-center text-[12px]"
                  />
                </div>
              )}
            </div>
          ))}
        </div>
        <Button variant="primary" className="self-start" onClick={runTest} disabled={testScope.size === 0}>
          Run test
        </Button>

        {testResult && (
          <div className="flex flex-col gap-2 pt-2 border-t border-border-hairline">
            <div className="grid grid-cols-[1.3fr_70px_80px_70px_70px] gap-2 px-3 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">
              <span>Category</span>
              <span>Inherent</span>
              <span>Retention</span>
              <span>Residual</span>
              <span>Weight</span>
            </div>
            {testResult.categories.map((cs) => (
              <div key={cs.category} className="grid grid-cols-[1.3fr_70px_80px_70px_70px] gap-2 items-center px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
                <span className="text-text-secondary">{cs.category}</span>
                <span className="font-mono">{cs.inherent}</span>
                <span className="font-mono">{cs.retention.toFixed(2)}</span>
                <span className="font-mono font-semibold">{cs.residual}</span>
                <span className="font-mono">{Math.round(cs.weight * 100)}%</span>
              </div>
            ))}
            <div className="flex items-center justify-between px-3.5 py-3 rounded-md bg-verdant-1 text-[13px]">
              <div className="flex flex-col gap-0.5">
                <span className="text-text-secondary">Composite residual</span>
                <span className="font-mono font-bold text-[16px] text-text-primary">{testResult.compositeResidual}</span>
              </div>
              <div className="flex flex-col gap-0.5 items-end">
                <Badge tone="success">Band {testResult.band}</Badge>
                <span className="text-[11.5px] text-text-faint">{testResult.route}</span>
              </div>
            </div>
          </div>
        )}
      </Card>

      <ScoringImpactModal
        open={impactOpen}
        impact={impact}
        pendingChangeCount={myChanges.length}
        note={note}
        onNoteChange={setNote}
        rescoreIds={rescoreIds}
        onToggleRescore={toggleRescore}
        onCancel={() => setImpactOpen(false)}
        onConfirm={handlePublish}
        confirming={publishing}
      />
    </div>
  );
}
