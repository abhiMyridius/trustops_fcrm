import { useEffect, useState } from 'react';
import type { TriggerCategoryDef } from '@/types';
import type { AutoClearCondition, AutoClearFieldKey, ClassificationSignal, ScreeningAutoClearRule } from '@/types/analyst';
import { AUTO_CLEAR_FIELD_CATALOG } from '@/types/analyst';
import {
  commitClassificationRulesChange,
  getClassificationRulesVersions,
  getClassificationSignals,
  getFieldSet,
  getScreeningAutoClearRules,
  getTriggerProposalThresholds,
  previewClassificationRulesImpact,
  type ClassificationRulesCandidate,
  type ClassificationRulesImpact,
} from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge, Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Select, Switch, Textarea } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';

const SECTION_ID = 'classification';
const SECTION_LABEL = 'Classification Rules';

function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function truncate(text: string, n = 46): string {
  return text.length > n ? `${text.slice(0, n).trim()}...` : text;
}

// A signal or disambiguation note "fires" against a test description when enough of its own
// distinctive vocabulary shows up in the text - a small illustrative stand-in for the real
// Classification Agent, not a claim of real NLP.
const GENERIC_WORDS = new Set([
  'submission', 'mentions', 'currently', 'appears', 'without', 'named', 'under', 'contract', 'shows',
  'describes', 'existing', 'standalone', 'rather', 'behaviour', 'population', 'served', 'criteria',
  'onboarding', 'diagram', 'transmission', 'mechanism', 'internal', 'process', 'system', 'control',
  'active', 'operations', 'active-operations',
]);
function significantWords(text: string): string[] {
  return Array.from(
    new Set(
      text
        .toLowerCase()
        .replace(/[^a-z\s]/g, ' ')
        .split(/\s+/)
        .filter((w) => w.length >= 6 && !GENERIC_WORDS.has(w)),
    ),
  );
}
function signalFires(signalText: string, description: string): boolean {
  const words = significantWords(signalText);
  if (words.length === 0) return false;
  const desc = description.toLowerCase();
  const hits = words.filter((w) => desc.includes(w)).length;
  return hits >= Math.min(2, words.length);
}

function renderConditionLabel(c: AutoClearCondition): string {
  const meta = AUTO_CLEAR_FIELD_CATALOG[c.field];
  if (meta.type === 'boolean') return `${meta.label}: ${c.value ? 'Yes' : 'No'}`;
  const op = c.operator === 'lessThan' ? '<' : '≥';
  return `${meta.label} ${op} ${c.value}`;
}

const TEST_PRESETS = [
  {
    label: 'New vendor onboarding',
    text: "We're onboarding a new third-party payment processor, Meridian Settlements Ltd, under a new MSA for card settlement services. A vendor due-diligence questionnaire is attached.",
  },
  {
    label: 'Internal process update',
    text: 'Internal update to our KYC review checklist and escalation process. No new systems, vendors or customer-facing change involved.',
  },
  {
    label: 'New wallet feature',
    text: 'Launching a stored-value wallet capability within the existing mobile app for existing customers, with its own settlement and revenue mechanism.',
  },
];

interface TestResult {
  trigger: string;
  threshold: number;
  combinedWeight: number;
  proposed: boolean;
  firedSignals: { text: string; weight: number }[];
  matchedNotes: string[];
}

// -----------------------------------------------------------------------
// A signal or disambiguation note row - 12-column grid (§A2): rule text 6, active/confused-with
// 2, weight 2, actions 2. Disambiguation notes reuse the weight column for their "applies to"
// target trigger instead, since they carry no weight of their own.
// -----------------------------------------------------------------------
function SignalRow({
  s,
  editing,
  canMoveUp,
  canMoveDown,
  triggerOptions,
  onStartEdit,
  onCancelEdit,
  onConfirmEdit,
  onToggleActive,
  onDelete,
  onMoveUp,
  onMoveDown,
}: {
  s: ClassificationSignal;
  editing: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  triggerOptions: string[];
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onConfirmEdit: (patch: { text: string; weight: number | null; confusedWith?: string }) => void;
  onToggleActive: () => void;
  onDelete: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  const isNote = s.kind === 'disambiguation';
  const [draftText, setDraftText] = useState(s.text);
  const [draftWeight, setDraftWeight] = useState(s.weight ?? 0.5);
  const [draftConfusedWith, setDraftConfusedWith] = useState(s.confusedWith ?? triggerOptions[0] ?? '');

  useEffect(() => {
    if (editing) {
      setDraftText(s.text);
      setDraftWeight(s.weight ?? 0.5);
      setDraftConfusedWith(s.confusedWith ?? triggerOptions[0] ?? '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  return (
    <div className="grid grid-cols-12 gap-3 items-center py-2.5 px-2 -mx-2 rounded-md text-[12.5px]">
      <div className="col-span-6">
        {editing ? (
          <Textarea rows={2} value={draftText} onChange={(e) => setDraftText(e.target.value)} className="text-[12.5px] py-1.5" />
        ) : (
          <span className="text-text-secondary leading-snug">{s.text}</span>
        )}
      </div>
      <div className="col-span-2">
        <Switch checked={s.active} onChange={onToggleActive} disabled={editing} />
      </div>
      <div className="col-span-2">
        {isNote ? (
          editing ? (
            <Select value={draftConfusedWith} onChange={(e) => setDraftConfusedWith(e.target.value)} className="text-[11.5px] py-1">
              {triggerOptions.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          ) : (
            <span className="text-[11px] text-text-faint">&#8618; {s.confusedWith}</span>
          )
        ) : editing ? (
          <Input type="number" min={0} max={1} step={0.05} value={draftWeight} onChange={(e) => setDraftWeight(Number(e.target.value))} className="py-1.5 text-center" />
        ) : (
          <span className="font-mono text-text-primary">{s.weight?.toFixed(2)}</span>
        )}
      </div>
      <div className="col-span-2 flex items-center justify-end gap-1">
        {editing ? (
          <>
            <Button size="sm" variant="ghost" onClick={onCancelEdit}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!draftText.trim() || (isNote && !draftConfusedWith)}
              onClick={() => onConfirmEdit({ text: draftText.trim(), weight: isNote ? null : draftWeight, confusedWith: isNote ? draftConfusedWith : undefined })}
            >
              Confirm
            </Button>
          </>
        ) : (
          <div className="flex items-center gap-1 border border-border-strong rounded-md p-1">
            <button
              disabled={!canMoveUp}
              onClick={onMoveUp}
              className="text-text-faint hover:text-carbon-moss disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer text-[13px] leading-none px-0.5"
              aria-label="Move up"
            >
              &uarr;
            </button>
            <button
              disabled={!canMoveDown}
              onClick={onMoveDown}
              className="text-text-faint hover:text-carbon-moss disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer text-[13px] leading-none px-0.5"
              aria-label="Move down"
            >
              &darr;
            </button>
            <Button size="sm" variant="ghost" onClick={onStartEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={onDelete}>
              Delete
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function AddSignalRow({ onAdd, onCancel }: { onAdd: (text: string, weight: number) => void; onCancel: () => void }) {
  const [text, setText] = useState('');
  const [weight, setWeight] = useState(0.5);
  return (
    <div className="grid grid-cols-12 gap-3 items-start py-2.5 px-2 -mx-2 rounded-md bg-surface-tertiary text-[12.5px]">
      <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="What should the Classification Agent look for?" className="col-span-6 text-[12.5px] py-1.5" />
      <div className="col-span-2" />
      <Input type="number" min={0} max={1} step={0.05} value={weight} onChange={(e) => setWeight(Number(e.target.value))} className="col-span-2 py-1.5 text-center" />
      <div className="col-span-2 flex justify-end gap-1.5">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={!text.trim()} onClick={() => onAdd(text.trim(), weight)}>
          Add
        </Button>
      </div>
    </div>
  );
}

function AddNoteRow({ triggerOptions, onAdd, onCancel }: { triggerOptions: string[]; onAdd: (text: string, confusedWith: string) => void; onCancel: () => void }) {
  const [text, setText] = useState('');
  const [confusedWith, setConfusedWith] = useState(triggerOptions[0] ?? '');
  return (
    <div className="grid grid-cols-12 gap-3 items-start py-2.5 px-2 -mx-2 rounded-md bg-surface-tertiary text-[12.5px]">
      <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="What confusion does this describe?" className="col-span-6 text-[12.5px] py-1.5" />
      <div className="col-span-2" />
      <Select value={confusedWith} onChange={(e) => setConfusedWith(e.target.value)} className="col-span-2 text-[11.5px] py-1">
        {triggerOptions.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </Select>
      <div className="col-span-2 flex justify-end gap-1.5">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={!text.trim() || !confusedWith} onClick={() => onAdd(text.trim(), confusedWith)}>
          Add
        </Button>
      </div>
    </div>
  );
}

const AUTO_CLEAR_FIELD_KEYS = Object.keys(AUTO_CLEAR_FIELD_CATALOG) as AutoClearFieldKey[];

function AutoClearRuleCard({
  rule,
  editing,
  onStartEdit,
  onCancelEdit,
  onConfirmEdit,
  onToggleActive,
  onDelete,
}: {
  rule: ScreeningAutoClearRule;
  editing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onConfirmEdit: (patch: { name: string; conditions: AutoClearCondition[] }) => void;
  onToggleActive: () => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(rule.name);
  const [conditions, setConditions] = useState<AutoClearCondition[]>(rule.conditions);

  useEffect(() => {
    if (editing) {
      setName(rule.name);
      setConditions(rule.conditions);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  function updateCondition(i: number, patch: Partial<AutoClearCondition>) {
    setConditions((cs) => cs.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }
  function addCondition() {
    setConditions((cs) => [...cs, { id: newId('cond'), field: 'newVendor', operator: 'is', value: false }]);
  }
  function removeCondition(i: number) {
    setConditions((cs) => cs.filter((_, idx) => idx !== i));
  }

  return (
    <div className="flex flex-col gap-3 px-3.5 py-3 rounded-md border border-border-strong">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        {editing ? (
          <Input value={name} onChange={(e) => setName(e.target.value)} className="flex-1 min-w-[200px] text-[13px] py-1.5" placeholder="Rule name" />
        ) : (
          <span className="text-[13.5px] font-semibold text-text-primary">{rule.name}</span>
        )}
        <div className="flex items-center gap-2.5 shrink-0">
          <span className="text-[11px] text-text-faint">Cleared {rule.recentClearCount} of last 50</span>
          <Switch checked={rule.active} onChange={onToggleActive} disabled={editing} />
        </div>
      </div>

      {editing ? (
        <div className="flex flex-col gap-2">
          {conditions.map((c, i) => {
            const meta = AUTO_CLEAR_FIELD_CATALOG[c.field];
            return (
              <div key={c.id} className="flex items-center gap-2">
                <Select
                  value={c.field}
                  onChange={(e) => {
                    const field = e.target.value as AutoClearFieldKey;
                    const fmeta = AUTO_CLEAR_FIELD_CATALOG[field];
                    updateCondition(i, { field, operator: fmeta.type === 'boolean' ? 'is' : 'lessThan', value: fmeta.type === 'boolean' ? false : 0 });
                  }}
                  className="flex-1 text-[12px] py-1.5"
                >
                  {AUTO_CLEAR_FIELD_KEYS.map((k) => (
                    <option key={k} value={k}>
                      {AUTO_CLEAR_FIELD_CATALOG[k].label}
                    </option>
                  ))}
                </Select>
                {meta.type === 'boolean' ? (
                  <Select value={String(c.value)} onChange={(e) => updateCondition(i, { value: e.target.value === 'true' })} className="w-24 text-[12px] py-1.5">
                    <option value="true">Yes</option>
                    <option value="false">No</option>
                  </Select>
                ) : (
                  <>
                    <Select value={c.operator} onChange={(e) => updateCondition(i, { operator: e.target.value as AutoClearCondition['operator'] })} className="w-20 text-[12px] py-1.5">
                      <option value="lessThan">&lt;</option>
                      <option value="greaterThanOrEqual">&ge;</option>
                    </Select>
                    <Input type="number" value={c.value as number} onChange={(e) => updateCondition(i, { value: Number(e.target.value) })} className="w-20 py-1.5 text-center" />
                  </>
                )}
                <button onClick={() => removeCondition(i)} className="text-text-faint hover:text-danger cursor-pointer shrink-0" aria-label="Remove condition">
                  <Icon name="trash" size={14} />
                </button>
              </div>
            );
          })}
          <Button size="sm" variant="ghost" className="self-start" icon={<Icon name="plus" size={13} />} onClick={addCondition}>
            Add condition
          </Button>
          <div className="flex justify-end gap-2 pt-1">
            <Button size="sm" variant="ghost" onClick={onCancelEdit}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" disabled={!name.trim() || conditions.length === 0} onClick={() => onConfirmEdit({ name: name.trim(), conditions })}>
              Confirm
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-1.5">
          {rule.conditions.map((c, i) => (
            <span key={c.id} className="inline-flex items-center gap-1.5">
              <Tag>{renderConditionLabel(c)}</Tag>
              {i < rule.conditions.length - 1 && <span className="text-[11px] text-text-faint">AND</span>}
            </span>
          ))}
        </div>
      )}

      {!editing && (
        <div className="flex justify-end">
          <div className="flex items-center gap-1.5 border border-border-strong rounded-md p-1">
            <Button size="sm" variant="ghost" onClick={onStartEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={onDelete}>
              Delete
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function makeBlankRule(): ScreeningAutoClearRule {
  return { id: 'new', name: '', conditions: [{ id: newId('cond'), field: 'newVendor', operator: 'is', value: false }], active: true, recentClearCount: 0 };
}

// -----------------------------------------------------------------------
// Main section
// -----------------------------------------------------------------------
export function ClassificationRulesSection({ onNavigateToCategories }: { onNavigateToCategories: () => void }) {
  const { changes, addChange, clearSection, registerActions } = useWorkingSet();
  const myChanges = changes.filter((c) => c.sectionId === SECTION_ID);
  const toast = useToast();

  const [triggerDefs, setTriggerDefs] = useState<TriggerCategoryDef[] | null>(null);
  const [versions, setVersions] = useState<{ version: number; publishedAt: string; note: string; changedBy: string }[] | null>(null);

  const [signals, setSignals] = useState<ClassificationSignal[]>([]);
  const [autoClearRules, setAutoClearRules] = useState<ScreeningAutoClearRule[]>([]);
  const [triggerThresholds, setTriggerThresholds] = useState<Record<string, number>>({});
  const [initial, setInitial] = useState<ClassificationRulesCandidate | null>(null);

  const [touchedSignalIds, setTouchedSignalIds] = useState<Set<string>>(new Set());
  const [touchedRuleIds, setTouchedRuleIds] = useState<Set<string>>(new Set());

  const [editingSignalId, setEditingSignalId] = useState<string | null>(null);
  const [addSignalOpenFor, setAddSignalOpenFor] = useState<string | null>(null);
  const [addNoteOpenFor, setAddNoteOpenFor] = useState<string | null>(null);

  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [addRuleOpen, setAddRuleOpen] = useState(false);
  const [blankRule, setBlankRule] = useState<ScreeningAutoClearRule>(makeBlankRule);

  const [editingThresholdFor, setEditingThresholdFor] = useState<string | null>(null);
  const [thresholdDraft, setThresholdDraft] = useState(0.6);

  const [deleteTarget, setDeleteTarget] = useState<{ kind: 'signal' | 'rule'; id: string; label: string } | null>(null);

  const [impact, setImpact] = useState<ClassificationRulesImpact | null>(null);
  const [impactOpen, setImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [publishing, setPublishing] = useState(false);

  const [testPreset, setTestPreset] = useState('');
  const [testText, setTestText] = useState('');
  const [testResults, setTestResults] = useState<TestResult[] | null>(null);

  async function reload() {
    const [s, a, t, snap, vers] = await Promise.all([
      getClassificationSignals(),
      getScreeningAutoClearRules(),
      getTriggerProposalThresholds(),
      getFieldSet(),
      getClassificationRulesVersions(),
    ]);
    setSignals(s);
    setAutoClearRules(a);
    setTriggerThresholds(t);
    setTriggerDefs(snap.triggerCategories);
    setVersions(vers);
    setInitial({ signals: s, autoClearRules: a, triggerThresholds: t });
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function candidate(): ClassificationRulesCandidate {
    return { signals, autoClearRules, triggerThresholds };
  }

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }
  function logSignal(id: string, label: string, revert: () => void) {
    log(label, revert);
    setTouchedSignalIds((prev) => new Set(prev).add(id));
  }
  function logRule(id: string, label: string, revert: () => void) {
    log(label, revert);
    setTouchedRuleIds((prev) => new Set(prev).add(id));
  }

  async function handleReviewImpact() {
    const result = await previewClassificationRulesImpact(candidate(), Array.from(touchedSignalIds), Array.from(touchedRuleIds));
    setImpact(result);
    setImpactOpen(true);
  }

  async function handlePublish() {
    setPublishing(true);
    await commitClassificationRulesChange(candidate(), note.trim() || 'Classification signal, threshold or auto-clear rule update.');
    setPublishing(false);
    setImpactOpen(false);
    setNote('');
    clearSection(SECTION_ID);
    setTouchedSignalIds(new Set());
    setTouchedRuleIds(new Set());
    toast('New classification rules version published. In-flight drafts stay on their starting version.', 'success');
    reload();
  }

  function handleDiscard() {
    if (!initial) return;
    setSignals(initial.signals);
    setAutoClearRules(initial.autoClearRules);
    setTriggerThresholds(initial.triggerThresholds);
    clearSection(SECTION_ID);
    setTouchedSignalIds(new Set());
    setTouchedRuleIds(new Set());
  }

  useEffect(() => {
    registerActions(SECTION_ID, { publish: handleReviewImpact, discard: handleDiscard });
  });

  if (!triggerDefs || !versions) return <Skeleton className="h-96 rounded-md" />;

  const activeTriggers = triggerDefs.filter((t) => t.status === 'active');

  function addSignal(triggerCategory: string, text: string, weight: number) {
    const id = newId('cs');
    const s: ClassificationSignal = { id, triggerCategory, kind: 'signal', text, weight, active: true, recentFireCount: 0 };
    setSignals((arr) => [...arr, s]);
    logSignal(id, `Added signal "${truncate(text)}" to ${triggerCategory}`, () => setSignals((arr) => arr.filter((x) => x.id !== id)));
    setAddSignalOpenFor(null);
  }
  function addNote(triggerCategory: string, text: string, confusedWith: string) {
    const id = newId('cs');
    const s: ClassificationSignal = { id, triggerCategory, kind: 'disambiguation', text, weight: null, active: true, confusedWith, recentFireCount: 0 };
    setSignals((arr) => [...arr, s]);
    logSignal(id, `Added disambiguation note to ${triggerCategory}`, () => setSignals((arr) => arr.filter((x) => x.id !== id)));
    setAddNoteOpenFor(null);
  }
  function confirmEditSignal(id: string, patch: { text: string; weight: number | null; confusedWith?: string }) {
    const prev = signals.find((s) => s.id === id);
    if (!prev) return;
    setSignals((arr) => arr.map((s) => (s.id === id ? { ...s, text: patch.text, weight: patch.weight, confusedWith: patch.confusedWith } : s)));
    logSignal(id, `Edited "${truncate(prev.text)}"`, () => setSignals((arr) => arr.map((s) => (s.id === id ? prev : s))));
    setEditingSignalId(null);
  }
  function toggleSignalActive(s: ClassificationSignal) {
    setSignals((arr) => arr.map((x) => (x.id === s.id ? { ...x, active: !x.active } : x)));
    logSignal(s.id, `${s.active ? 'Disabled' : 'Enabled'} "${truncate(s.text)}"`, () => setSignals((arr) => arr.map((x) => (x.id === s.id ? { ...x, active: s.active } : x))));
  }
  function swapSignals(idA: string, idB: string) {
    setSignals((arr) => {
      const next = [...arr];
      const posA = next.findIndex((s) => s.id === idA);
      const posB = next.findIndex((s) => s.id === idB);
      if (posA === -1 || posB === -1) return arr;
      [next[posA], next[posB]] = [next[posB], next[posA]];
      return next;
    });
  }
  function moveSignal(item: ClassificationSignal, direction: 'up' | 'down') {
    const siblingIds = signals.filter((s) => s.triggerCategory === item.triggerCategory && s.kind === item.kind).map((s) => s.id);
    const idx = siblingIds.indexOf(item.id);
    const swapIdx = direction === 'up' ? idx - 1 : idx + 1;
    if (swapIdx < 0 || swapIdx >= siblingIds.length) return;
    const otherId = siblingIds[swapIdx];
    swapSignals(item.id, otherId);
    logSignal(item.id, `Reordered "${truncate(item.text)}"`, () => swapSignals(item.id, otherId));
  }
  function requestDeleteSignal(s: ClassificationSignal) {
    setDeleteTarget({ kind: 'signal', id: s.id, label: truncate(s.text) });
  }

  function handleAddRuleConfirm(patch: { name: string; conditions: AutoClearCondition[] }) {
    const id = newId('ac');
    const rule: ScreeningAutoClearRule = { id, name: patch.name, conditions: patch.conditions, active: true, recentClearCount: 0 };
    setAutoClearRules((arr) => [...arr, rule]);
    logRule(id, `Added auto-clear rule "${patch.name}"`, () => setAutoClearRules((arr) => arr.filter((r) => r.id !== id)));
    setAddRuleOpen(false);
  }
  function confirmEditRule(id: string, patch: { name: string; conditions: AutoClearCondition[] }) {
    const prev = autoClearRules.find((r) => r.id === id);
    if (!prev) return;
    setAutoClearRules((arr) => arr.map((r) => (r.id === id ? { ...r, name: patch.name, conditions: patch.conditions } : r)));
    logRule(id, `Edited auto-clear rule "${prev.name}"`, () => setAutoClearRules((arr) => arr.map((r) => (r.id === id ? prev : r))));
    setEditingRuleId(null);
  }
  function toggleRuleActive(r: ScreeningAutoClearRule) {
    setAutoClearRules((arr) => arr.map((x) => (x.id === r.id ? { ...x, active: !x.active } : x)));
    logRule(r.id, `${r.active ? 'Disabled' : 'Enabled'} auto-clear rule "${r.name}"`, () => setAutoClearRules((arr) => arr.map((x) => (x.id === r.id ? { ...x, active: r.active } : x))));
  }
  function requestDeleteRule(r: ScreeningAutoClearRule) {
    setDeleteTarget({ kind: 'rule', id: r.id, label: r.name });
  }

  function confirmDelete() {
    if (!deleteTarget) return;
    if (deleteTarget.kind === 'signal') {
      const removed = signals.find((s) => s.id === deleteTarget.id);
      setSignals((arr) => arr.filter((s) => s.id !== deleteTarget.id));
      if (removed) logSignal(removed.id, `Deleted "${truncate(removed.text)}"`, () => setSignals((arr) => [...arr, removed]));
    } else {
      const removed = autoClearRules.find((r) => r.id === deleteTarget.id);
      setAutoClearRules((arr) => arr.filter((r) => r.id !== deleteTarget.id));
      if (removed) logRule(removed.id, `Deleted auto-clear rule "${removed.name}"`, () => setAutoClearRules((arr) => [...arr, removed]));
    }
    setDeleteTarget(null);
  }

  function startEditThreshold(trigger: string) {
    setThresholdDraft(triggerThresholds[trigger] ?? 0.6);
    setEditingThresholdFor(trigger);
  }
  function confirmThreshold(trigger: string) {
    const prev = triggerThresholds[trigger] ?? 0.6;
    setTriggerThresholds((m) => ({ ...m, [trigger]: thresholdDraft }));
    setEditingThresholdFor(null);
    if (prev !== thresholdDraft) {
      log(`Proposal threshold for "${trigger}" ${prev.toFixed(2)} → ${thresholdDraft.toFixed(2)}`, () => setTriggerThresholds((m) => ({ ...m, [trigger]: prev })));
    }
  }

  function runTest() {
    const desc = testText;
    const results: TestResult[] = activeTriggers.map((t) => {
      const triggerSignals = signals.filter((s) => s.triggerCategory === t.key && s.kind === 'signal' && s.active);
      const fired = triggerSignals.filter((s) => signalFires(s.text, desc));
      const combinedWeight = Math.min(1, fired.reduce((sum, s) => sum + (s.weight ?? 0), 0));
      const threshold = triggerThresholds[t.key] ?? 0.6;
      const notes = signals.filter((s) => s.triggerCategory === t.key && s.kind === 'disambiguation' && s.active && signalFires(s.text, desc));
      return {
        trigger: t.key,
        threshold,
        combinedWeight,
        proposed: combinedWeight >= threshold,
        firedSignals: fired.map((s) => ({ text: s.text, weight: s.weight ?? 0 })),
        matchedNotes: notes.map((n) => n.text),
      };
    });
    setTestResults(results);
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug">
        <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
        Not visible to the PO - these are the rules deciding whether their change needs review in the first place.
      </div>

      <div className="flex items-start gap-2.5 bg-verdant-1 rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug">
        <Icon name="sparkles" size={14} className="text-teal shrink-0 mt-0.5" />
        <span>
          <strong>How a tag gets proposed:</strong> a trigger's active signals sum their weights (capped at 1.0). If that sum meets or exceeds the trigger's proposal threshold, the
          Classification Agent proposes the tag. Disambiguation notes carry no weight - they flag a confusion with another trigger, not evidence for this one.
        </span>
      </div>

      {activeTriggers.map((t) => {
        const triggerSignals = signals.filter((s) => s.triggerCategory === t.key && s.kind === 'signal');
        const triggerNotes = signals.filter((s) => s.triggerCategory === t.key && s.kind === 'disambiguation');
        const threshold = triggerThresholds[t.key] ?? 0.6;
        const otherTriggers = activeTriggers.map((x) => x.key).filter((k) => k !== t.key);

        return (
          <Card key={t.key} material="flat" className="p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between gap-3 flex-wrap pb-3 border-b border-border-hairline">
              <div className="flex items-center gap-2.5 flex-wrap">
                <span className="text-sm font-bold text-text-primary">{t.key}</span>
                <Badge tone="info">{t.amlCategory}</Badge>
                <button onClick={onNavigateToCategories} className="text-[11.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer inline-flex items-center gap-1">
                  <Icon name="external-link" size={11} /> Categories & Fields
                </button>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11.5px] text-text-faint">Proposal threshold</span>
                {editingThresholdFor === t.key ? (
                  <>
                    <Input
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={thresholdDraft}
                      onChange={(e) => setThresholdDraft(Number(e.target.value))}
                      className="w-20 py-1 text-center text-[12.5px]"
                    />
                    <Button size="sm" variant="ghost" onClick={() => setEditingThresholdFor(null)}>
                      Cancel
                    </Button>
                    <Button size="sm" variant="primary" onClick={() => confirmThreshold(t.key)}>
                      Confirm
                    </Button>
                  </>
                ) : (
                  <>
                    <span className="font-mono text-[13px] font-semibold text-text-primary">{threshold.toFixed(2)}</span>
                    <Button size="sm" variant="ghost" onClick={() => startEditThreshold(t.key)}>
                      Edit
                    </Button>
                  </>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Signals</span>
              <div className="grid grid-cols-12 gap-3 px-2 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">
                <span className="col-span-6">Rule text</span>
                <span className="col-span-2">Active</span>
                <span className="col-span-2">Weight</span>
                <span className="col-span-2 text-right">Actions</span>
              </div>
              {triggerSignals.length === 0 && addSignalOpenFor !== t.key && <p className="text-[12px] text-text-faint px-2">No signals yet.</p>}
              {triggerSignals.map((s, i) => (
                <SignalRow
                  key={s.id}
                  s={s}
                  editing={editingSignalId === s.id}
                  canMoveUp={i > 0}
                  canMoveDown={i < triggerSignals.length - 1}
                  triggerOptions={otherTriggers}
                  onStartEdit={() => setEditingSignalId(s.id)}
                  onCancelEdit={() => setEditingSignalId(null)}
                  onConfirmEdit={(patch) => confirmEditSignal(s.id, patch)}
                  onToggleActive={() => toggleSignalActive(s)}
                  onDelete={() => requestDeleteSignal(s)}
                  onMoveUp={() => moveSignal(s, 'up')}
                  onMoveDown={() => moveSignal(s, 'down')}
                />
              ))}
              {addSignalOpenFor === t.key ? (
                <AddSignalRow onAdd={(text, weight) => addSignal(t.key, text, weight)} onCancel={() => setAddSignalOpenFor(null)} />
              ) : (
                <Button size="sm" variant="ghost" className="self-start" icon={<Icon name="plus" size={13} />} onClick={() => setAddSignalOpenFor(t.key)}>
                  Add signal
                </Button>
              )}
            </div>

            <div className="flex flex-col gap-1.5 pt-2 border-t border-border-hairline">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Disambiguation notes</span>
              <p className="text-[11.5px] text-text-faint -mt-0.5">Confusions the agent makes with another trigger. Carry no weight - they don't contribute to a proposal.</p>
              {triggerNotes.length === 0 && addNoteOpenFor !== t.key && <p className="text-[12px] text-text-faint px-2">None yet.</p>}
              {triggerNotes.map((s, i) => (
                <SignalRow
                  key={s.id}
                  s={s}
                  editing={editingSignalId === s.id}
                  canMoveUp={i > 0}
                  canMoveDown={i < triggerNotes.length - 1}
                  triggerOptions={otherTriggers}
                  onStartEdit={() => setEditingSignalId(s.id)}
                  onCancelEdit={() => setEditingSignalId(null)}
                  onConfirmEdit={(patch) => confirmEditSignal(s.id, patch)}
                  onToggleActive={() => toggleSignalActive(s)}
                  onDelete={() => requestDeleteSignal(s)}
                  onMoveUp={() => moveSignal(s, 'up')}
                  onMoveDown={() => moveSignal(s, 'down')}
                />
              ))}
              {addNoteOpenFor === t.key ? (
                <AddNoteRow triggerOptions={otherTriggers} onAdd={(text, confusedWith) => addNote(t.key, text, confusedWith)} onCancel={() => setAddNoteOpenFor(null)} />
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  className="self-start"
                  icon={<Icon name="plus" size={13} />}
                  onClick={() => setAddNoteOpenFor(t.key)}
                  disabled={otherTriggers.length === 0}
                >
                  Add disambiguation note
                </Button>
              )}
            </div>
          </Card>
        );
      })}

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Screening auto-clear rules</span>
          {!addRuleOpen && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Icon name="plus" size={13} />}
              onClick={() => {
                setBlankRule(makeBlankRule());
                setAddRuleOpen(true);
              }}
            >
              Add rule
            </Button>
          )}
        </div>
        <p className="text-[12.5px] text-text-muted -mt-2">Structured conditions the Screening Agent checks to auto-clear a change without a full case. All conditions in a rule must hold.</p>
        <div className="flex flex-col gap-2.5">
          {autoClearRules.map((r) => (
            <AutoClearRuleCard
              key={r.id}
              rule={r}
              editing={editingRuleId === r.id}
              onStartEdit={() => setEditingRuleId(r.id)}
              onCancelEdit={() => setEditingRuleId(null)}
              onConfirmEdit={(patch) => confirmEditRule(r.id, patch)}
              onToggleActive={() => toggleRuleActive(r)}
              onDelete={() => requestDeleteRule(r)}
            />
          ))}
          {addRuleOpen && (
            <AutoClearRuleCard
              rule={blankRule}
              editing
              onStartEdit={() => {}}
              onCancelEdit={() => setAddRuleOpen(false)}
              onConfirmEdit={handleAddRuleConfirm}
              onToggleActive={() => {}}
              onDelete={() => {}}
            />
          )}
        </div>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Version history</span>
        <div className="flex flex-col divide-y divide-border-hairline">
          {versions
            .slice()
            .reverse()
            .map((v) => (
              <div key={v.version} className="flex items-center gap-3 py-2.5 text-[13px]">
                <span className="font-mono text-text-faint w-10">v{v.version}</span>
                <span className="text-text-secondary flex-1">{v.note}</span>
                <span className="text-text-faint text-[12px]">{v.changedBy}</span>
                <span className="text-text-faint text-[12px]">{formatDate(v.publishedAt)}</span>
              </div>
            ))}
        </div>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary">Test panel</span>
        <p className="text-[12.5px] text-text-muted -mt-1">Read-only - runs against the rule set as currently edited, including unsaved changes. Changes nothing.</p>
        <div className="flex gap-3 flex-wrap">
          <Select
            value={testPreset}
            onChange={(e) => {
              const p = TEST_PRESETS.find((x) => x.label === e.target.value);
              setTestPreset(e.target.value);
              if (p) setTestText(p.text);
            }}
            className="w-[240px]"
          >
            <option value="">Sample submission...</option>
            {TEST_PRESETS.map((p) => (
              <option key={p.label} value={p.label}>
                {p.label}
              </option>
            ))}
          </Select>
          <Button variant="primary" onClick={runTest} disabled={!testText.trim()} className="shrink-0">
            Run test
          </Button>
        </div>
        <Textarea rows={3} placeholder="Paste or write a submission description..." value={testText} onChange={(e) => setTestText(e.target.value)} />

        {testResults && (
          <div className="flex flex-col gap-2 pt-2 border-t border-border-hairline">
            {testResults
              .slice()
              .sort((a, b) => Number(b.proposed) - Number(a.proposed) || b.combinedWeight - a.combinedWeight)
              .map((r) => (
                <div key={r.trigger} className={`flex flex-col gap-1.5 px-3.5 py-3 rounded-md ${r.proposed ? 'bg-verdant-1' : 'bg-surface-tertiary'}`}>
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <span className="text-[13px] font-semibold text-text-primary">{r.trigger}</span>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[12.5px] text-text-secondary">
                        {r.combinedWeight.toFixed(2)} / {r.threshold.toFixed(2)}
                      </span>
                      <Badge tone={r.proposed ? 'success' : 'default'}>{r.proposed ? 'Would propose' : 'Would not propose'}</Badge>
                    </div>
                  </div>
                  {r.firedSignals.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {r.firedSignals.map((s, i) => (
                        <Tag key={i}>
                          {truncate(s.text, 40)} &middot; {s.weight.toFixed(2)}
                        </Tag>
                      ))}
                    </div>
                  )}
                  {r.matchedNotes.length > 0 && <p className="text-[11.5px] text-text-faint">Also matched a disambiguation note - worth double-checking the trigger it names instead.</p>}
                </div>
              ))}
          </div>
        )}
      </Card>

      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="Delete this entry" width={420}>
        {deleteTarget && (
          <div className="flex flex-col gap-4">
            <p className="text-[13px] text-text-secondary">
              Delete {deleteTarget.kind === 'signal' ? 'the signal or disambiguation note' : 'the auto-clear rule'} "{deleteTarget.label}"? This is staged in the working set and can be
              undone from there until you publish.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={confirmDelete}>
                Delete
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={impactOpen} onClose={() => setImpactOpen(false)} title="Impact preview - review before publishing" width={640}>
        {impact && (
          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3 py-2.5">
              <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
              <p className="text-[12px] text-[#133338]">
                Reflects the full working set - {myChanges.length} pending change{myChanges.length === 1 ? '' : 's'} - not just the edit that opened this. In-flight drafts continue on the
                rules version they started on; new drafts use the new version; submitted and closed cases are never reclassified.
              </p>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">In-flight drafts by rules version</span>
              {impact.draftsByVersion.length === 0 ? (
                <p className="text-[13px] text-teal font-medium">None - no drafts in flight.</p>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {impact.draftsByVersion.map((d) => (
                    <div key={d.version} className="flex items-center justify-between px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
                      <span className="text-text-secondary">Rules v{d.version}</span>
                      <span className="font-mono font-semibold text-text-primary">
                        {d.count} draft{d.count === 1 ? '' : 's'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <p className="text-[11.5px] text-text-faint">New drafts will start on v{impact.newDraftsVersion}.</p>
            </div>

            {impact.changedSignals.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Signals changed - recent firing</span>
                {impact.changedSignals.map((s) => (
                  <div key={s.id} className="flex items-center justify-between gap-3 px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
                    <span className="text-text-secondary flex-1">{truncate(s.text, 70)}</span>
                    <span className="font-mono font-semibold text-text-primary shrink-0">{s.recentFireCount} of last 50</span>
                  </div>
                ))}
              </div>
            )}

            {impact.changedRules.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Auto-clear rules changed - recent clears</span>
                {impact.changedRules.map((r) => (
                  <div key={r.id} className="flex items-center justify-between gap-3 px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
                    <span className="text-text-secondary flex-1">{r.name}</span>
                    <span className="font-mono font-semibold text-text-primary shrink-0">{r.recentClearCount} of last 50</span>
                  </div>
                ))}
              </div>
            )}

            <Textarea rows={2} placeholder="Version note (what changed and why)..." value={note} onChange={(e) => setNote(e.target.value)} />

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setImpactOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handlePublish} loading={publishing}>
                Confirm and publish new version
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
