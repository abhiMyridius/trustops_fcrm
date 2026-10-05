import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { AnalystAgentConfig, AnalystAgentConfigSnapshot, AnalystAgentName } from '@/types/analyst';
import type { ModelInfo } from '@/types';
import {
  commitAgentConfigChange,
  getAgentOverrideRates,
  getAnalystAgentConfigVersions,
  getAnalystModelReference,
  previewAgentConfigImpact,
  type AgentConfigImpact,
} from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Select, Textarea } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';

const SECTION_ID = 'agents';
const SECTION_LABEL = 'AI Agents';

const AGENT_DESCRIPTIONS: Record<AnalystAgentName, string> = {
  'Policy Mapping': "Retrieves applicable policy clauses for the case's confirmed categories and shows each verbatim with its source. Never paraphrases policy in place of the clause.",
  'Risk Drafting': 'Pre-populates likelihood and impact factor values from the submission and drafts the rationale narrative per risk category. Computes no composite residual.',
  'Control Matching': 'Proposes mitigating controls from the control inventory with a suggested effectiveness value and the evidence behind it.',
  Precedent: 'Surfaces comparable prior cases and the clauses cited on them.',
  Clarification: 'Drafts batched clarification questions from detected gaps, for the analyst to edit and send.',
};

const EFFORT_ORDER: Record<AnalystAgentConfig['effort'], number> = { Low: 0, Standard: 1, High: 2 };

function overrideTone(pct: number): 'success' | 'default' | 'warning' | 'danger' {
  if (pct <= 3) return 'danger';
  if (pct <= 15) return 'success';
  if (pct <= 25) return 'default';
  return 'warning';
}

function ModelReferenceTable({ models }: { models: ModelInfo[] }) {
  return (
    <Card material="flat" className="overflow-hidden">
      <div className="grid grid-cols-[1fr_1fr_100px_70px_2fr_120px] gap-3 px-5 py-2.5 bg-[#f0efed] text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        <span>Model</span>
        <span>Provider</span>
        <span>Speed</span>
        <span>Cost</span>
        <span>Strengths</span>
        <span>Context window</span>
      </div>
      <div className="flex flex-col divide-y divide-border-hairline">
        {models.map((m) => (
          <div key={m.id} className="grid grid-cols-[1fr_1fr_100px_70px_2fr_120px] gap-3 px-5 py-3 items-center text-[12.5px]">
            <span className="text-text-primary font-semibold">{m.name}</span>
            <span className="text-text-secondary">{m.provider}</span>
            <span className="text-text-secondary">{m.relativeSpeed}</span>
            <span className="text-text-secondary font-mono">{m.relativeCost}</span>
            <span className="text-text-secondary">{m.strengths}</span>
            <span className="text-text-faint">{m.contextWindow}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function AgentCard({
  config,
  draft,
  models,
  overrideRatePct,
  editing,
  buffer,
  onStartEdit,
  onCancel,
  onBufferChange,
  onConfirm,
}: {
  config: AnalystAgentConfig;
  draft: AnalystAgentConfig;
  models: ModelInfo[];
  overrideRatePct: number | undefined;
  editing: boolean;
  buffer: AnalystAgentConfig;
  onStartEdit: () => void;
  onCancel: () => void;
  onBufferChange: (patch: Partial<AnalystAgentConfig>) => void;
  onConfirm: () => void;
}) {
  const navigate = useNavigate();
  const modelName = models.find((m) => m.id === draft.modelId)?.name ?? draft.modelId;

  return (
    <Card material="flat" className="p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-lg bg-carbon-moss-deep flex items-center justify-center shrink-0">
            <Icon name="sparkles" size={18} className="text-quantum-leaf" />
          </div>
          <span className="text-[14px] font-semibold text-text-primary">{config.agent} Agent</span>
        </div>
        {!editing && (
          <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={onStartEdit}>
            Edit
          </Button>
        )}
      </div>

      <p className="text-[12.5px] text-text-secondary leading-relaxed -mt-1">{AGENT_DESCRIPTIONS[config.agent]}</p>

      {config.agent === 'Policy Mapping' && (
        <div className="flex items-start gap-2 bg-[#F4F8F7] rounded-md px-3 py-2.5 -mt-1">
          <Icon name="info" size={13} className="text-teal shrink-0 mt-0.5" />
          <p className="text-[11.5px] text-[#133338] leading-relaxed">
            Configured higher than the rest on purpose: policy retrieval is citation-bound with no generation, and carries the highest consequence of any agent here if the model fabricates a
            clause. The stronger model and higher effort buy accuracy where the cost of getting it wrong is greatest.
          </p>
        </div>
      )}

      {!editing ? (
        <div className="grid grid-cols-2 gap-3 pt-3 border-t border-border-hairline">
          <div className="flex flex-col gap-0.5">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Model</span>
            <span className="text-[13.5px] font-semibold text-text-primary">{modelName}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Effort</span>
            <span className="text-[13.5px] font-semibold text-text-primary">{draft.effort}</span>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3 pt-3 border-t border-border-hairline">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint px-0.5">Model</label>
              <Select value={buffer.modelId} onChange={(e) => onBufferChange({ modelId: e.target.value })} className="text-[13px]">
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint px-0.5">Effort</label>
              <Select value={buffer.effort} onChange={(e) => onBufferChange({ effort: e.target.value as AnalystAgentConfig['effort'] })} className="text-[13px]">
                <option value="Low">Low</option>
                <option value="Standard">Standard</option>
                <option value="High">High</option>
              </Select>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={onConfirm}>
              Confirm
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between pt-3 border-t border-border-hairline">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Override rate, last 6 months</span>
          {overrideRatePct === undefined ? (
            <span className="text-[12px] text-text-faint">-</span>
          ) : (
            <Badge tone={overrideTone(overrideRatePct)}>{overrideRatePct}%</Badge>
          )}
        </div>
        <button onClick={() => navigate('/analyst#ai-quality')} className="text-[11.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer">
          View trend on Dashboard
        </button>
      </div>
    </Card>
  );
}

function AgentConfigImpactModal({
  open,
  impact,
  note,
  onNoteChange,
  onCancel,
  onConfirm,
  confirming,
}: {
  open: boolean;
  impact: AgentConfigImpact | null;
  note: string;
  onNoteChange: (v: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  return (
    <Modal open={open} onClose={onCancel} title="Impact preview - review before publishing" width={620}>
      {impact && (
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
            <span className="text-text-secondary">Cases already run - unaffected, stay on the agent config version that ran them</span>
            <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{impact.alreadyRunCount}</span>
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Cases pending an analysis run - will use {impact.changedAgents.join(', ')}'s new setting</span>
            {impact.pendingAnalysisRun.length === 0 ? (
              <p className="text-[13px] text-teal font-medium">None.</p>
            ) : (
              <div className="flex flex-col gap-1 max-h-[160px] overflow-y-auto">
                {impact.pendingAnalysisRun.map((c) => (
                  <div key={c.caseId} className="text-[12.5px] text-text-secondary px-3 py-1.5 rounded-md bg-surface-tertiary">
                    {c.caseId} - {c.title}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3.5 py-3">
            <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
            <p className="text-[12px] text-[#133338] leading-relaxed">
              Where a rerun is later requested on an already-run case, it uses whatever settings are current at that time, not the ones in force at first run - this is the one place old and new
              settings can mix on a single case.
            </p>
          </div>

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

export function AIAgentsSection() {
  const { addChange, clearSection, registerActions } = useWorkingSet();
  const toast = useToast();

  const [versions, setVersions] = useState<AnalystAgentConfigSnapshot[] | null>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [overrideRates, setOverrideRates] = useState<Record<string, number> | null>(null);
  const [initial, setInitial] = useState<AnalystAgentConfig[] | null>(null);
  const [draft, setDraft] = useState<AnalystAgentConfig[]>([]);

  const [editingAgent, setEditingAgent] = useState<AnalystAgentName | null>(null);
  const [buffer, setBuffer] = useState<AnalystAgentConfig | null>(null);
  const [tierConfirmOpen, setTierConfirmOpen] = useState(false);

  const [impact, setImpact] = useState<AgentConfigImpact | null>(null);
  const [impactOpen, setImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);

  async function reload() {
    const vers = await getAnalystAgentConfigVersions();
    setVersions(vers);
    const active = vers[vers.length - 1].configs;
    setInitial(active);
    setDraft(active);
  }

  useEffect(() => {
    reload();
    getAnalystModelReference().then(setModels);
    getAgentOverrideRates().then((rows) => setOverrideRates(Object.fromEntries(rows.map((r) => [r.agent, r.overrideRatePct]))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }

  function startEdit(agent: AnalystAgentName) {
    setBuffer(draft.find((c) => c.agent === agent) ?? null);
    setEditingAgent(agent);
  }

  function applyBuffer() {
    if (!buffer) return;
    const prev = draft;
    const target = prev.find((c) => c.agent === buffer.agent)!;
    setDraft((d) => d.map((c) => (c.agent === buffer.agent ? buffer : c)));
    setEditingAgent(null);
    setTierConfirmOpen(false);
    if (target.modelId !== buffer.modelId || target.effort !== buffer.effort) {
      log(`Updated ${buffer.agent} Agent configuration`, () => setDraft(prev));
    }
    setBuffer(null);
  }

  function handleConfirmClick() {
    if (!buffer) return;
    const current = draft.find((c) => c.agent === buffer.agent)!;
    if (current.modelId === buffer.modelId && current.effort === buffer.effort) {
      setEditingAgent(null);
      setBuffer(null);
      return;
    }
    if (EFFORT_ORDER[buffer.effort] !== EFFORT_ORDER[current.effort]) setTierConfirmOpen(true);
    else applyBuffer();
  }

  async function reviewImpact() {
    const result = await previewAgentConfigImpact(draft);
    setImpact(result);
    setImpactOpen(true);
  }

  async function confirmPublish() {
    setPublishing(true);
    const { version } = await commitAgentConfigChange(draft, note.trim() || 'Agent configuration update.');
    setPublishing(false);
    setImpactOpen(false);
    setNote('');
    clearSection(SECTION_ID);
    toast(`Agent configuration updated - published as v${version}.`, 'success');
    reload();
  }

  function handleDiscard() {
    if (initial) setDraft(initial);
    setEditingAgent(null);
    setBuffer(null);
    clearSection(SECTION_ID);
  }

  useEffect(() => {
    registerActions(SECTION_ID, { publish: reviewImpact, discard: handleDiscard });
  });

  if (!versions || !models || !overrideRates) return <Skeleton className="h-96 rounded-md" />;

  const sortedVersions = versions.slice().sort((a, b) => b.version - a.version);
  const activeVersionNumber = versions[versions.length - 1].version;
  const currentTarget = buffer ? draft.find((c) => c.agent === buffer.agent)! : null;
  const increasing = buffer && currentTarget ? EFFORT_ORDER[buffer.effort] > EFFORT_ORDER[currentTarget.effort] : false;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-relaxed">
        <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
        <p>
          These settings apply to every analyst on the team, not just you. Analyst-side agent settings affect assessment consistency across cases, so they are not per-analyst - one analyst
          changing a model here changes everyone's assessments. These are analyst-side agents only; Product Owners configure their own five agents separately.
        </p>
      </div>

      <ModelReferenceTable models={models} />

      <div className="flex items-start gap-2.5 bg-surface-tertiary rounded-md px-4 py-3 text-[12.5px] text-text-secondary leading-relaxed">
        <Icon name="sparkles" size={14} className="text-carbon-moss shrink-0 mt-0.5" />
        <p>
          <strong className="text-text-primary">What effort changes:</strong> how much reasoning the agent does before it answers. Low reasons the least - fastest and cheapest, best for narrow,
          low-ambiguity work. Standard is the default balance most agents run at. High reasons substantially more before responding - noticeably slower and more expensive per case, worth it
          where the source material is ambiguous or a wrong answer is costly, as with Policy Mapping below.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {draft.map((c) => (
          <AgentCard
            key={c.agent}
            config={c}
            draft={c}
            models={models}
            overrideRatePct={overrideRates[c.agent]}
            editing={editingAgent === c.agent}
            buffer={buffer && buffer.agent === c.agent ? buffer : c}
            onStartEdit={() => startEdit(c.agent)}
            onCancel={() => {
              setEditingAgent(null);
              setBuffer(null);
            }}
            onBufferChange={(patch) => setBuffer((b) => (b ? { ...b, ...patch } : b))}
            onConfirm={handleConfirmClick}
          />
        ))}
      </div>

      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug">
        <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
        Committee pack generation is not listed here, because it is deterministic - generated on demand from the structured assessment, no model or effort involved.
      </div>

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
                  {viewingVersion === v.version ? 'Hide configuration' : 'View configuration'}
                </button>
              </div>
              <p className="text-[12.5px] text-text-secondary leading-relaxed">{v.note}</p>
              {viewingVersion === v.version && (
                <div className="flex flex-col gap-1 mt-1 p-3 rounded-md bg-surface-tertiary text-[12px]">
                  {v.configs.map((c) => (
                    <div key={c.agent} className="flex justify-between">
                      <span className="text-text-faint">{c.agent}</span>
                      <span className="font-mono font-semibold text-text-primary">
                        {models.find((m) => m.id === c.modelId)?.name ?? c.modelId} - {c.effort}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      <Modal open={tierConfirmOpen} onClose={() => setTierConfirmOpen(false)} title={increasing ? 'Higher effort selected' : 'Lower effort selected'} width={440}>
        {buffer && currentTarget && (
          <div className="flex flex-col gap-4">
            <div className={`flex items-start gap-2.5 rounded-md px-3.5 py-3 ${increasing ? 'bg-danger-bg' : 'bg-verdant-1'}`}>
              <Icon name={increasing ? 'warning' : 'info'} size={16} className={increasing ? 'text-danger shrink-0 mt-0.5' : 'text-teal shrink-0 mt-0.5'} />
              <p className={`text-[13px] leading-relaxed ${increasing ? 'text-danger' : 'text-[#133338]'}`}>
                {increasing ? (
                  <>
                    Moving the <strong>{buffer.agent} Agent</strong> from {currentTarget.effort} to {buffer.effort} effort will make it slower and more expensive to run, on every case going
                    forward, not just this one. Use higher effort where the extra thoroughness is worth the cost.
                  </>
                ) : (
                  <>
                    Moving the <strong>{buffer.agent} Agent</strong> from {currentTarget.effort} to {buffer.effort} effort will make it faster and cheaper to run, on every case going forward.
                    Potential savings: lower cost per run and quicker turnaround, with somewhat less thoroughness on ambiguous cases.
                  </>
                )}
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setTierConfirmOpen(false)}>
                Cancel
              </Button>
              <Button variant={increasing ? 'primary' : 'accent'} onClick={applyBuffer}>
                {increasing ? 'Confirm increase' : 'Confirm and save'}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <AgentConfigImpactModal open={impactOpen} impact={impact} note={note} onNoteChange={setNote} onCancel={() => setImpactOpen(false)} onConfirm={confirmPublish} confirming={publishing} />
    </div>
  );
}
