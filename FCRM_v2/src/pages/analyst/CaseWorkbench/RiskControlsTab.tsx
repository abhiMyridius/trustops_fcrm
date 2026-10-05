import { Fragment, useEffect, useState } from 'react';
import type { AmlCategory, AnchorLevel, CaseControl, CategoryScore, PlannedControl, ScoringResult } from '@/types/analyst';
import { ALL_AML_CATEGORIES } from '@/types/analyst';
import {
  acknowledgeBoundaryFlag,
  addExistingControlToCase,
  addPlannedControl,
  confirmControl,
  getAnalystCase,
  getControlInventory,
  getGeographyJurisdictionFlag,
  getScoring,
  overrideControl,
  overrideImpact,
  overrideLikelihood,
  proposeAndAddNewControl,
  revertImpact,
  revertLikelihood,
  type GeographyJurisdictionFlag,
} from '@/services/analystApi';
import { useNavigate } from 'react-router-dom';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Select, Textarea } from '@/components/ui/Form';
import { Modal } from '@/components/ui/Modal';
import { Drawer } from '@/components/ui/Drawer';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { ScoreBadge } from '@/pages/analyst/shared/ScoreBadge';
import { ProvenanceTag } from '@/pages/analyst/shared/ProvenanceTag';
import { RiskMatrix } from './RiskMatrix';
import { SCORING_MODEL_VERSIONS } from '@/mocks/seed/analystConfig';
import type { ControlInventoryItem } from '@/types/analyst';

const anchors = SCORING_MODEL_VERSIONS[0];

// Round only at the end of each displayed step (§7 of the editable-factors mod) - intermediate
// rounding shifts composites enough to move a case across a band edge, so every derivation below
// rounds its own final value and nothing upstream of it.
function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

type Factor = 'likelihood' | 'impact';

// One row per in-scope category, plus a Composite total row - the table itself is the composite
// derivation (redesign, prompted by "I don't know what 20, 16 and 12 came from" against the old
// layout, where the composite arithmetic quoted bare per-category numbers several sections above
// where those numbers were ever named). The two right-hand columns are the per-category
// contribution to each composite total, so the total row's own cells are visibly the sum of the
// column above them - no separate formula string repeating numbers the reader already has to
// track down. Clicking a row opens that category's full working in the side drawer.
function CompositeRow({ c, onOpen }: { c: CategoryScore; onOpen: () => void }) {
  return (
    <tr className="border-t border-border-hairline hover:bg-surface-tertiary cursor-pointer transition-colors" onClick={onOpen}>
      <td className="px-3 py-2.5 font-semibold text-text-primary">{c.category}</td>
      <td className="px-3 py-2.5">
        {c.likelihood}
        {c.likelihoodProposed != null && <Icon name="edit" size={10} className="inline-block ml-1 text-warning align-middle" />}
      </td>
      <td className="px-3 py-2.5">
        {c.impact}
        {c.impactProposed != null && <Icon name="edit" size={10} className="inline-block ml-1 text-warning align-middle" />}
      </td>
      <td className="px-3 py-2.5">{c.inherent}</td>
      <td className="px-3 py-2.5">{c.controlEffectiveness.toFixed(1)}</td>
      <td className="px-3 py-2.5">{c.retention.toFixed(2)}</td>
      <td className="px-3 py-2.5 font-semibold">{c.residual}</td>
      <td className="px-3 py-2.5">{c.weight.toFixed(2)}</td>
      <td className="px-3 py-2.5 font-mono text-text-secondary whitespace-nowrap">{(c.inherent * c.weight).toFixed(2)}</td>
      <td className="px-3 py-2.5 font-mono text-text-secondary font-semibold whitespace-nowrap">{(c.residual * c.weight).toFixed(2)}</td>
      <td className="px-2 py-2.5">
        <Icon name="chevron-right" size={14} className="text-text-faint" />
      </td>
    </tr>
  );
}

/** Everything a FactorBlock needs, read off whichever of likelihood/impact it's rendering -
 * keeps FactorBlock itself factor-agnostic instead of branching internally. */
function factorFields(c: CategoryScore, factor: Factor) {
  return factor === 'likelihood'
    ? { value: c.likelihood, provenance: c.likelihoodProvenance, overrideReason: c.likelihoodOverrideReason, proposed: c.likelihoodProposed, baselineDetail: c.likelihoodBaselineDetail }
    : { value: c.impact, provenance: c.impactProvenance, overrideReason: c.impactOverrideReason, proposed: c.impactProposed, baselineDetail: undefined };
}

/** The level 1-5 selector (editable-factors mod, §1.2): each level shows its own written anchor
 * definition, so the analyst chooses against the scale rather than typing a bare number. */
function FactorEditorPanel({ anchors, current, onSave, onCancel }: { anchors: AnchorLevel[]; current: number; onSave: (value: number, reason: string) => void; onCancel: () => void }) {
  const [selected, setSelected] = useState(current);
  const [reason, setReason] = useState('');
  const changed = selected !== current;

  return (
    <div className="flex flex-col gap-2.5 border border-border-strong rounded-md p-3 bg-white">
      <div className="flex flex-col gap-1">
        {anchors.map((a) => (
          <button
            key={a.level}
            onClick={() => setSelected(a.level)}
            className={`text-left rounded-md px-2.5 py-1.5 border cursor-pointer transition-colors ${selected === a.level ? 'border-carbon-moss bg-[#F4F8F7]' : 'border-border-hairline hover:bg-surface-tertiary'}`}
          >
            <span className="text-[12px] font-semibold text-text-primary">
              {a.level} - {a.label}
            </span>
            <p className="text-[11px] text-text-muted mt-0.5">{a.definition}</p>
          </button>
        ))}
      </div>
      {changed && <Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for the adjustment (required when changing from the proposed value)" />}
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={changed && !reason.trim()} onClick={() => (changed ? onSave(selected, reason.trim()) : onCancel())}>
          Save
        </Button>
      </div>
    </div>
  );
}

/** Likelihood or impact for one category - read-only display, or the editor in its place while
 * `editing`. `proposed` set means this factor has been analyst-adjusted at least once; the
 * original proposal stays available for "Revert to proposal" until it is. */
function FactorBlock({
  label,
  factor,
  c,
  anchors,
  canWrite,
  editing,
  onStartEdit,
  onCancelEdit,
  onSave,
  onRevert,
}: {
  label: string;
  factor: Factor;
  c: CategoryScore;
  anchors: AnchorLevel[];
  canWrite: boolean;
  editing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSave: (value: number, reason: string) => void;
  onRevert: () => void;
}) {
  const { value, provenance, overrideReason, proposed, baselineDetail } = factorFields(c, factor);
  const anchor = anchors.find((a) => a.level === value);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[12px] font-semibold text-text-primary">
          {label}: {value}
        </span>
        <ProvenanceTag source={provenance} />
        {proposed != null && (
          <span className="text-[11px] text-text-faint">
            proposed {proposed} -&gt; adjusted {value}
          </span>
        )}
        {canWrite && !editing && (
          <div className="flex items-center gap-1.5 ml-auto">
            {proposed != null && (
              <Button size="sm" variant="ghost" onClick={onRevert}>
                Revert to proposal
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={onStartEdit}>
              Edit
            </Button>
          </div>
        )}
      </div>
      {!editing && (
        <p className="text-[11.5px] text-text-muted">
          <span className="font-semibold">{anchor?.label}</span> - {anchor?.definition}
        </p>
      )}
      {!editing && baselineDetail && <p className="text-[11px] text-text-faint italic">{baselineDetail}</p>}
      {!editing && overrideReason && <p className="text-[11.5px] text-warning bg-[#FBF3E4] rounded-md px-2 py-1.5">Adjustment reason: {overrideReason}</p>}
      {editing && <FactorEditorPanel anchors={anchors} current={value} onSave={onSave} onCancel={onCancelEdit} />}
    </div>
  );
}

/** The category CE weighted mean shown in full, itemised by control, with overridden controls
 * showing both the proposed and confirmed CE inside the arithmetic and the renormalisation
 * explained in words when the case carries fewer controls than the inventory holds for this
 * category, since that is a real, not a rounding, effect on the result. */
function CategoryCeDerivation({ category, controls, missingFromInventory }: { category: AmlCategory; controls: CaseControl[]; missingFromInventory: number }) {
  const inCategory = controls.filter((ctl) => ctl.category === category);
  const totalWeight = round2(inCategory.reduce((s, ctl) => s + ctl.weight, 0));
  const totalProduct = inCategory.reduce((s, ctl) => s + (ctl.effectivenessConfirmed ?? ctl.effectivenessAI) * ctl.weight, 0);
  const categoryCE = totalWeight > 0 ? totalProduct / totalWeight : 0;

  return (
    <div className="bg-surface-tertiary rounded-md p-4">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint block mb-2">Category CE derivation</span>
      <p className="text-[12px] font-mono text-text-primary mb-2">Category CE = &Sigma;(control CE &times; weight) &divide; &Sigma;(weight)</p>
      <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 gap-y-1 text-[12px] font-mono text-text-primary overflow-x-auto">
        {inCategory.map((ctl) => {
          const ce = ctl.effectivenessConfirmed ?? ctl.effectivenessAI;
          const overridden = ctl.effectivenessConfirmed != null && ctl.effectivenessConfirmed !== ctl.effectivenessAI;
          return (
            <Fragment key={ctl.id}>
              <span className="truncate">{ctl.name}</span>
              <span className="text-right whitespace-nowrap">
                {ce.toFixed(1)} &times; {ctl.weight.toFixed(2)} = {(ce * ctl.weight).toFixed(3)}
              </span>
              <span className="text-text-faint text-[11px] whitespace-nowrap">{overridden ? `(overridden from ${ctl.effectivenessAI.toFixed(1)})` : ''}</span>
            </Fragment>
          );
        })}
        <div className="col-span-3 border-t border-border-strong my-1" />
        <span className="text-text-secondary">&Sigma;(control CE &times; weight)</span>
        <span className="text-right font-semibold whitespace-nowrap">{totalProduct.toFixed(3)}</span>
        <span />
        <span className="text-text-secondary">&Sigma;(weight)</span>
        <span className="text-right whitespace-nowrap">{totalWeight.toFixed(2)}</span>
        <span />
        <span className="text-text-secondary font-semibold">Category CE</span>
        <span className="text-right font-bold whitespace-nowrap">
          {totalProduct.toFixed(3)} &divide; {totalWeight.toFixed(2)} = {categoryCE.toFixed(1)}
        </span>
        <span />
      </div>
      {missingFromInventory > 0 && (
        <p className="text-[11.5px] text-text-secondary mt-2.5">
          &Sigma;(weight) = {totalWeight.toFixed(2)}. {missingFromInventory} inventory control{missingFromInventory === 1 ? ' is' : 's are'} not on this case, so the {inCategory.length} present carry proportionally
          more weight.
        </p>
      )}
    </div>
  );
}

/** The full working for one category - opened in a side drawer from its row in the composite
 * table (redesign: previously an inline expansion split across two more sections further down
 * the page, disconnected from the row that named its numbers). Likelihood, impact, the category
 * CE derivation and the retention substitution all live here now, so opening a category answers
 * everything about it in one place. */
function CategoryDetail({
  c,
  model,
  controls,
  missingFromInventory,
  rawWeight,
  rawWeightSum,
  jurisdictionFlag,
  onNavigateToJurisdictionList,
  canWrite,
  editingFactor,
  onStartEditFactor,
  onCancelEditFactor,
  onSaveFactor,
  onRevertFactor,
}: {
  c: CategoryScore;
  model: typeof anchors;
  controls: CaseControl[];
  missingFromInventory: number;
  rawWeight: number;
  rawWeightSum: number;
  jurisdictionFlag: GeographyJurisdictionFlag | null;
  onNavigateToJurisdictionList: () => void;
  canWrite: boolean;
  editingFactor: Factor | null;
  onStartEditFactor: (factor: Factor) => void;
  onCancelEditFactor: () => void;
  onSaveFactor: (factor: Factor, value: number, reason: string) => void;
  onRevertFactor: (factor: Factor) => void;
}) {
  // The 0.6 here is (1 - retention floor / 100) - shown as a substitution with the floor named,
  // since it's the least intuitive constant in the model.
  const floorMultiplier = round2(1 - model.retentionFloorPct / 100);
  return (
    <div className="flex flex-col gap-4">
      {c.category === 'Geography' && jurisdictionFlag && jurisdictionFlag.unlistedNames.length > 0 && (
        <div className="flex items-start gap-2.5 bg-[#FBF3E4] rounded-md px-3.5 py-3">
          <Icon name="warning" size={14} className="text-[#B98A1E] shrink-0 mt-0.5" />
          <div className="flex flex-col gap-1">
            <p className="text-[12.5px] text-[#5c4415] leading-relaxed">
              <strong>{jurisdictionFlag.unlistedNames.join(', ')}</strong> {jurisdictionFlag.unlistedNames.length === 1 ? "isn't" : "aren't"} on the Jurisdiction Risk List - the configured default of{' '}
              <strong>{jurisdictionFlag.defaultUnlistedValue}</strong> was applied instead of a listed rating.
            </p>
            <button onClick={onNavigateToJurisdictionList} className="text-[11.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer text-left">
              Add it to the Jurisdiction Risk List
            </button>
          </div>
        </div>
      )}

      <FactorBlock
        label="Likelihood"
        factor="likelihood"
        c={c}
        anchors={model.likelihoodAnchors}
        canWrite={canWrite}
        editing={editingFactor === 'likelihood'}
        onStartEdit={() => onStartEditFactor('likelihood')}
        onCancelEdit={onCancelEditFactor}
        onSave={(v, r) => onSaveFactor('likelihood', v, r)}
        onRevert={() => onRevertFactor('likelihood')}
      />
      <FactorBlock
        label="Impact"
        factor="impact"
        c={c}
        anchors={model.impactAnchors}
        canWrite={canWrite}
        editing={editingFactor === 'impact'}
        onStartEdit={() => onStartEditFactor('impact')}
        onCancelEdit={onCancelEditFactor}
        onSave={(v, r) => onSaveFactor('impact', v, r)}
        onRevert={() => onRevertFactor('impact')}
      />

      <div className="bg-surface-tertiary rounded-md p-3.5">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint block mb-1.5">Inherent</span>
        <p className="text-[13px] font-mono text-text-primary">
          Likelihood &times; Impact = {c.likelihood} &times; {c.impact} = <strong>{c.inherent}</strong>
        </p>
      </div>

      <div className="bg-surface-tertiary rounded-md p-3.5">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint block mb-1.5">Category weight</span>
        <p className="text-[12.5px] font-mono text-text-primary">
          raw {rawWeight.toFixed(2)} &divide; {rawWeightSum.toFixed(2)} (&Sigma; raw weight, in-scope categories) = <strong>{c.weight.toFixed(2)}</strong>
        </p>
      </div>

      <CategoryCeDerivation category={c.category} controls={controls} missingFromInventory={missingFromInventory} />

      <div className="bg-surface-tertiary rounded-md p-4">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint block mb-2">Retention and residual</span>
        <div className="flex flex-col gap-1.5 text-[12.5px] font-mono text-text-primary">
          <span>Retention = 1 - ({floorMultiplier} x Category CE &divide; 10)</span>
          <span>
            {'          = 1 - ('}
            {floorMultiplier} x {c.controlEffectiveness.toFixed(1)} &divide; 10)
          </span>
          <span>
            {'          = 1 - '}
            {(floorMultiplier * (c.controlEffectiveness / 10)).toFixed(3)}
          </span>
          <span>
            {'          = '}
            <strong>{c.retention.toFixed(3)}</strong>
          </span>
        </div>
        <p className="text-[11px] text-text-faint mt-2 leading-relaxed">
          The {floorMultiplier} multiplier sets the floor. At a category CE of 10 the retention factor is {(model.retentionFloorPct / 100).toFixed(2)}, so {model.retentionFloorPct}% of inherent risk always
          survives. Controls mitigate risk and never eliminate it.
        </p>
        <div className="flex flex-col gap-1.5 text-[12.5px] font-mono text-text-primary mt-3 pt-3 border-t border-border-hairline">
          <span>
            Residual = Inherent x Retention = {c.inherent} x {c.retention.toFixed(3)} = <strong>{c.residual}</strong>
          </span>
        </div>
      </div>
    </div>
  );
}

function EvidenceBadge({ ctl }: { ctl: CaseControl }) {
  if (!ctl.evidence) return <Badge tone="warning">Judgment only</Badge>;
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11.5px] text-text-secondary">
        {ctl.evidence.name} ({ctl.evidence.type}, {ctl.evidence.date})
      </span>
      {ctl.evidence.recency === 'stale' && <Badge tone="danger">Stale</Badge>}
    </div>
  );
}

function ControlCard({ ctl, canWrite, onConfirm, onOverride }: { ctl: CaseControl; canWrite: boolean; onConfirm: (v: number) => void; onOverride: (v: number, reason: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(ctl.effectivenessConfirmed ?? ctl.effectivenessAI));
  const [reason, setReason] = useState('');

  function openEdit() {
    setValue(String(ctl.effectivenessConfirmed ?? ctl.effectivenessAI));
    setReason('');
    setEditing(true);
  }

  function handleSave() {
    const v = Number(value);
    if (Number.isNaN(v)) return;
    if (v !== ctl.effectivenessAI) {
      if (!reason.trim()) return;
      onOverride(v, reason.trim());
    } else {
      onConfirm(v);
    }
    setEditing(false);
    setReason('');
  }

  return (
    <div className="border border-border-strong rounded-lg p-3.5 flex flex-col gap-2.5 bg-white">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-[13px] font-semibold text-text-primary">{ctl.name}</span>
          <span className="text-[11px] text-text-faint">
            {ctl.category} - weight {(ctl.weight * 100).toFixed(0)}% {ctl.source === 'analyst-added' && '- analyst-added'}
          </span>
        </div>
        <Badge tone={ctl.status === 'overridden' ? 'warning' : ctl.status === 'confirmed' ? 'success' : 'default'}>
          {ctl.status === 'overridden' ? 'Overridden' : ctl.status === 'confirmed' ? 'Confirmed' : 'Proposed'}
        </Badge>
      </div>
      <EvidenceBadge ctl={ctl} />
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-text-muted">
          AI-proposed: <strong className="text-text-primary">{ctl.effectivenessAI.toFixed(1)}</strong>
          {ctl.effectivenessConfirmed != null && ctl.effectivenessConfirmed !== ctl.effectivenessAI && (
            <>
              {' '}
              -&gt; confirmed: <strong className="text-text-primary">{ctl.effectivenessConfirmed.toFixed(1)}</strong>
            </>
          )}
        </span>
        {!editing && canWrite && (
          <div className="flex items-center gap-2">
            {ctl.status === 'proposed' && (
              <Button size="sm" variant="secondary" onClick={() => onConfirm(ctl.effectivenessAI)}>
                Confirm
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={openEdit}>
              Edit
            </Button>
          </div>
        )}
      </div>
      {ctl.overrideReason && <p className="text-[11.5px] text-warning bg-[#FBF3E4] rounded-md px-2 py-1.5">Override reason: {ctl.overrideReason}</p>}
      {editing && canWrite && (
        <div className="flex flex-col gap-2 pt-2 border-t border-border-hairline">
          <div className="flex items-center gap-2">
            <Input type="number" min={0} max={10} step={0.1} value={value} onChange={(e) => setValue(e.target.value)} className="w-24 py-1.5" />
            <span className="text-[11.5px] text-text-faint">0.0 - 10.0</span>
          </div>
          {Number(value) !== ctl.effectivenessAI && (
            <Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for override (required when changing from AI-proposed value)" />
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={handleSave}>
              Save
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function RiskControlsTab({
  caseId,
  hasRun,
  canWrite,
  onRunAnalysis,
  onRequestClarification,
  onReload,
}: {
  caseId: string;
  hasRun: boolean;
  canWrite: boolean;
  onRunAnalysis: () => void;
  onRequestClarification: () => void;
  onReload: () => void;
}) {
  const [scoring, setScoring] = useState<ScoringResult | null>(null);
  const [controls, setControls] = useState<CaseControl[]>([]);
  const [plannedControls, setPlannedControls] = useState<PlannedControl[]>([]);
  const [drawerCategory, setDrawerCategory] = useState<AmlCategory | null>(null);
  const [inventory, setInventory] = useState<ControlInventoryItem[]>([]);
  const [addControlOpen, setAddControlOpen] = useState(false);
  const [plannedOpen, setPlannedOpen] = useState(false);
  const [bandChangeInfo, setBandChangeInfo] = useState<{ before: ScoringResult; after: ScoringResult } | null>(null);
  const [jurisdictionFlag, setJurisdictionFlag] = useState<GeographyJurisdictionFlag | null>(null);
  const [editingFactor, setEditingFactor] = useState<Factor | null>(null);
  const toast = useToast();
  const navigate = useNavigate();

  async function reload() {
    const [s, inv, a, flag] = await Promise.all([getScoring(caseId), getControlInventory(), getAnalystCase(caseId), getGeographyJurisdictionFlag(caseId)]);
    setScoring(s);
    setControls(a.controls);
    setPlannedControls(a.plannedControls);
    setInventory(inv);
    setJurisdictionFlag(flag);
  }

  useEffect(() => {
    if (hasRun) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, hasRun]);

  if (!hasRun) {
    return (
      <EmptyState
        icon="lock"
        title="Risk & Controls will populate after the analysis run"
        description="Scoring, factor values, and mitigating controls appear here once you run the analysis from Summary."
        action={
          <Button variant="primary" onClick={onRunAnalysis}>
            Run analysis
          </Button>
        }
      />
    );
  }

  if (!scoring) return <Skeleton className="h-96 rounded-md" />;

  const confirmedCount = controls.filter((c) => c.status !== 'proposed').length;
  const allConfirmed = confirmedCount === controls.length && controls.length > 0;
  const outOfScope = ALL_AML_CATEGORIES.filter((cat) => !scoring.categories.some((c) => c.category === cat));
  const rawWeightSum = round2(scoring.categories.reduce((s, c) => s + (anchors.categoryWeights[c.category] ?? 0), 0));
  const reductionPct = scoring.compositeInherent > 0 ? Math.round((1 - scoring.compositeResidual / scoring.compositeInherent) * 100) : 0;

  async function handleConfirm(ctl: CaseControl, v: number) {
    await confirmControl(caseId, ctl.id, v);
    reload();
  }

  async function handleOverride(ctl: CaseControl, v: number, reason: string) {
    const result = await overrideControl(caseId, ctl.id, v, reason);
    await reload();
    onReload();
    if (result.bandChanged && result.before && result.after) {
      setBandChangeInfo({ before: result.before, after: result.after });
    }
  }

  async function handleAcknowledgeBoundary() {
    await acknowledgeBoundaryFlag(caseId);
    reload();
  }

  async function handleSaveFactor(category: AmlCategory, factor: Factor, v: number, reason: string) {
    const result = await (factor === 'likelihood' ? overrideLikelihood(caseId, category, v, reason) : overrideImpact(caseId, category, v, reason));
    setEditingFactor(null);
    await reload();
    onReload();
    if (result.bandChanged && result.before && result.after) {
      setBandChangeInfo({ before: result.before, after: result.after });
    }
  }

  async function handleRevertFactor(category: AmlCategory, factor: Factor) {
    await (factor === 'likelihood' ? revertLikelihood(caseId, category) : revertImpact(caseId, category));
    await reload();
    onReload();
  }

  function openDrawer(category: AmlCategory) {
    setDrawerCategory(category);
    setEditingFactor(null);
  }
  function closeDrawer() {
    setDrawerCategory(null);
    setEditingFactor(null);
  }

  const byCategory = new Map<string, CaseControl[]>();
  for (const c of controls) {
    if (!byCategory.has(c.category)) byCategory.set(c.category, []);
    byCategory.get(c.category)!.push(c);
  }

  const drawerCat = drawerCategory ? scoring.categories.find((c) => c.category === drawerCategory) : null;

  return (
    <div className="flex flex-col gap-5">
      {/* Summary - composite residual is the number that decides routing; composite inherent sits
          beside it as context, not a rival number, with the reduction between the two stated in
          one sentence instead of a separately-derived block. */}
      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-8">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Composite residual</span>
              <ScoreBadge scoring={scoring} />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Composite inherent</span>
              <span className="text-[20px] font-bold text-text-secondary leading-none">{scoring.compositeInherent}</span>
              <span className="text-[11px] text-text-faint">before controls - not used for banding or routing</span>
            </div>
          </div>
          <span className="text-[11px] text-text-faint font-mono">model {scoring.modelVersion}</span>
        </div>
        <p className="text-[12.5px] text-text-secondary">
          Controls reduced exposure by <strong>{reductionPct}%</strong> (composite inherent {scoring.compositeInherent} &rarr; composite residual {scoring.compositeResidual}). The table below shows how each
          category adds up to both.
        </p>
        <p className="text-[11.5px] text-text-faint">
          Category weights normalise across the {scoring.categories.length} categor{scoring.categories.length === 1 ? 'y' : 'ies'} in scope on this case
          {outOfScope.length > 0 && (
            <>
              {' '}
              - {outOfScope.join(', ')} {outOfScope.length === 1 ? 'is' : 'are'} not in scope and contribute{outOfScope.length === 1 ? 's' : ''} nothing
            </>
          )}
          . Click a category below for its full weight, control-effectiveness and retention working.
        </p>
      </Card>

      {scoring.boundaryFlag && !scoring.boundaryAcknowledged && (
        <Card material="flat" className="p-4 flex items-start gap-3 border-2 border-warning bg-[#FBF3E4]">
          <Icon name="warning" size={18} className="text-warning shrink-0 mt-0.5" />
          <div className="flex-1">
            <span className="text-[13.5px] font-bold text-text-primary block">Boundary proximity flag</span>
            <p className="text-[12.5px] text-text-secondary mt-1">
              The composite residual ({scoring.compositeResidual}) is {scoring.boundaryFlag.distance} {scoring.boundaryFlag.direction === 'above' ? 'below' : 'above'} the start
              of Band {scoring.boundaryFlag.otherBand}. On the other side: <strong>{scoring.boundaryFlag.otherRoute}</strong>.
            </p>
            <p className="text-[11px] text-text-faint mt-1">Measured to where Band {scoring.boundaryFlag.otherBand} begins, not to the edge of the current band.</p>
            {canWrite && (
              <Button size="sm" variant="primary" className="mt-2.5" onClick={handleAcknowledgeBoundary}>
                Acknowledge and continue
              </Button>
            )}
          </div>
        </Card>
      )}

      {/* Composite table - each row is a category; the two right-hand columns are that
          category's contribution to composite inherent and composite residual, and the total
          row's cells are visibly the sum of those columns. This table is the composite
          derivation now, not a summary that a separate formula repeats. */}
      <Card material="flat" className="p-5 flex flex-col gap-4">
        <span className="text-sm font-bold text-text-primary">Per-category breakdown</span>
        <div className="grid grid-cols-[1.6fr_1fr] gap-6">
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="bg-surface-tertiary text-[10.5px] uppercase tracking-wide text-text-muted text-left">
                  <th className="px-3 py-2">Category</th>
                  <th className="px-3 py-2">Likelihood</th>
                  <th className="px-3 py-2">Impact</th>
                  <th className="px-3 py-2">Inherent</th>
                  <th className="px-3 py-2">Category CE</th>
                  <th className="px-3 py-2">Retention</th>
                  <th className="px-3 py-2">Residual</th>
                  <th className="px-3 py-2">Weight</th>
                  <th className="px-3 py-2">Inherent &times; weight</th>
                  <th className="px-3 py-2">Residual &times; weight</th>
                  <th className="px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {scoring.categories.map((c) => (
                  <CompositeRow key={c.category} c={c} onOpen={() => openDrawer(c.category)} />
                ))}
                <tr className="border-t-2 border-border-strong bg-surface-tertiary">
                  <td colSpan={8} className="px-3 py-2.5 font-bold text-text-primary text-right">
                    Composite
                  </td>
                  <td className="px-3 py-2.5 font-mono font-bold text-text-primary whitespace-nowrap">{scoring.compositeInherent}</td>
                  <td className="px-3 py-2.5 font-mono font-bold text-text-primary whitespace-nowrap">{scoring.compositeResidual}</td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
          <RiskMatrix categories={scoring.categories} />
        </div>
      </Card>

      <Drawer
        open={!!drawerCat}
        onClose={closeDrawer}
        title={drawerCat?.category}
        subtitle={drawerCat ? `Residual ${drawerCat.residual} - contributes ${(drawerCat.residual * drawerCat.weight).toFixed(2)} to the composite` : undefined}
      >
        {drawerCat && (
          <CategoryDetail
            c={drawerCat}
            model={anchors}
            controls={controls}
            missingFromInventory={Math.max(
              0,
              inventory.filter((i) => i.category === drawerCat.category && i.status === 'active').length - controls.filter((ctl) => ctl.category === drawerCat.category).length,
            )}
            rawWeight={anchors.categoryWeights[drawerCat.category] ?? 0}
            rawWeightSum={rawWeightSum}
            jurisdictionFlag={jurisdictionFlag}
            onNavigateToJurisdictionList={() => navigate('/analyst/config#jurisdiction')}
            canWrite={canWrite}
            editingFactor={editingFactor}
            onStartEditFactor={setEditingFactor}
            onCancelEditFactor={() => setEditingFactor(null)}
            onSaveFactor={(factor, v, r) => handleSaveFactor(drawerCat.category, factor, v, r)}
            onRevertFactor={(factor) => handleRevertFactor(drawerCat.category, factor)}
          />
        )}
      </Drawer>

      {/* Controls */}
      <Card material="flat" className="p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Mitigating controls</span>
          <div className="flex items-center gap-3">
            <span className="text-[12px] text-text-muted">
              {confirmedCount} of {controls.length} confirmed
            </span>
            {canWrite && (
              <Button size="sm" variant="secondary" icon={<Icon name="plus" size={14} />} onClick={() => setAddControlOpen(true)}>
                Add existing control
              </Button>
            )}
          </div>
        </div>
        {Array.from(byCategory.entries()).map(([category, ctls]) => (
          <div key={category} className="flex flex-col gap-2.5">
            <span className="text-[12px] font-semibold text-text-muted">{category}</span>
            {ctls.map((ctl) => (
              <ControlCard key={ctl.id} ctl={ctl} canWrite={canWrite} onConfirm={(v) => handleConfirm(ctl, v)} onOverride={(v, r) => handleOverride(ctl, v, r)} />
            ))}
          </div>
        ))}
        {!allConfirmed && <p className="text-[11.5px] text-warning">All controls must be confirmed or overridden before advancing to Recommendation.</p>}
      </Card>

      {/* Planned controls */}
      <Card material="flat" className="p-5 flex flex-col gap-3 border-2 border-dashed border-border-strong">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Planned controls</span>
          {canWrite && (
            <Button size="sm" variant="secondary" icon={<Icon name="plus" size={14} />} onClick={() => setPlannedOpen(true)}>
              Add planned control
            </Button>
          )}
        </div>
        <div className="flex items-start gap-2.5 bg-surface-tertiary rounded-md px-3 py-2.5">
          <Icon name="info" size={14} className="text-text-muted shrink-0 mt-0.5" />
          <p className="text-[12px] text-text-secondary">Planned controls do not reduce residual risk - a control that does not exist cannot mitigate anything.</p>
        </div>
        {plannedControls.length === 0 ? (
          <p className="text-[13px] text-text-faint">No planned controls yet.</p>
        ) : (
          plannedControls.map((p) => (
            <div key={p.id} className="border border-border-strong rounded-lg p-3.5 flex flex-col gap-1.5">
              <span className="text-[13px] font-semibold text-text-primary">{p.description}</span>
              <span className="text-[11.5px] text-text-muted">
                Owner: {p.owner} - Target: {p.targetDate} - Addresses: {p.addressesCategory}
              </span>
              <span className="text-[12px] text-teal font-medium">Hypothetical residual if implemented: {p.hypotheticalResidual} (labelled hypothetical, not applied)</span>
            </div>
          ))
        )}
      </Card>

      <AddControlModal
        open={addControlOpen}
        onClose={() => setAddControlOpen(false)}
        inventory={inventory}
        onAdd={async (id) => {
          await addExistingControlToCase(caseId, id);
          toast('Control added to this case.', 'success');
          setAddControlOpen(false);
          reload();
        }}
        onPropose={async (input) => {
          await proposeAndAddNewControl(caseId, input);
          toast(`"${input.name}" added to this case and joined the active Control Inventory immediately.`, 'success');
          setAddControlOpen(false);
          reload();
        }}
      />

      <PlannedControlModal
        open={plannedOpen}
        onClose={() => setPlannedOpen(false)}
        onAdd={async (input) => {
          await addPlannedControl(caseId, input);
          setPlannedOpen(false);
          reload();
        }}
      />

      <Modal open={!!bandChangeInfo} onClose={() => setBandChangeInfo(null)} title="Band changed" width={560}>
        {bandChangeInfo && (
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-2.5 bg-danger-bg border-2 border-danger rounded-lg px-4 py-3">
              <Icon name="warning" size={20} className="text-danger shrink-0" />
              <span className="text-[14px] font-bold text-danger">This override changed the case's band and route.</span>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="border border-border-strong rounded-lg p-3.5 flex flex-col gap-1">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Before</span>
                <ScoreBadge scoring={bandChangeInfo.before} />
              </div>
              <div className="border-2 border-danger rounded-lg p-3.5 flex flex-col gap-1">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">After</span>
                <ScoreBadge scoring={bandChangeInfo.after} />
              </div>
            </div>
            <p className="text-[13px] text-text-secondary">This is logged with the before and after composite residual, band, and route.</p>
            <div className="flex justify-end gap-2">
              <Button variant="primary" onClick={() => setBandChangeInfo(null)}>
                Close
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {canWrite && (
        <div className="flex justify-end">
          <Button size="sm" variant="secondary" onClick={onRequestClarification}>
            Raise clarification from here
          </Button>
        </div>
      )}
    </div>
  );
}

function AddControlModal({
  open,
  onClose,
  inventory,
  onAdd,
  onPropose,
}: {
  open: boolean;
  onClose: () => void;
  inventory: ControlInventoryItem[];
  onAdd: (id: string) => void;
  onPropose: (input: { name: string; category: CategoryScore['category']; weight: number; effectivenessAI: number }) => void;
}) {
  const [mode, setMode] = useState<'existing' | 'new'>('existing');
  const [selected, setSelected] = useState('');
  const [name, setName] = useState('');
  const [category, setCategory] = useState<CategoryScore['category']>('Delivery channel and third party');
  const [weight, setWeight] = useState(0.5);
  const [ce, setCe] = useState(5);

  useEffect(() => {
    if (open) {
      setMode('existing');
      setSelected('');
      setName('');
      setWeight(0.5);
      setCe(5);
    }
  }, [open]);

  return (
    <Modal open={open} onClose={onClose} title="Add a control" width={460}>
      <div className="flex flex-col gap-4">
        <div className="flex gap-2">
          <Button size="sm" variant={mode === 'existing' ? 'primary' : 'ghost'} onClick={() => setMode('existing')}>
            From inventory
          </Button>
          <Button size="sm" variant={mode === 'new' ? 'primary' : 'ghost'} onClick={() => setMode('new')}>
            Propose a new control
          </Button>
        </div>

        {mode === 'existing' ? (
          <>
            <p className="text-[13px] text-text-secondary">Applies to this case immediately.</p>
            <Select value={selected} onChange={(e) => setSelected(e.target.value)}>
              <option value="">Select a control...</option>
              {inventory.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.category})
                </option>
              ))}
            </Select>
          </>
        ) : (
          <>
            <p className="text-[13px] text-text-secondary">Joins the active Control Inventory immediately, sourced as Analyst-added, and applies to this case.</p>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Control name" />
            <div className="grid grid-cols-3 gap-2.5">
              <Select value={category} onChange={(e) => setCategory(e.target.value as CategoryScore['category'])} className="col-span-3">
                <option>Customer / entity</option>
                <option>Product and service</option>
                <option>Geography</option>
                <option>Delivery channel and third party</option>
              </Select>
              <div className="flex flex-col gap-1">
                <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Weight</label>
                <Input type="number" min={0} step={0.05} value={weight} onChange={(e) => setWeight(Number(e.target.value))} />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">CE (0-10)</label>
                <Input type="number" min={0} max={10} step={0.1} value={ce} onChange={(e) => setCe(Number(e.target.value))} />
              </div>
            </div>
          </>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {mode === 'existing' ? (
            <Button variant="primary" disabled={!selected} onClick={() => onAdd(selected)}>
              Add control
            </Button>
          ) : (
            <Button variant="primary" disabled={!name.trim()} onClick={() => onPropose({ name: name.trim(), category, weight, effectivenessAI: ce })}>
              Add control
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

function PlannedControlModal({ open, onClose, onAdd }: { open: boolean; onClose: () => void; onAdd: (input: { description: string; owner: string; targetDate: string; addressesCategory: CategoryScore['category'] }) => void }) {
  const [description, setDescription] = useState('');
  const [owner, setOwner] = useState('');
  const [targetDate, setTargetDate] = useState('');
  const [category, setCategory] = useState<CategoryScore['category']>('Delivery channel and third party');

  return (
    <Modal open={open} onClose={onClose} title="Add planned control" width={460}>
      <div className="flex flex-col gap-3">
        <Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is the control?" />
        <Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Owner" />
        <Input value={targetDate} onChange={(e) => setTargetDate(e.target.value)} placeholder="Target date (e.g. 2026-Q4)" />
        <Select value={category} onChange={(e) => setCategory(e.target.value as CategoryScore['category'])}>
          <option>Customer / entity</option>
          <option>Product and service</option>
          <option>Geography</option>
          <option>Delivery channel and third party</option>
        </Select>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!description.trim() || !owner.trim() || !targetDate.trim()} onClick={() => onAdd({ description, owner, targetDate, addressesCategory: category })}>
            Add
          </Button>
        </div>
      </div>
    </Modal>
  );
}
