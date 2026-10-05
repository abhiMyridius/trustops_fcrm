import { useEffect, useRef, useState } from 'react';
import type { FieldSetSnapshot, FieldSetVersion, OptionalFieldDef, RequiredFieldDef, TriggerCategory, TriggerCategoryDef } from '@/types';
import { ALL_AML_CATEGORIES } from '@/types/analyst';
import {
  commitFieldSetChange,
  getFieldSet,
  getFieldSetVersions,
  getPoOptionalFieldAdditions,
  isTriggerCategoryInUse,
  previewFieldSetImpact,
  type FieldSetCandidate,
  type FieldSetImpact,
} from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Select, Checkbox, Textarea } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';

const SECTION_ID = 'categories';
const SECTION_LABEL = 'Categories & Fields';
const FFIEC_MANUAL_URL = 'https://bsaaml.ffiec.gov/manual';

const FIELD_TYPES: NonNullable<RequiredFieldDef['type']>[] = ['text', 'textarea', 'date', 'documents'];

const CORE_TONE = { bg: '#DDE3E2', text: '#2c3a39' };
const CATEGORY_TONE_PALETTE = [
  { bg: '#194247', text: '#ffffff' },
  { bg: '#09A78D', text: '#ffffff' },
  { bg: '#337077', text: '#ffffff' },
  { bg: '#B98A1E', text: '#ffffff' },
  { bg: '#8a3324', text: '#ffffff' },
  { bg: '#5b3a8e', text: '#ffffff' },
  { bg: '#c2410c', text: '#ffffff' },
  { bg: '#0f4c81', text: '#ffffff' },
];

function mergeLocal(library: RequiredFieldDef[], coreKeys: string[], packKeys: Record<TriggerCategory, string[]>, categories: TriggerCategory[]): RequiredFieldDef[] {
  const lib = new Map(library.map((f) => [f.id, f]));
  const ids = new Map<string, RequiredFieldDef>();
  for (const k of coreKeys) {
    const f = lib.get(k);
    if (f && f.status !== 'retired') ids.set(k, f);
  }
  for (const cat of categories) {
    for (const k of packKeys[cat] ?? []) {
      const f = lib.get(k);
      if (f && f.status !== 'retired') ids.set(k, f);
    }
  }
  return Array.from(ids.values());
}

// -----------------------------------------------------------------------
// Collapsible section - every card on this page defaults closed (a managed view; open what
// you need) except the always-visible working-set banner, which lives at the Config shell level.
// -----------------------------------------------------------------------
function Section({
  id,
  title,
  meta,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  meta?: React.ReactNode;
  open: boolean;
  onToggle: (id: string) => void;
  children: React.ReactNode;
}) {
  return (
    <Card material="flat" className="overflow-hidden">
      {/* Two separate toggle buttons either side of `meta`, not one button wrapping it - `meta`
          sometimes carries its own interactive control (e.g. Add field to library), and a
          <button> cannot legally contain another <button>. */}
      <div className="w-full flex items-center gap-3 p-5">
        <button onClick={() => onToggle(id)} className="flex-1 flex items-center text-left cursor-pointer">
          <span className="text-sm font-bold text-text-primary">{title}</span>
        </button>
        {meta}
        <button onClick={() => onToggle(id)} className="shrink-0 text-text-faint cursor-pointer" aria-label={open ? 'Collapse section' : 'Expand section'}>
          <Icon name={open ? 'chevron-down' : 'chevron-right'} size={15} />
        </button>
      </div>
      {open && <div className="px-5 pb-5 flex flex-col gap-3">{children}</div>}
    </Card>
  );
}

function AddFieldToLibraryForm({ existingKeys, onAdd }: { existingKeys: string[]; onAdd: (f: RequiredFieldDef) => void }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState('');
  const [label, setLabel] = useState('');
  const [type, setType] = useState<NonNullable<RequiredFieldDef['type']>>('text');
  const [neverBlank, setNeverBlank] = useState(false);
  const toast = useToast();

  function submit() {
    const cleanKey = key.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    if (!cleanKey || !label.trim()) return;
    if (existingKeys.includes(cleanKey)) {
      toast(`A field with key "${cleanKey}" already exists. Keys are immutable once created - edit the existing entry instead.`, 'danger');
      return;
    }
    onAdd({ id: cleanKey, label: label.trim(), category: 'Common', type, neverBlank, status: 'active' });
    setKey('');
    setLabel('');
    setType('text');
    setNeverBlank(false);
    setOpen(false);
  }

  if (!open) {
    return (
      <Button size="sm" variant="secondary" icon={<Icon name="plus" size={14} />} onClick={() => setOpen(true)}>
        Add field to library
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2.5 p-3.5 rounded-md border border-border-strong bg-surface-tertiary">
      <div className="grid grid-cols-2 gap-2.5">
        <Input placeholder="Key (e.g. cost_centre)" value={key} onChange={(e) => setKey(e.target.value)} />
        <Input placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} />
      </div>
      <div className="flex items-center gap-3">
        <Select value={type} onChange={(e) => setType(e.target.value as typeof type)} className="w-[160px]">
          {FIELD_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
        <Checkbox checked={neverBlank} onChange={setNeverBlank} label={<span className="text-[12.5px]">Always required</span>} />
      </div>
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" onClick={submit}>
          Add to library
        </Button>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Field library row - read-only until Edit is clicked. Keys never change in any mode.
// -----------------------------------------------------------------------
function FieldLibraryRow({
  f,
  referencedByLabel,
  editing,
  highlighted,
  onStartEdit,
  onCancelEdit,
  onConfirmEdit,
  onRetire,
}: {
  f: RequiredFieldDef;
  referencedByLabel: string;
  editing: boolean;
  highlighted: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onConfirmEdit: (patch: { label: string; type: NonNullable<RequiredFieldDef['type']>; neverBlank: boolean }) => void;
  onRetire: () => void;
}) {
  const [draftLabel, setDraftLabel] = useState(f.label);
  const [draftType, setDraftType] = useState<NonNullable<RequiredFieldDef['type']>>(f.type ?? 'text');
  const [draftNeverBlank, setDraftNeverBlank] = useState(!!f.neverBlank);

  useEffect(() => {
    if (editing) {
      setDraftLabel(f.label);
      setDraftType(f.type ?? 'text');
      setDraftNeverBlank(!!f.neverBlank);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const retired = f.status === 'retired';

  return (
    <div
      id={`field-row-${f.id}`}
      className={`grid grid-cols-[1.4fr_170px_100px_1.4fr_120px_auto] gap-3 items-center py-2.5 px-2 -mx-2 rounded-md text-[12.5px] transition-colors ${retired ? 'opacity-50' : ''} ${
        highlighted ? 'bg-[#FBF3E4]' : ''
      }`}
    >
      {editing ? <Input value={draftLabel} onChange={(e) => setDraftLabel(e.target.value)} className="py-1.5 text-[12.5px]" /> : <span className="text-text-primary">{f.label}</span>}
      <span className="font-mono text-text-faint break-all">{f.id}</span>
      {editing ? (
        <Select value={draftType} onChange={(e) => setDraftType(e.target.value as typeof draftType)} className="py-1 text-[12px]">
          {FIELD_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
      ) : (
        <span className="text-text-secondary">{f.type ?? 'text'}</span>
      )}
      <span className="text-text-faint text-[11.5px]">{referencedByLabel}</span>
      {editing ? (
        <Checkbox checked={draftNeverBlank} onChange={setDraftNeverBlank} label={<span className="text-[11.5px]">Always required</span>} />
      ) : f.neverBlank ? (
        <Badge tone="danger">Always required</Badge>
      ) : (
        <span />
      )}
      <div className="flex items-center gap-1.5 justify-end">
        {editing ? (
          <>
            <Button size="sm" variant="ghost" onClick={onCancelEdit}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={() => onConfirmEdit({ label: draftLabel, type: draftType, neverBlank: draftNeverBlank })}>
              Confirm
            </Button>
          </>
        ) : retired ? (
          <Badge tone="default">Retired</Badge>
        ) : (
          <div className="flex items-center gap-1.5 border border-border-strong rounded-md p-1">
            <Button size="sm" variant="ghost" onClick={onStartEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={onRetire}>
              Retire
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Guided "+ Add trigger category" flow - four required steps, none skippable, ending in a
// summary before the new category is handed back to the page's own working set.
// -----------------------------------------------------------------------
interface TriggerDraft {
  name: string;
  description: string;
  fieldPackKeys: string[];
  amlCategory: string;
  classificationSignals: string[];
}

const EMPTY_DRAFT: TriggerDraft = { name: '', description: '', fieldPackKeys: [], amlCategory: '', classificationSignals: [''] };

function AddTriggerWizard({
  open,
  onClose,
  activeLibrary,
  existingKeys,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  activeLibrary: RequiredFieldDef[];
  existingKeys: string[];
  onCreate: (draft: TriggerDraft) => void;
}) {
  const [step, setStep] = useState(1);
  const [draft, setDraft] = useState<TriggerDraft>(EMPTY_DRAFT);

  useEffect(() => {
    if (open) {
      setStep(1);
      setDraft(EMPTY_DRAFT);
    }
  }, [open]);

  const cleanSignals = draft.classificationSignals.map((s) => s.trim()).filter(Boolean);
  const nameTaken = existingKeys.includes(draft.name.trim());
  const step1Valid = draft.name.trim().length > 0 && !nameTaken && draft.description.trim().length > 0;
  const step3Valid = !!draft.amlCategory;
  const step4Valid = cleanSignals.length > 0;

  const steps = [
    { n: 1, label: 'Name and description', valid: step1Valid },
    { n: 2, label: 'Field pack', valid: true },
    { n: 3, label: 'AML category mapping', valid: step3Valid },
    { n: 4, label: 'Classification signals', valid: step4Valid },
  ];

  function setSignal(i: number, v: string) {
    setDraft((d) => ({ ...d, classificationSignals: d.classificationSignals.map((s, idx) => (idx === i ? v : s)) }));
  }
  function addSignal() {
    setDraft((d) => ({ ...d, classificationSignals: [...d.classificationSignals, ''] }));
  }
  function removeSignal(i: number) {
    setDraft((d) => ({ ...d, classificationSignals: d.classificationSignals.filter((_, idx) => idx !== i) }));
  }

  function canAdvanceFrom(n: number): boolean {
    if (n === 1) return step1Valid;
    if (n === 3) return step3Valid;
    if (n === 4) return step4Valid;
    return true;
  }

  return (
    <Modal open={open} onClose={onClose} title="Add trigger category" width={620}>
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
          {steps.map((s, i) => (
            <div key={s.n} className="flex items-center gap-2 flex-1">
              <div
                className={`w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 ${
                  step === s.n ? 'bg-carbon-moss text-white' : step > s.n ? 'bg-teal text-white' : 'bg-surface-tertiary text-text-faint'
                }`}
              >
                {step > s.n ? <Icon name="check" size={12} strokeWidth={3} /> : s.n}
              </div>
              <span className={`text-[11px] font-medium ${step === s.n ? 'text-text-primary' : 'text-text-faint'}`}>{s.label}</span>
              {i < steps.length - 1 && <div className="flex-1 h-px bg-border-hairline" />}
            </div>
          ))}
        </div>

        {step === 1 && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-[12.5px] font-semibold text-[#15393e]">Name</label>
              <Input value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} placeholder="e.g. Referral Partnership" />
              {nameTaken && <span className="text-[11.5px] text-danger">A trigger category with this name already exists.</span>}
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[12.5px] font-semibold text-[#15393e]">Description</label>
              <p className="text-[11.5px] text-text-faint -mt-0.5">This is what the PO sees on the Browse all categories modal - write it for them.</p>
              <Textarea rows={3} value={draft.description} onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))} placeholder="What kind of change does this cover?" />
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-2">
            <label className="text-[12.5px] font-semibold text-[#15393e]">Field pack</label>
            <p className="text-[11.5px] text-text-faint -mt-1">
              Fields added on top of the core set when this trigger is selected. May be left empty - the trigger then resolves to the core set alone.
            </p>
            <div className="flex flex-col gap-1 max-h-[280px] overflow-y-auto border border-border-hairline rounded-md p-2">
              {activeLibrary.map((f) => (
                <Checkbox
                  key={f.id}
                  checked={draft.fieldPackKeys.includes(f.id)}
                  onChange={(v) =>
                    setDraft((d) => ({ ...d, fieldPackKeys: v ? [...d.fieldPackKeys, f.id] : d.fieldPackKeys.filter((k) => k !== f.id) }))
                  }
                  label={<span className="text-[12.5px]">{f.label}</span>}
                />
              ))}
            </div>
            {draft.fieldPackKeys.length === 0 && (
              <p className="text-[12px] text-teal bg-[#F4F8F7] rounded-md px-3 py-2">No fields selected - this trigger will resolve to the core set alone.</p>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="flex flex-col gap-2">
            <label className="text-[12.5px] font-semibold text-[#15393e]">AML category mapping</label>
            <p className="text-[11.5px] text-text-faint -mt-1">
              Required - without it, a case tagged with this trigger has no risk category in scope and nothing for the scoring model to compute.
            </p>
            <Select value={draft.amlCategory} onChange={(e) => setDraft((d) => ({ ...d, amlCategory: e.target.value }))}>
              <option value="">Select an AML category...</option>
              {ALL_AML_CATEGORIES.map((aml) => (
                <option key={aml} value={aml}>
                  {aml}
                </option>
              ))}
            </Select>
          </div>
        )}

        {step === 4 && (
          <div className="flex flex-col gap-2">
            <label className="text-[12.5px] font-semibold text-[#15393e]">Classification signals</label>
            <p className="text-[11.5px] text-text-faint -mt-1">
              What the Classification Agent looks for when proposing this tag. Required - without at least one, this trigger can only ever be applied manually by a PO who already knows it exists.
            </p>
            <div className="flex flex-col gap-2">
              {draft.classificationSignals.map((s, i) => (
                <div key={i} className="flex items-start gap-2">
                  <Textarea rows={2} value={s} onChange={(e) => setSignal(i, e.target.value)} placeholder="What in a submission suggests this trigger applies?" className="flex-1" />
                  {draft.classificationSignals.length > 1 && (
                    <button onClick={() => removeSignal(i)} className="text-text-faint hover:text-danger cursor-pointer p-1.5 mt-1">
                      <Icon name="trash" size={14} />
                    </button>
                  )}
                </div>
              ))}
              <Button size="sm" variant="ghost" icon={<Icon name="plus" size={13} />} onClick={addSignal} className="self-start">
                Add another signal
              </Button>
            </div>
          </div>
        )}

        {step === 5 && (
          <div className="flex flex-col gap-3">
            <span className="text-[12.5px] font-semibold text-text-primary">Review before creating</span>
            <div className="flex flex-col gap-2.5 text-[12.5px]">
              <div>
                <span className="text-text-faint">Name</span>
                <p className="text-text-primary font-semibold">{draft.name}</p>
              </div>
              <div>
                <span className="text-text-faint">Description</span>
                <p className="text-text-primary">{draft.description}</p>
              </div>
              <div>
                <span className="text-text-faint">Field pack</span>
                <p className="text-text-primary">
                  {draft.fieldPackKeys.length ? `${draft.fieldPackKeys.length} field(s): ${draft.fieldPackKeys.map((k) => activeLibrary.find((f) => f.id === k)?.label ?? k).join(', ')}` : 'None - resolves to the core set alone'}
                </p>
              </div>
              <div>
                <span className="text-text-faint">AML category</span>
                <p className="text-text-primary">{draft.amlCategory}</p>
              </div>
              <div>
                <span className="text-text-faint">Classification signals</span>
                <ul className="list-disc pl-4 text-text-primary">
                  {cleanSignals.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            </div>
            <p className="text-[11.5px] text-text-faint">Creating this adds it to your working set - it still goes through impact preview before publishing, same as any other edit here.</p>
          </div>
        )}

        <div className="flex justify-between gap-2 pt-2 border-t border-border-hairline">
          <Button variant="ghost" onClick={() => (step === 1 ? onClose() : setStep((s) => s - 1))}>
            {step === 1 ? 'Cancel' : 'Back'}
          </Button>
          {step < 5 ? (
            <Button variant="primary" disabled={!canAdvanceFrom(step)} onClick={() => setStep((s) => s + 1)}>
              Next
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={() => {
                onCreate({ ...draft, name: draft.name.trim(), description: draft.description.trim(), classificationSignals: cleanSignals });
                onClose();
              }}
            >
              Create trigger category
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

// -----------------------------------------------------------------------
// Edit trigger category - all five attributes in one form, same working-set/impact-preview
// gate on save as everything else on this page.
// -----------------------------------------------------------------------
function EditTriggerModal({
  target,
  onClose,
  activeLibrary,
  packKeys,
  onSave,
}: {
  target: TriggerCategoryDef | null;
  onClose: () => void;
  activeLibrary: RequiredFieldDef[];
  packKeys: Record<TriggerCategory, string[]>;
  onSave: (key: string, patch: { description: string; amlCategory: string; classificationSignals: string[]; fieldPackKeys: string[] }) => void;
}) {
  const [description, setDescription] = useState('');
  const [amlCategory, setAmlCategory] = useState('');
  const [signals, setSignals] = useState<string[]>(['']);
  const [packSel, setPackSel] = useState<string[]>([]);

  useEffect(() => {
    if (target) {
      setDescription(target.description);
      setAmlCategory(target.amlCategory);
      setSignals(target.classificationSignals.length ? target.classificationSignals : ['']);
      setPackSel(packKeys[target.key] ?? []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  if (!target) return null;
  const cleanSignals = signals.map((s) => s.trim()).filter(Boolean);
  const valid = description.trim().length > 0 && !!amlCategory && cleanSignals.length > 0;

  return (
    <Modal open={!!target} onClose={onClose} title={`Edit ${target.key}`} width={620}>
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-[12.5px] font-semibold text-[#15393e]">Description</label>
          <Textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-[12.5px] font-semibold text-[#15393e]">Field pack</label>
          <div className="flex flex-col gap-1 max-h-[220px] overflow-y-auto border border-border-hairline rounded-md p-2">
            {activeLibrary.map((f) => (
              <Checkbox
                key={f.id}
                checked={packSel.includes(f.id)}
                onChange={(v) => setPackSel((p) => (v ? [...p, f.id] : p.filter((k) => k !== f.id)))}
                label={<span className="text-[12.5px]">{f.label}</span>}
              />
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-[12.5px] font-semibold text-[#15393e]">AML category mapping</label>
          <p className="text-[11.5px] text-text-faint -mt-0.5">Required - a trigger category cannot be saved without one.</p>
          <Select value={amlCategory} onChange={(e) => setAmlCategory(e.target.value)}>
            <option value="">Select an AML category...</option>
            {ALL_AML_CATEGORIES.map((aml) => (
              <option key={aml} value={aml}>
                {aml}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-[12.5px] font-semibold text-[#15393e]">Classification signals</label>
          <div className="flex flex-col gap-2">
            {signals.map((s, i) => (
              <div key={i} className="flex items-start gap-2">
                <Textarea rows={2} value={s} onChange={(e) => setSignals((arr) => arr.map((x, idx) => (idx === i ? e.target.value : x)))} className="flex-1" />
                {signals.length > 1 && (
                  <button onClick={() => setSignals((arr) => arr.filter((_, idx) => idx !== i))} className="text-text-faint hover:text-danger cursor-pointer p-1.5 mt-1">
                    <Icon name="trash" size={14} />
                  </button>
                )}
              </div>
            ))}
            <Button size="sm" variant="ghost" icon={<Icon name="plus" size={13} />} onClick={() => setSignals((arr) => [...arr, ''])} className="self-start">
              Add another signal
            </Button>
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!valid} onClick={() => onSave(target.key, { description: description.trim(), amlCategory, classificationSignals: cleanSignals, fieldPackKeys: packSel })}>
            Save
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function CategoriesFieldsSection() {
  const { addChange, changes, registerActions, clearSection } = useWorkingSet();
  const myChanges = changes.filter((c) => c.sectionId === SECTION_ID);

  const [snapshot, setSnapshot] = useState<FieldSetSnapshot | null>(null);
  const [versions, setVersions] = useState<FieldSetVersion[] | null>(null);
  const [poAdditions, setPoAdditions] = useState<{ category: TriggerCategory; fields: OptionalFieldDef[] }[] | null>(null);

  const [library, setLibrary] = useState<RequiredFieldDef[]>([]);
  const [coreKeys, setCoreKeys] = useState<string[]>([]);
  const [packKeys, setPackKeys] = useState<Record<TriggerCategory, string[]>>({} as Record<TriggerCategory, string[]>);
  const [threshold, setThreshold] = useState(80);
  const [triggerMapping, setTriggerMapping] = useState<Record<TriggerCategory, string>>({} as Record<TriggerCategory, string>);
  const [triggerCategories, setTriggerCategories] = useState<TriggerCategoryDef[]>([]);

  const [thresholdEditing, setThresholdEditing] = useState(false);
  const [thresholdDraft, setThresholdDraft] = useState(80);

  const [editingFieldKey, setEditingFieldKey] = useState<string | null>(null);
  const [retireFieldTarget, setRetireFieldTarget] = useState<string | null>(null);

  const [addTriggerOpen, setAddTriggerOpen] = useState(false);
  const [editTriggerTarget, setEditTriggerTarget] = useState<TriggerCategoryDef | null>(null);
  const [removeTriggerTarget, setRemoveTriggerTarget] = useState<{ def: TriggerCategoryDef; inUse: boolean } | null>(null);

  const [highlightedField, setHighlightedField] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [previewCategories, setPreviewCategories] = useState<TriggerCategory[]>([]);
  const [impact, setImpact] = useState<FieldSetImpact | null>(null);
  const [impactOpen, setImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const toast = useToast();

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  function toggleSection(id: string) {
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function expandSection(id: string) {
    setExpanded((s) => (s.has(id) ? s : new Set(s).add(id)));
  }

  async function reload() {
    const [snap, vers, po] = await Promise.all([getFieldSet(), getFieldSetVersions(), getPoOptionalFieldAdditions()]);
    setSnapshot(snap);
    setVersions(vers);
    setPoAdditions(po);
    setLibrary(snap.fieldLibrary);
    setCoreKeys(snap.coreSetKeys);
    setPackKeys(snap.categoryPackKeys);
    setThreshold(snap.completionThreshold);
    setTriggerMapping(snap.triggerToAmlCategory);
    setTriggerCategories(snap.triggerCategories);
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function candidate(): FieldSetCandidate {
    return { fieldLibrary: library, coreSetKeys: coreKeys, categoryPackKeys: packKeys, completionThreshold: threshold, triggerToAmlCategory: triggerMapping, triggerCategories };
  }

  async function handleReviewImpact() {
    setReviewing(true);
    const result = await previewFieldSetImpact(candidate());
    setReviewing(false);
    setImpact(result);
    setImpactOpen(true);
  }

  async function handlePublish() {
    setPublishing(true);
    await commitFieldSetChange(candidate(), note.trim() || 'Field library, core set, pack or trigger category update.');
    setPublishing(false);
    setImpactOpen(false);
    setNote('');
    clearSection(SECTION_ID);
    toast('New field-set version published. In-flight drafts stay on their starting version.', 'success');
    reload();
  }

  function handleDiscard() {
    if (!snapshot) return;
    setLibrary(snapshot.fieldLibrary);
    setCoreKeys(snapshot.coreSetKeys);
    setPackKeys(snapshot.categoryPackKeys);
    setThreshold(snapshot.completionThreshold);
    setTriggerMapping(snapshot.triggerToAmlCategory);
    setTriggerCategories(snapshot.triggerCategories);
    clearSection(SECTION_ID);
  }

  // The shell-level working-set banner drives these two - registered every render so it always
  // closes over the latest state (cheap: just replacing a map entry, not a subscription).
  useEffect(() => {
    registerActions(SECTION_ID, { publish: handleReviewImpact, discard: handleDiscard });
  });

  if (!snapshot || !versions || !poAdditions) return <Skeleton className="h-96 rounded-md" />;

  const libByKey = new Map(library.map((f) => [f.id, f]));
  const activeLibrary = library.filter((f) => f.status !== 'retired');
  const coreFields = coreKeys.map((k) => libByKey.get(k)).filter((f): f is RequiredFieldDef => !!f);
  const activeTriggers = triggerCategories.filter((t) => t.status === 'active');
  const retiredTriggers = triggerCategories.filter((t) => t.status === 'retired');
  const activeTriggerKeys = activeTriggers.map((t) => t.key);

  function fieldPacks(key: string): string[] {
    const packs = activeTriggerKeys.filter((cat) => (packKeys[cat] ?? []).includes(key));
    const inCore = coreKeys.includes(key);
    return [...(inCore ? ['Core'] : []), ...packs];
  }

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }

  function addToCore(key: string) {
    if (coreKeys.includes(key)) return;
    const f = libByKey.get(key);
    setCoreKeys((k) => [...k, key]);
    log(`Added "${f?.label ?? key}" to core set`, () => setCoreKeys((k) => k.filter((x) => x !== key)));
  }

  // §2.1 / §2.2 - some removals are structurally invalid, not just reviewable: refused at the
  // click with the reason stated inline, never reaching the working set or the preview.
  function removeFromCore(key: string) {
    const f = libByKey.get(key);
    if (key === 'trigger_categories') {
      toast('Trigger categories cannot be removed. It determines which category packs apply to a submission, so every case depends on it.', 'danger');
      return;
    }
    if (f?.neverBlank) {
      toast(`"${f.label}" is always-required. Clear that flag first (Edit it in the field library), then remove it.`, 'danger');
      return;
    }
    setCoreKeys((k) => k.filter((x) => x !== key));
    log(`Removed "${f?.label ?? key}" from core set`, () => setCoreKeys((k) => [...k, key]));
  }

  function addToPack(cat: TriggerCategory, key: string) {
    if (coreKeys.includes(key)) {
      toast(`"${libByKey.get(key)?.label}" is already in the core set - it's required on every submission already, adding it to a pack would be redundant.`, 'default');
      return;
    }
    const f = libByKey.get(key);
    setPackKeys((p) => ({ ...p, [cat]: [...new Set([...(p[cat] ?? []), key])] }));
    log(`Added "${f?.label ?? key}" to ${cat} pack`, () => setPackKeys((p) => ({ ...p, [cat]: (p[cat] ?? []).filter((x) => x !== key) })));
  }

  function removeFromPack(cat: TriggerCategory, key: string) {
    const f = libByKey.get(key);
    if (f?.neverBlank) {
      toast(`"${f.label}" is always-required. Clear that flag first (Edit it in the field library), then remove it from ${cat}.`, 'danger');
      return;
    }
    setPackKeys((p) => ({ ...p, [cat]: (p[cat] ?? []).filter((x) => x !== key) }));
    log(`Removed "${f?.label ?? key}" from ${cat} pack`, () => setPackKeys((p) => ({ ...p, [cat]: [...(p[cat] ?? []), key] })));
  }

  function confirmRetireField() {
    if (!retireFieldTarget) return;
    const key = retireFieldTarget;
    const label = libByKey.get(key)?.label ?? key;
    const prevCore = coreKeys;
    const prevPacks = packKeys;
    setLibrary((l) => l.map((f) => (f.id === key ? { ...f, status: 'retired' as const } : f)));
    setCoreKeys((k) => k.filter((x) => x !== key));
    setPackKeys((p) => Object.fromEntries(activeTriggerKeys.map((cat) => [cat, (p[cat] ?? []).filter((x) => x !== key)])) as Record<TriggerCategory, string[]>);
    log(`Retired "${label}" from the field library`, () => {
      setLibrary((l) => l.map((f) => (f.id === key ? { ...f, status: 'active' as const } : f)));
      setCoreKeys(prevCore);
      setPackKeys(prevPacks);
    });
    setRetireFieldTarget(null);
  }

  function confirmFieldEdit(key: string, patch: { label: string; type: NonNullable<RequiredFieldDef['type']>; neverBlank: boolean }) {
    const before = libByKey.get(key);
    if (!before) return;
    setLibrary((l) => l.map((f) => (f.id === key ? { ...f, ...patch } : f)));
    if (before.label !== patch.label || before.type !== patch.type || !!before.neverBlank !== patch.neverBlank) {
      log(`Edited "${before.label}" in the field library`, () => setLibrary((l) => l.map((f) => (f.id === key ? before : f))));
    }
    setEditingFieldKey(null);
  }

  function scrollToField(key: string) {
    expandSection('library');
    setHighlightedField(key);
    setTimeout(() => document.getElementById(`field-row-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlightedField(null), 2200);
  }

  function handleConfirmThreshold() {
    const prev = threshold;
    setThreshold(thresholdDraft);
    setThresholdEditing(false);
    if (prev !== thresholdDraft) log(`Threshold ${prev}% → ${thresholdDraft}%`, () => setThreshold(prev));
  }

  function createTrigger(draft: TriggerDraft) {
    setTriggerCategories((t) => [...t, { key: draft.name, description: draft.description, amlCategory: draft.amlCategory, classificationSignals: draft.classificationSignals, status: 'active' }]);
    setPackKeys((p) => ({ ...p, [draft.name]: draft.fieldPackKeys }));
    setTriggerMapping((m) => ({ ...m, [draft.name]: draft.amlCategory }));
    log(`Added trigger category "${draft.name}"`, () => {
      setTriggerCategories((t) => t.filter((x) => x.key !== draft.name));
      setPackKeys((p) => { const n = { ...p }; delete n[draft.name]; return n; });
      setTriggerMapping((m) => { const n = { ...m }; delete n[draft.name]; return n; });
    });
  }

  function saveTriggerEdit(key: string, patch: { description: string; amlCategory: string; classificationSignals: string[]; fieldPackKeys: string[] }) {
    const beforeDef = triggerCategories.find((t) => t.key === key);
    const beforePack = packKeys[key] ?? [];
    const beforeMapping = triggerMapping[key];
    setTriggerCategories((t) => t.map((x) => (x.key === key ? { ...x, description: patch.description, amlCategory: patch.amlCategory, classificationSignals: patch.classificationSignals } : x)));
    setPackKeys((p) => ({ ...p, [key]: patch.fieldPackKeys }));
    setTriggerMapping((m) => ({ ...m, [key]: patch.amlCategory }));
    log(`Edited trigger category "${key}"`, () => {
      if (beforeDef) setTriggerCategories((t) => t.map((x) => (x.key === key ? beforeDef : x)));
      setPackKeys((p) => ({ ...p, [key]: beforePack }));
      setTriggerMapping((m) => ({ ...m, [key]: beforeMapping }));
    });
    setEditTriggerTarget(null);
  }

  async function openRemoveTrigger(def: TriggerCategoryDef) {
    const inUse = await isTriggerCategoryInUse(def.key);
    setRemoveTriggerTarget({ def, inUse });
  }

  function confirmRemoveTrigger() {
    if (!removeTriggerTarget) return;
    const { def, inUse } = removeTriggerTarget;
    if (inUse) {
      setTriggerCategories((t) => t.map((x) => (x.key === def.key ? { ...x, status: 'retired' as const } : x)));
      log(`Retired trigger category "${def.key}"`, () => setTriggerCategories((t) => t.map((x) => (x.key === def.key ? { ...x, status: 'active' as const } : x))));
    } else {
      const prevPack = packKeys[def.key];
      const prevMapping = triggerMapping[def.key];
      setTriggerCategories((t) => t.filter((x) => x.key !== def.key));
      setPackKeys((p) => { const n = { ...p }; delete n[def.key]; return n; });
      setTriggerMapping((m) => { const n = { ...m }; delete n[def.key]; return n; });
      log(`Deleted trigger category "${def.key}"`, () => {
        setTriggerCategories((t) => [...t, def]);
        setPackKeys((p) => ({ ...p, [def.key]: prevPack }));
        setTriggerMapping((m) => ({ ...m, [def.key]: prevMapping }));
      });
    }
    setRemoveTriggerTarget(null);
  }

  function remapAml(cat: TriggerCategory, next: string) {
    const prev = triggerMapping[cat];
    if (prev === next) return;
    setTriggerMapping((m) => ({ ...m, [cat]: next }));
    log(`Remapped "${cat}" to ${next}`, () => setTriggerMapping((m) => ({ ...m, [cat]: prev })));
  }

  // Resolved-set preview helpers.
  const previewResolved = mergeLocal(library, coreKeys, packKeys, previewCategories);
  const previewCoreCount = previewResolved.filter((f) => coreKeys.includes(f.id)).length;
  const previewPackFields = previewResolved.filter((f) => !coreKeys.includes(f.id));
  function contributingSelectedCats(fieldId: string): TriggerCategory[] {
    return previewCategories.filter((cat) => (packKeys[cat] ?? []).includes(fieldId));
  }
  const previewSharedCount = previewPackFields.filter((f) => contributingSelectedCats(f.id).length > 1).length;
  function toneForField(fieldId: string): { bg: string; text: string } {
    if (coreKeys.includes(fieldId)) return CORE_TONE;
    const contributors = contributingSelectedCats(fieldId);
    const firstCat = contributors[0];
    const idx = previewCategories.indexOf(firstCat);
    return CATEGORY_TONE_PALETTE[idx % CATEGORY_TONE_PALETTE.length] ?? CORE_TONE;
  }

  return (
    <div className="flex flex-col gap-4">
      <Card material="flat" className="overflow-hidden">
        <div className="p-5 flex flex-col gap-3">
          <span className="text-sm font-bold text-text-primary">Global completion threshold</span>
          <div className="flex items-center gap-4 max-w-md">
          <p className="text-[12.5px] text-text-muted flex-1">One threshold, applies to every submission regardless of category.</p>
          {!thresholdEditing ? (
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-[22px] font-bold text-text-primary">{threshold}%</span>
              <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={() => { setThresholdDraft(threshold); setThresholdEditing(true); }}>
                Edit
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 shrink-0">
              <Input type="number" min={0} max={100} value={thresholdDraft} onChange={(e) => setThresholdDraft(Number(e.target.value))} className="w-20 text-center" autoFocus />
              <span className="text-[14px] font-semibold text-text-primary">%</span>
              <Button size="sm" variant="ghost" onClick={() => setThresholdEditing(false)}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" onClick={handleConfirmThreshold}>
                Confirm
              </Button>
            </div>
          )}
          </div>
        </div>
      </Card>

      <Section id="core" title="Core set" open={expanded.has('core')} onToggle={toggleSection} meta={<span className="text-[12px] text-text-faint">{coreFields.length} fields</span>}>
        <span className="text-[12px] text-text-faint -mt-2">Required on every submission</span>
        <div className="flex flex-col gap-1">
          {coreFields.map((f) => (
            <div key={f.id} className="flex items-center gap-2.5 px-3 py-2 rounded-md hover:bg-surface-tertiary group">
              <button onClick={() => scrollToField(f.id)} className="text-[13px] text-text-primary flex-1 text-left cursor-pointer hover:underline hover:text-carbon-moss">
                {f.label}
              </button>
              {f.neverBlank && (
                <button onClick={() => scrollToField(f.id)} className="cursor-pointer">
                  <Badge tone="danger">Always required</Badge>
                </button>
              )}
              <span className="text-[11px] font-mono text-text-faint">{f.id}</span>
              <button onClick={() => removeFromCore(f.id)} className="opacity-0 group-hover:opacity-100 text-text-faint hover:text-danger cursor-pointer p-1">
                <Icon name="close" size={13} />
              </button>
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2 pt-2 border-t border-border-hairline">
          <Select
            className="flex-1"
            value=""
            onChange={(e) => {
              if (e.target.value) addToCore(e.target.value);
            }}
          >
            <option value="">Add a library field to the core set...</option>
            {activeLibrary
              .filter((f) => !coreKeys.includes(f.id))
              .map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
          </Select>
        </div>
      </Section>

      <Section
        id="triggers"
        title="Trigger categories"
        open={expanded.has('triggers')}
        onToggle={toggleSection}
        meta={<span className="text-[12px] text-text-faint">{activeTriggers.length} active</span>}
      >
        <div className="flex items-center justify-between -mt-2">
          <p className="text-[12px] text-text-faint">FCRM-owned and editable - a new trigger is a legitimate FCRM action.</p>
          <Button size="sm" variant="secondary" icon={<Icon name="plus" size={14} />} onClick={() => setAddTriggerOpen(true)}>
            Add trigger category
          </Button>
        </div>
        <div className="flex flex-col gap-2">
          {activeTriggers.map((t) => (
            <div key={t.key} className="border border-border-strong rounded-lg p-3.5 flex flex-col gap-1.5 bg-white">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[13.5px] font-semibold text-text-primary">{t.key}</span>
                <div className="flex items-center gap-1.5 border border-border-strong rounded-md p-1">
                  <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={() => setEditTriggerTarget(t)}>
                    Edit
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => openRemoveTrigger(t)}>
                    Remove
                  </Button>
                </div>
              </div>
              <p className="text-[12.5px] text-text-secondary">{t.description}</p>
              <div className="flex items-center gap-2 flex-wrap text-[11.5px] text-text-faint">
                <span>Maps to {t.amlCategory}</span>
                <span>·</span>
                <span>{(packKeys[t.key] ?? []).length} pack field(s)</span>
                <span>·</span>
                <span>{t.classificationSignals.length} classification signal(s)</span>
              </div>
            </div>
          ))}
        </div>
        {retiredTriggers.length > 0 && (
          <div className="flex flex-col gap-1 pt-2 border-t border-border-hairline">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Retired - still applies to cases that used it</span>
            {retiredTriggers.map((t) => (
              <div key={t.key} className="flex items-center gap-2 px-1 py-1 opacity-60">
                <span className="text-[12.5px] text-text-primary">{t.key}</span>
                <Badge tone="default">Retired</Badge>
              </div>
            ))}
          </div>
        )}
      </Section>

      {activeTriggers.map((t) => {
        const cat = t.key;
        const fields = (packKeys[cat] ?? []).map((k) => libByKey.get(k)).filter((f): f is RequiredFieldDef => !!f);
        return (
          <Section
            key={cat}
            id={`pack-${cat}`}
            title={`Category pack - ${cat}`}
            open={expanded.has(`pack-${cat}`)}
            onToggle={toggleSection}
            meta={<span className="text-[12px] text-text-faint">{fields.length} additional · {coreFields.length + fields.length} total</span>}
          >
            <div className="flex flex-col gap-1">
              {fields.map((f) => (
                <div key={f.id} className="flex items-center gap-2.5 px-3 py-2 rounded-md hover:bg-surface-tertiary group">
                  <button onClick={() => scrollToField(f.id)} className="text-[13px] text-text-primary flex-1 text-left cursor-pointer hover:underline hover:text-carbon-moss">
                    {f.label}
                  </button>
                  {f.neverBlank && (
                    <button onClick={() => scrollToField(f.id)} className="cursor-pointer">
                      <Badge tone="danger">Always required</Badge>
                    </button>
                  )}
                  {fieldPacks(f.id).length > 1 && <span className="text-[10.5px] text-text-faint italic">shared with {fieldPacks(f.id).filter((p) => p !== cat).join(', ')}</span>}
                  <span className="text-[11px] font-mono text-text-faint">{f.id}</span>
                  <button onClick={() => removeFromPack(cat, f.id)} className="opacity-0 group-hover:opacity-100 text-text-faint hover:text-danger cursor-pointer p-1">
                    <Icon name="close" size={13} />
                  </button>
                </div>
              ))}
              {fields.length === 0 && <p className="text-[12.5px] text-text-faint px-3 py-1">No additional fields in this pack.</p>}
            </div>
            <Select
              value=""
              onChange={(e) => {
                if (e.target.value) addToPack(cat, e.target.value);
              }}
            >
              <option value="">Add a library field to this pack...</option>
              {activeLibrary
                .filter((f) => !(packKeys[cat] ?? []).includes(f.id))
                .map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
            </Select>
          </Section>
        );
      })}

      <Section
        id="library"
        title="Field library"
        open={expanded.has('library')}
        onToggle={toggleSection}
        meta={<span className="text-[12px] text-text-faint">{library.length} fields</span>}
      >
        <div className="flex justify-end">
          <AddFieldToLibraryForm existingKeys={library.map((f) => f.id)} onAdd={(f) => { setLibrary((l) => [...l, f]); log(`Added "${f.label}" to the field library`, () => setLibrary((l) => l.filter((x) => x.id !== f.id))); }} />
        </div>
        <div className="grid grid-cols-[1.4fr_170px_100px_1.4fr_120px_auto] gap-3 px-2 py-2 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">
          <span>Label</span>
          <span>Key</span>
          <span>Type</span>
          <span>Referenced by</span>
          <span>Always required</span>
          <span />
        </div>
        <div className="flex flex-col divide-y divide-border-hairline">
          {library.map((f) => (
            <FieldLibraryRow
              key={f.id}
              f={f}
              referencedByLabel={fieldPacks(f.id).join(', ') || 'Not yet referenced'}
              editing={editingFieldKey === f.id}
              highlighted={highlightedField === f.id}
              onStartEdit={() => setEditingFieldKey(f.id)}
              onCancelEdit={() => setEditingFieldKey(null)}
              onConfirmEdit={(patch) => confirmFieldEdit(f.id, patch)}
              onRetire={() => setRetireFieldTarget(f.id)}
            />
          ))}
        </div>
      </Section>

      <Section id="mapping" title="Trigger-to-AML-category mapping" open={expanded.has('mapping')} onToggle={toggleSection}>
        <p className="text-[12.5px] text-text-secondary -mt-1">
          The four AML risk categories themselves - customer/entity, product and service, geography, delivery channel and third party - come from the{' '}
          <strong className="text-text-primary">Federal Financial Institutions Examination Council (FFIEC)</strong>'s{' '}
          <a href={FFIEC_MANUAL_URL} target="_blank" rel="noopener noreferrer" className="underline text-carbon-moss hover:text-carbon-moss-hover">
            BSA/AML Examination Manual
          </a>{' '}
          and are not editable here. Each trigger category still maps to exactly one of them.
        </p>
        <div className="flex flex-col gap-2">
          {activeTriggers.map((t) => (
            <div key={t.key} className="flex items-center gap-3">
              <span className="text-[13px] text-text-primary flex-1 flex items-center gap-1.5">
                {t.key}
                {t.key === 'Process Change' && (
                  <span
                    title="Reviewed: an internal process change with no external party involved isn't a third-party exposure, but the four FFIEC categories offer no closer fit - kept as Delivery channel and third party. Recorded as a finding, not a default left unexamined."
                    className="cursor-help text-text-faint"
                  >
                    <Icon name="info" size={13} />
                  </span>
                )}
              </span>
              <Icon name="arrow-right" size={13} className="text-text-faint" />
              <Select value={triggerMapping[t.key] ?? ''} onChange={(e) => remapAml(t.key, e.target.value)} className="w-[260px]">
                {ALL_AML_CATEGORIES.map((aml) => (
                  <option key={aml} value={aml}>
                    {aml}
                  </option>
                ))}
              </Select>
            </div>
          ))}
        </div>
      </Section>

      <Section id="preview" title="Resolved-set preview" open={expanded.has('preview')} onToggle={toggleSection}>
        <p className="text-[12.5px] text-text-muted -mt-1">Pick any category combination to see the resulting required-field list and count.</p>
        <div className="flex flex-wrap gap-2">
          {activeTriggerKeys.map((cat) => (
            <button
              key={cat}
              onClick={() => setPreviewCategories((c) => (c.includes(cat) ? c.filter((x) => x !== cat) : [...c, cat]))}
              className={`text-[12.5px] font-medium px-3 py-1.5 rounded-full cursor-pointer transition-colors ${
                previewCategories.includes(cat) ? 'bg-carbon-moss text-white' : 'bg-surface-tertiary text-text-secondary hover:bg-ink-5'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
        {previewCategories.length > 0 && (
          <div className="flex flex-col gap-2 pt-2 border-t border-border-hairline">
            <span className="text-[12.5px] font-semibold text-text-primary">
              {previewResolved.length} required fields · {previewCoreCount} core + {previewPackFields.length} pack field{previewPackFields.length === 1 ? '' : 's'} · {previewSharedCount} field
              {previewSharedCount === 1 ? '' : 's'} shared across packs
            </span>
            <div className="flex flex-wrap gap-1.5">
              {previewResolved.map((f) => {
                const tone = toneForField(f.id);
                const shared = !coreKeys.includes(f.id) && contributingSelectedCats(f.id).length > 1;
                const sourceLabel = coreKeys.includes(f.id) ? 'Core' : contributingSelectedCats(f.id)[0] ?? '';
                return (
                  <button
                    key={f.id}
                    onClick={() => scrollToField(f.id)}
                    title={shared ? `Also in: ${contributingSelectedCats(f.id).join(', ')}` : sourceLabel}
                    className="text-[11.5px] px-2 py-1 rounded flex items-center gap-1 cursor-pointer hover:opacity-85"
                    style={{ background: tone.bg, color: tone.text }}
                  >
                    {shared && <Icon name="grip" size={10} />}
                    {f.label}
                    <span className="opacity-75 text-[10px]">· {sourceLabel}</span>
                  </button>
                );
              })}
            </div>
            <div className="flex flex-wrap gap-3 pt-1">
              <span className="flex items-center gap-1.5 text-[11px] text-text-secondary">
                <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: CORE_TONE.bg }} />
                Core
              </span>
              {previewCategories.map((cat, i) => (
                <span key={cat} className="flex items-center gap-1.5 text-[11px] text-text-secondary">
                  <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: CATEGORY_TONE_PALETTE[i % CATEGORY_TONE_PALETTE.length].bg }} />
                  {cat}
                </span>
              ))}
            </div>
          </div>
        )}
      </Section>

      <Section id="po" title="PO optional field additions" open={expanded.has('po')} onToggle={toggleSection}>
        <p className="text-[12.5px] text-text-muted -mt-1">Read-only - the PO controls these from their own Templates page.</p>
        <div className="grid grid-cols-2 gap-4">
          {poAdditions
            .filter((a) => a.fields.length > 0)
            .map((a) => (
              <div key={a.category} className="flex flex-col gap-1.5">
                <span className="text-[13px] font-bold text-text-primary pb-1 border-b border-border-hairline">{a.category}</span>
                {a.fields.map((f) => (
                  <span key={f.id} className="text-[12.5px] text-text-secondary">
                    {f.label}
                  </span>
                ))}
              </div>
            ))}
          {poAdditions.every((a) => a.fields.length === 0) && <p className="text-[13px] text-text-faint">No PO-added optional fields yet.</p>}
        </div>
      </Section>

      <Section id="history" title="Version history" open={expanded.has('history')} onToggle={toggleSection} meta={<span className="text-[12px] text-text-faint">{versions.length} versions</span>}>
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
      </Section>

      <AddTriggerWizard
        open={addTriggerOpen}
        onClose={() => setAddTriggerOpen(false)}
        activeLibrary={activeLibrary}
        existingKeys={triggerCategories.map((t) => t.key)}
        onCreate={createTrigger}
      />

      <EditTriggerModal target={editTriggerTarget} onClose={() => setEditTriggerTarget(null)} activeLibrary={activeLibrary} packKeys={packKeys} onSave={saveTriggerEdit} />

      <Modal open={!!retireFieldTarget} onClose={() => setRetireFieldTarget(null)} title="Retire field" width={460}>
        <div className="flex flex-col gap-4">
          <p className="text-[13px] text-text-secondary">
            Retiring removes this field from new packs. Cases that already use it keep it, and it stays visible in their records and in Assurance queries.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRetireFieldTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmRetireField}>
              Retire field
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={!!removeTriggerTarget} onClose={() => setRemoveTriggerTarget(null)} title={removeTriggerTarget?.inUse ? 'Retire trigger category' : 'Delete trigger category'} width={480}>
        {removeTriggerTarget && (
          <div className="flex flex-col gap-4">
            {removeTriggerTarget.inUse ? (
              <p className="text-[13px] text-text-secondary">
                "{removeTriggerTarget.def.key}" is used by at least one existing case. Retiring makes it unavailable for new submissions and removes it from the PO's Browse all categories
                modal. It still renders on cases that already used it, including in Assurance queries.
              </p>
            ) : (
              <p className="text-[13px] text-text-secondary">
                "{removeTriggerTarget.def.key}" has never been used by any case, so it can be deleted outright rather than retired. This cannot be undone.
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setRemoveTriggerTarget(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={confirmRemoveTrigger}>
                {removeTriggerTarget.inUse ? 'Retire' : 'Delete'}
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
                Reflects the full working set - {myChanges.length} pending change{myChanges.length === 1 ? '' : 's'} - not just the edit that opened this.
              </p>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Packs affected</span>
              {impact.affectedPacks.length === 0 ? (
                <p className="text-[13px] text-teal font-medium">None - no trigger category's resolved field set changes.</p>
              ) : (
                <p className="text-[13px] text-text-primary">{impact.affectedPacks.join(', ')}</p>
              )}
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Resolved count per category</span>
              <div className="grid grid-cols-2 gap-1.5">
                {impact.resolvedCounts.map((r) => (
                  <div key={r.category} className="flex items-center justify-between px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
                    <span className="text-text-secondary">{r.category}</span>
                    <span className={`font-mono font-semibold ${r.before === r.after ? 'text-text-primary' : r.after > r.before ? 'text-danger' : 'text-teal'}`}>
                      {r.before} → {r.after}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">In-flight PO drafts affected</span>
              {impact.affectedDrafts.length === 0 ? (
                <p className="text-[13px] text-teal font-medium">None - no in-flight draft's resolved set or completion percentage changes.</p>
              ) : (
                impact.affectedDrafts.map((d) => (
                  <div key={d.caseId} className="flex items-center justify-between px-3 py-2 rounded-md bg-danger-bg text-[12.5px]">
                    <span className="text-text-primary">
                      {d.caseId} - {d.title}
                    </span>
                    <span className="font-mono font-semibold text-danger">
                      {d.beforePct}% → {d.afterPct}%
                    </span>
                  </div>
                ))
              )}
            </div>

            <div className="flex items-center justify-between px-3 py-2 rounded-md bg-verdant-1 text-[12.5px]">
              <span className="text-text-secondary">Submitted cases affected</span>
              <span className="font-mono font-semibold text-teal">{impact.affectedSubmittedCases}</span>
            </div>

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
