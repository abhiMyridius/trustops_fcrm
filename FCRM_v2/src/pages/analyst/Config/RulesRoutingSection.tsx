import { useEffect, useState } from 'react';
import type { AutoTriageRule, BandDef, RoutingRules, ScoringModelVersion } from '@/types/analyst';
import {
  commitRoutingRulesChange,
  commitScoringModelChange,
  getRoutingRulesVersions,
  previewAutoTriageImpact,
  previewRoutingThresholdImpact,
  previewScoringModelImpact,
  validateRoutingThresholds,
  type AutoTriageImpact,
  type RoutingThresholdImpact,
  type ScoringModelImpact,
} from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Select, Textarea } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';
import { ScoringImpactModal } from './ScoringModelSection';

const SECTION_ID = 'routing';
const SECTION_LABEL = 'Rules & Routing';

type ThresholdFields = Pick<RoutingRules, 'boundaryProximityThreshold' | 'evidenceRecencyMonths' | 'nearingStalenessMonths' | 'slaTargetBusinessDays'>;

const THRESHOLD_FIELDS: { key: keyof ThresholdFields; label: string; help: string; step?: number }[] = [
  { key: 'boundaryProximityThreshold', label: 'Boundary proximity threshold', help: 'Composite residual within this distance of a band edge raises a boundary flag.', step: 0.1 },
  { key: 'evidenceRecencyMonths', label: 'Evidence recency window (months)', help: 'Control evidence older than this is flagged stale.' },
  { key: 'nearingStalenessMonths', label: 'Nearing-staleness window (months)', help: 'A control within this many months of the recency window shows a nearing-staleness caution. Must stay positive and below the recency window.' },
  { key: 'slaTargetBusinessDays', label: 'SLA target (business days)', help: 'Total elapsed vs. analyst-active dual clock target.' },
];

function bandsContiguous(bands: BandDef[]): string | null {
  const sorted = [...bands].sort((a, b) => a.band - b.band);
  for (const b of sorted) {
    if (b.min >= b.max) return `Band ${b.band}'s minimum (${b.min}) must be below its maximum (${b.max}).`;
  }
  if (sorted[0].min !== 0) return `Band ${sorted[0].band} must start at 0.0.`;
  if (sorted[sorted.length - 1].max !== 25) return `Band ${sorted[sorted.length - 1].band} must end at 25.0.`;
  for (let i = 0; i < sorted.length - 1; i++) {
    if (Math.round((sorted[i + 1].min - sorted[i].max) * 10) / 10 !== 0.1) {
      return `Gap or overlap between Band ${sorted[i].band} (ends ${sorted[i].max}) and Band ${sorted[i + 1].band} (starts ${sorted[i + 1].min}) - they must be contiguous (0.1 apart).`;
    }
  }
  return null;
}

// -----------------------------------------------------------------------
// Threshold/auto-triage impact preview - a second, lighter preview alongside the shared
// ScoringImpactModal (reused as-is for band changes, since those recompute scores the same way a
// category weight edit does). None of these change a computed score; they change which existing
// facts read as a flag going forward, so each number here is a live count, not a recomputation.
// -----------------------------------------------------------------------
function RoutingImpactModal({
  open,
  impact,
  note,
  onNoteChange,
  onCancel,
  onConfirm,
  confirming,
}: {
  open: boolean;
  impact: { threshold: RoutingThresholdImpact; autoTriage: AutoTriageImpact } | null;
  note: string;
  onNoteChange: (v: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  const threshold = impact?.threshold ?? null;
  const autoTriage = impact?.autoTriage ?? null;
  const nothingChanged =
    !!threshold && !!autoTriage && !threshold.evidenceOrNearingChanged && !threshold.boundaryProximityChanged && !threshold.slaChanged && !autoTriage.autoTriageChanged;

  return (
    <Modal open={open} onClose={onCancel} title="Impact preview - review before publishing" width={620}>
      <div className="flex flex-col gap-3">
        {threshold?.evidenceOrNearingChanged && (
          <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
            <span className="text-text-secondary">Controls that would change staleness state</span>
            <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{threshold.controlsChangingStalenessState}</span>
          </div>
        )}
        {threshold?.boundaryProximityChanged && (
          <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
            <span className="text-text-secondary">Last 50 cases that would raise a boundary flag under the new threshold</span>
            <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{threshold.last50CasesWouldFlag}</span>
          </div>
        )}
        {threshold?.slaChanged && (
          <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
            <span className="text-text-secondary">In-flight cases that would change SLA at-risk state</span>
            <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{threshold.inFlightCasesChangingAtRiskState}</span>
          </div>
        )}
        {autoTriage?.autoTriageChanged && (
          <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
            <span className="text-text-secondary">Currently open Band 3/4 cases the new auto-triage rule would apply to</span>
            <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{autoTriage.openBand34Cases}</span>
          </div>
        )}
        {nothingChanged && (
          <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3.5 py-3">
            <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
            <p className="text-[13px] text-[#133338]">No live consequence detected for this change.</p>
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
    </Modal>
  );
}

export function RulesRoutingSection({
  activeModel,
  onNavigateToScoring,
  onScoringPublished,
}: {
  activeModel: ScoringModelVersion | null;
  onNavigateToScoring: () => void;
  /** Band changes here publish a new scoring model version - the Scoring Model page holds its own
   * copy of that resource and needs to know to re-fetch, same as this page does when it publishes. */
  onScoringPublished: () => void;
}) {
  const { changes, addChange, clearSection, registerActions } = useWorkingSet();
  const toast = useToast();

  const [versions, setVersions] = useState<Awaited<ReturnType<typeof getRoutingRulesVersions>> | null>(null);
  const [initial, setInitial] = useState<RoutingRules | null>(null);

  const [bands, setBands] = useState<BandDef[]>([]);
  const [initialBands, setInitialBands] = useState<BandDef[]>([]);
  const [bandsEditing, setBandsEditing] = useState(false);
  const [bandsBuffer, setBandsBuffer] = useState<BandDef[]>([]);
  const [bandsError, setBandsError] = useState<string | null>(null);

  const [thresholds, setThresholds] = useState<ThresholdFields | null>(null);
  const [thresholdsEditing, setThresholdsEditing] = useState(false);
  const [thresholdsBuffer, setThresholdsBuffer] = useState<ThresholdFields | null>(null);
  const [thresholdsError, setThresholdsError] = useState<string | null>(null);

  const [autoTriage, setAutoTriage] = useState<AutoTriageRule | null>(null);
  const [autoTriageEditing, setAutoTriageEditing] = useState(false);
  const [autoTriageBuffer, setAutoTriageBuffer] = useState<AutoTriageRule | null>(null);

  const [viewingVersion, setViewingVersion] = useState<number | null>(null);

  const [scoringImpact, setScoringImpact] = useState<ScoringModelImpact | null>(null);
  const [scoringImpactOpen, setScoringImpactOpen] = useState(false);
  const [rescoreIds, setRescoreIds] = useState<Set<string>>(new Set());
  const [routingImpact, setRoutingImpact] = useState<{ threshold: RoutingThresholdImpact; autoTriage: AutoTriageImpact } | null>(null);
  const [routingImpactOpen, setRoutingImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [publishing, setPublishing] = useState(false);

  async function reload() {
    const vers = await getRoutingRulesVersions();
    setVersions(vers);
    const active = vers[vers.length - 1].rules;
    setInitial(active);
    setThresholds({ boundaryProximityThreshold: active.boundaryProximityThreshold, evidenceRecencyMonths: active.evidenceRecencyMonths, nearingStalenessMonths: active.nearingStalenessMonths, slaTargetBusinessDays: active.slaTargetBusinessDays });
    setAutoTriage(active.autoTriageRule);
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The band table lives on a sibling resource (the scoring model) that the Scoring Model page
  // can also publish - re-sync from the prop whenever that happens, unless a band edit is
  // pending here, so an in-progress change is never silently discarded by a sibling publish.
  useEffect(() => {
    if (!activeModel) return;
    if (!changes.some((c) => c.sectionId === SECTION_ID && c.label === 'Updated band table')) {
      setBands(activeModel.bands);
      setInitialBands(activeModel.bands);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModel?.version]);

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }

  function routingCandidate(): RoutingRules | null {
    if (!thresholds || !autoTriage) return null;
    return { ...thresholds, autoTriageRule: autoTriage };
  }

  const candidate = routingCandidate();
  const bandsDirty = JSON.stringify(bands) !== JSON.stringify(initialBands);
  const routingDirty = !!candidate && !!initial && JSON.stringify(candidate) !== JSON.stringify(initial);
  const myChanges = changes.filter((c) => c.sectionId === SECTION_ID);

  // -------------------- Band table --------------------
  function startEditBands() {
    setBandsBuffer(bands);
    setBandsError(null);
    setBandsEditing(true);
  }
  function updateBandBuffer(band: number, patch: Partial<BandDef>) {
    setBandsBuffer((bs) => bs.map((b) => (b.band === band ? { ...b, ...patch } : b)));
  }
  function confirmBands() {
    const err = bandsContiguous(bandsBuffer);
    if (err) {
      setBandsError(err);
      return;
    }
    const prev = bands;
    setBands(bandsBuffer);
    setBandsEditing(false);
    setBandsError(null);
    if (JSON.stringify(prev) !== JSON.stringify(bandsBuffer)) log('Updated band table', () => setBands(prev));
  }

  // -------------------- Thresholds and windows --------------------
  function startEditThresholds() {
    if (!thresholds) return;
    setThresholdsBuffer(thresholds);
    setThresholdsError(null);
    setThresholdsEditing(true);
  }
  function confirmThresholds() {
    if (!thresholdsBuffer) return;
    const err = validateRoutingThresholds(thresholdsBuffer);
    if (err) {
      setThresholdsError(err);
      return;
    }
    const prev = thresholds;
    setThresholds(thresholdsBuffer);
    setThresholdsEditing(false);
    setThresholdsError(null);
    if (JSON.stringify(prev) !== JSON.stringify(thresholdsBuffer)) log('Updated thresholds and windows', () => setThresholds(prev));
  }

  // -------------------- Auto-triage rule --------------------
  function startEditAutoTriage() {
    if (!autoTriage) return;
    setAutoTriageBuffer(autoTriage);
    setAutoTriageEditing(true);
  }
  function confirmAutoTriage() {
    if (!autoTriageBuffer) return;
    const prev = autoTriage;
    setAutoTriage(autoTriageBuffer);
    setAutoTriageEditing(false);
    if (JSON.stringify(prev) !== JSON.stringify(autoTriageBuffer)) log('Updated auto-triage rule', () => setAutoTriage(prev));
  }

  // -------------------- Publish / discard (registered into the shared working set) --------------------
  async function reviewImpact() {
    if (bandsDirty && activeModel) {
      const err = bandsContiguous(bands);
      if (err) {
        toast(err, 'danger');
        return;
      }
      const result = await previewScoringModelImpact({ categoryWeights: activeModel.categoryWeights, likelihoodAnchors: activeModel.likelihoodAnchors, impactAnchors: activeModel.impactAnchors, bands });
      setScoringImpact(result);
      setRescoreIds(new Set());
      setScoringImpactOpen(true);
      return;
    }
    if (routingDirty) await openRoutingImpact();
  }

  async function openRoutingImpact() {
    if (!candidate) return;
    const err = validateRoutingThresholds(candidate);
    if (err) {
      toast(err, 'danger');
      return;
    }
    const [threshold, autoTriage] = await Promise.all([previewRoutingThresholdImpact(candidate), previewAutoTriageImpact(candidate.autoTriageRule)]);
    setRoutingImpact({ threshold, autoTriage });
    setRoutingImpactOpen(true);
  }

  async function confirmBandsPublish() {
    if (!activeModel) return;
    setPublishing(true);
    const { version } = await commitScoringModelChange(
      { categoryWeights: activeModel.categoryWeights, likelihoodAnchors: activeModel.likelihoodAnchors, impactAnchors: activeModel.impactAnchors, bands },
      note.trim() || 'Band boundary update.',
      undefined,
      Array.from(rescoreIds),
    );
    setPublishing(false);
    setScoringImpactOpen(false);
    setInitialBands(bands);
    toast(`Band table updated - published as scoring model ${version}.`, 'success');
    onScoringPublished();
    if (routingDirty) await openRoutingImpact();
    else finishPublish();
  }

  async function confirmRoutingPublish() {
    if (!candidate) return;
    setPublishing(true);
    await commitRoutingRulesChange(candidate, note.trim() || 'Rules & Routing update.');
    setPublishing(false);
    setRoutingImpactOpen(false);
    finishPublish();
  }

  function finishPublish() {
    setNote('');
    clearSection(SECTION_ID);
    toast('Rules & Routing updated.', 'success');
    reload();
  }

  function handleDiscard() {
    setBands(initialBands);
    setBandsEditing(false);
    if (initial) {
      setThresholds({ boundaryProximityThreshold: initial.boundaryProximityThreshold, evidenceRecencyMonths: initial.evidenceRecencyMonths, nearingStalenessMonths: initial.nearingStalenessMonths, slaTargetBusinessDays: initial.slaTargetBusinessDays });
      setAutoTriage(initial.autoTriageRule);
    }
    setThresholdsEditing(false);
    setAutoTriageEditing(false);
    clearSection(SECTION_ID);
  }

  useEffect(() => {
    registerActions(SECTION_ID, { publish: reviewImpact, discard: handleDiscard });
  });

  if (!versions || !thresholds || !autoTriage || !activeModel) return <Skeleton className="h-96 rounded-md" />;

  const sortedVersions = versions.slice().sort((a, b) => b.version - a.version);
  const activeVersionNumber = versions[versions.length - 1].version;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-relaxed">
        <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
        <p>
          <strong>Non-retroactive.</strong> A case already assessed keeps the band, route and flags decided by the rules in force at the time, stamped to that scoring model and routing rules
          version - a change here never moves it. Cases pending an analysis run pick up whatever is active when that run starts. An in-flight case already scored continues on its version unless
          explicitly re-scored - offered per case when a band boundary change publishes, never migrated silently. Closed cases are never re-evaluated, under any circumstances.
        </p>
      </div>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Band table</span>
          {!bandsEditing && (
            <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={startEditBands}>
              Edit
            </Button>
          )}
        </div>
        <p className="text-[12.5px] text-text-muted -mt-1">
          Decides whether a case reaches committee at all. Ranges must stay contiguous and cover 0.0 to 25.0 with no gaps. Category weights and anchors live in{' '}
          <button onClick={onNavigateToScoring} className="underline text-carbon-moss hover:text-carbon-moss-hover cursor-pointer font-semibold">
            Scoring Model
          </button>{' '}
          - an analyst changing bands needs the weights in view and vice versa. Publishes as a new scoring model version, since it recomputes scores.
        </p>
        {!bandsEditing ? (
          <div className="grid grid-cols-1 gap-1.5">
            {bands.map((b) => (
              <div key={b.band} className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
                <span className="font-mono font-semibold text-text-primary w-16 shrink-0">Band {b.band}</span>
                <span className="font-mono text-text-faint w-28 shrink-0">
                  {b.min.toFixed(1)}-{b.max.toFixed(1)}
                </span>
                <span className="text-text-secondary">{b.route}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-[70px_100px_100px_1fr] gap-2.5 px-1 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">
              <span>Band</span>
              <span>Min</span>
              <span>Max</span>
              <span>Route</span>
            </div>
            {bandsBuffer
              .slice()
              .sort((a, b) => a.band - b.band)
              .map((b) => (
                <div key={b.band} className="grid grid-cols-[70px_100px_100px_1fr] gap-2.5 items-center">
                  <span className="text-[13px] font-mono font-semibold text-text-primary">Band {b.band}</span>
                  <Input type="number" step={0.1} value={b.min} onChange={(e) => updateBandBuffer(b.band, { min: Number(e.target.value) })} className="py-1.5 text-[12.5px]" />
                  <Input type="number" step={0.1} value={b.max} onChange={(e) => updateBandBuffer(b.band, { max: Number(e.target.value) })} className="py-1.5 text-[12.5px]" />
                  <Input value={b.route} onChange={(e) => updateBandBuffer(b.band, { route: e.target.value, routeShort: e.target.value })} className="py-1.5 text-[12.5px]" />
                </div>
              ))}
            {bandsError && (
              <div className="flex items-start gap-2 bg-danger-bg rounded-md px-3 py-2.5 text-[12.5px] text-danger">
                <Icon name="warning" size={14} className="shrink-0 mt-0.5" />
                {bandsError}
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
              <Button size="sm" variant="ghost" onClick={() => setBandsEditing(false)}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" onClick={confirmBands}>
                Confirm
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Thresholds and windows</span>
          {!thresholdsEditing && (
            <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={startEditThresholds}>
              Edit
            </Button>
          )}
        </div>
        <div className="grid grid-cols-2 gap-5">
          {THRESHOLD_FIELDS.map((f) => (
            <div key={f.key} className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">{f.label}</span>
              {thresholdsEditing && thresholdsBuffer ? (
                <Input
                  type="number"
                  step={f.step}
                  value={thresholdsBuffer[f.key]}
                  onChange={(e) => setThresholdsBuffer((t) => (t ? { ...t, [f.key]: Number(e.target.value) } : t))}
                  className="w-28"
                />
              ) : (
                <span className="font-mono text-[14px] font-semibold text-text-primary">{thresholds[f.key]}</span>
              )}
              <span className="text-[12px] text-text-muted">{f.help}</span>
            </div>
          ))}
        </div>
        {thresholdsEditing && thresholdsError && (
          <div className="flex items-start gap-2 bg-danger-bg rounded-md px-3 py-2.5 text-[12.5px] text-danger">
            <Icon name="warning" size={14} className="shrink-0 mt-0.5" />
            {thresholdsError}
          </div>
        )}
        {thresholdsEditing && (
          <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setThresholdsEditing(false);
                setThresholdsError(null);
              }}
            >
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmThresholds}>
              Confirm
            </Button>
          </div>
        )}
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Auto-triage rule</span>
          {!autoTriageEditing && (
            <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={startEditAutoTriage}>
              Edit
            </Button>
          )}
        </div>
        <div className="grid grid-cols-3 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Strategy</label>
            {autoTriageEditing && autoTriageBuffer ? (
              <Select value={autoTriageBuffer.strategy} onChange={(e) => setAutoTriageBuffer((a) => (a ? { ...a, strategy: e.target.value as AutoTriageRule['strategy'] } : a))}>
                <option value="round-robin">Round-robin</option>
                <option value="least-active-cases">Least active cases</option>
                <option value="manual">Manual</option>
              </Select>
            ) : (
              <span className="text-[13.5px] font-semibold text-text-primary capitalize">{autoTriage.strategy.replace(/-/g, ' ')}</span>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Lookback window (days)</label>
            {autoTriageEditing && autoTriageBuffer ? (
              <Input type="number" value={autoTriageBuffer.lookbackDays} onChange={(e) => setAutoTriageBuffer((a) => (a ? { ...a, lookbackDays: Number(e.target.value) } : a))} />
            ) : (
              <span className="text-[13.5px] font-semibold text-text-primary">{autoTriage.lookbackDays}</span>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Band 3/4 weighting factor</label>
            {autoTriageEditing && autoTriageBuffer ? (
              <Input type="number" step={0.1} value={autoTriageBuffer.band34WeightingFactor} onChange={(e) => setAutoTriageBuffer((a) => (a ? { ...a, band34WeightingFactor: Number(e.target.value) } : a))} />
            ) : (
              <span className="text-[13.5px] font-semibold text-text-primary">{autoTriage.band34WeightingFactor}</span>
            )}
          </div>
        </div>
        <p className="text-[12px] text-text-muted">Lighter weighting toward analysts already holding an open Band 3/4 case, over this lookback window.</p>
        {autoTriageEditing && (
          <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
            <Button size="sm" variant="ghost" onClick={() => setAutoTriageEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmAutoTriage}>
              Confirm
            </Button>
          </div>
        )}
        <div className="flex items-start gap-2.5 bg-[#FBF3E4] rounded-md px-3.5 py-3">
          <Icon name="warning" size={14} className="text-[#B98A1E] shrink-0 mt-0.5" />
          <p className="text-[12px] text-[#5c4415] leading-relaxed">
            <strong>Known gap, not required for this build:</strong> auto-triage distributes without any notion of analyst availability or capacity ceiling. With no lead role built and no
            reassignment path, a case assigned to an analyst who is on leave or already at capacity sits until someone notices.
          </p>
        </div>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Version history</span>
        <div className="flex flex-col divide-y divide-border-hairline">
          {sortedVersions.map((v) => (
            <div key={v.version} className="py-2.5 flex flex-col gap-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-[12.5px] font-semibold text-text-primary">v{v.version}</span>
                {v.version === activeVersionNumber && <Badge tone="success">Active</Badge>}
                <span className="text-[11.5px] text-text-faint">{formatDate(v.publishedAt)}</span>
                <span className="text-[11.5px] text-text-faint">{v.changedBy}</span>
                <button
                  onClick={() => setViewingVersion((cur) => (cur === v.version ? null : v.version))}
                  className="ml-auto text-[11.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer"
                >
                  {viewingVersion === v.version ? 'Hide rules' : 'View rules'}
                </button>
              </div>
              <p className="text-[12.5px] text-text-secondary leading-relaxed">{v.note}</p>
              {viewingVersion === v.version && (
                <div className="flex flex-col gap-1 mt-1 p-3 rounded-md bg-surface-tertiary text-[12px]">
                  <div className="flex justify-between">
                    <span className="text-text-faint">Boundary proximity threshold</span>
                    <span className="font-mono font-semibold text-text-primary">{v.rules.boundaryProximityThreshold}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-faint">Evidence recency window</span>
                    <span className="font-mono font-semibold text-text-primary">{v.rules.evidenceRecencyMonths} months</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-faint">Nearing-staleness window</span>
                    <span className="font-mono font-semibold text-text-primary">{v.rules.nearingStalenessMonths} months</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-faint">SLA target</span>
                    <span className="font-mono font-semibold text-text-primary">{v.rules.slaTargetBusinessDays} business days</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-faint">Auto-triage strategy</span>
                    <span className="font-mono font-semibold text-text-primary capitalize">{v.rules.autoTriageRule.strategy.replace(/-/g, ' ')}</span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      <ScoringImpactModal
        open={scoringImpactOpen}
        impact={scoringImpact}
        pendingChangeCount={myChanges.length}
        note={note}
        onNoteChange={setNote}
        rescoreIds={rescoreIds}
        onToggleRescore={(caseId) =>
          setRescoreIds((s) => {
            const next = new Set(s);
            if (next.has(caseId)) next.delete(caseId);
            else next.add(caseId);
            return next;
          })
        }
        onCancel={() => setScoringImpactOpen(false)}
        onConfirm={confirmBandsPublish}
        confirming={publishing}
      />

      <RoutingImpactModal
        open={routingImpactOpen}
        impact={routingImpact}
        note={note}
        onNoteChange={setNote}
        onCancel={() => setRoutingImpactOpen(false)}
        onConfirm={confirmRoutingPublish}
        confirming={publishing}
      />
    </div>
  );
}
