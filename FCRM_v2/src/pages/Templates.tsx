import { useEffect, useMemo, useState } from 'react';
import type { FieldType, RequiredFieldDef, Template, TriggerCategory } from '@/types';
import { ALL_CATEGORIES } from '@/types';
import { addOptionalField, getTemplates, removeOptionalField, renameOptionalField, reorderOptionalFields } from '@/services/api';
import { CORE_SET_KEYS, GLOBAL_COMPLETION_THRESHOLD, CORE_SET_VERSION, CORE_SET_LAST_UPDATED, CORE_SET_HISTORY } from '@/mocks/seed/requiredFields';
import { CATEGORY_DESCRIPTIONS } from '@/mocks/seed/templates';
import { Card } from '@/components/ui/Card';
import { LockedBadge } from '@/components/ui/Badge';
import { Icon, type IconName } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { useToast } from '@/components/ui/Toast';

const FIELD_TYPE_META: Record<FieldType, { icon: IconName; label: string }> = {
  text: { icon: 'type-text', label: 'Text' },
  textarea: { icon: 'type-longtext', label: 'Long text' },
  number: { icon: 'type-number', label: 'Number' },
  date: { icon: 'calendar', label: 'Date' },
  documents: { icon: 'upload', label: 'File upload' },
  multiselect: { icon: 'type-multiselect', label: 'Multi-select' },
};

function AlwaysRequiredChip() {
  return (
    <span className="inline-flex items-center gap-1 text-[9.5px] font-bold uppercase tracking-wide text-carbon-moss bg-verdant-1 px-1.5 py-0.5 rounded-full shrink-0">
      <Icon name="lock" size={9} strokeWidth={2.2} />
      Always required
    </span>
  );
}

function GroupHeaderNote({ groupLabel }: { groupLabel: string }) {
  return (
    <p className="text-[12px] text-text-muted leading-snug">
      All fields in {groupLabel} are required. {GLOBAL_COMPLETION_THRESHOLD}% of the resolved set must be filled to submit, and fields marked{' '}
      <span className="font-semibold text-carbon-moss">Always required</span> can never be among the ones left blank.
    </p>
  );
}

function FieldRow({ field, highlighted }: { field: RequiredFieldDef; highlighted?: boolean }) {
  const meta = FIELD_TYPE_META[field.type ?? 'text'];
  return (
    <div className={`flex items-start gap-2.5 px-3 py-2.5 rounded-md transition-colors ${highlighted ? 'bg-verdant-1' : ''}`} id={`field-${field.id}`}>
      <span className="text-text-faint shrink-0 mt-0.5">
        <Icon name={meta.icon} size={15} />
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2 flex-wrap">
          <span className="flex items-center gap-2 flex-wrap min-w-0">
            <span className="text-[13px] font-semibold text-text-primary">{field.label}</span>
            <span className="text-[10.5px] text-text-faint shrink-0">{meta.label}</span>
          </span>
          {field.neverBlank && <AlwaysRequiredChip />}
        </div>
        {field.helpText && <p className="text-[12px] text-text-muted mt-0.5">{field.helpText}</p>}
      </div>
    </div>
  );
}

function WorkedExample() {
  return (
    <Card material="flat" className="p-5 flex flex-col gap-2">
      <span className="text-sm font-bold text-text-primary">How the two-tier rule works</span>
      <p className="text-[13px] text-text-secondary leading-relaxed">
        A vendor onboarding case in a new geography resolves to <strong>18</strong> required fields, <strong>6</strong> of them always-required. Submitting needs{' '}
        <strong>15 of 18</strong> filled, and all <strong>6</strong> always-required fields must be among them. That leaves 3 fields that can be completed later
        through clarification.
      </p>
      <p className="text-[12px] text-text-faint">
        Always-required fields are counted in both the numerator and denominator of the {GLOBAL_COMPLETION_THRESHOLD}% - the flag constrains which fields the
        remaining slack can fall on, not an extra requirement on top of it.
      </p>
    </Card>
  );
}

function OwnershipExplainer() {
  return (
    <Card material="flat" className="p-5 flex flex-col gap-2.5">
      <span className="text-sm font-bold text-text-primary">Ownership</span>
      <p className="text-[13px] text-text-secondary leading-relaxed">
        <strong className="text-text-primary">FCRM owns</strong> required fields, the category packs, and the completion threshold. These are read-only here.
      </p>
      <p className="text-[13px] text-text-secondary leading-relaxed">
        <strong className="text-text-primary">You own</strong> optional fields. Add, rename, reorder or remove them at any time. They take effect immediately.
      </p>
    </Card>
  );
}

function OptionalFieldsEditor({ template, onChanged }: { template: Template; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const [newLabel, setNewLabel] = useState('');
  const [newType, setNewType] = useState<FieldType>('text');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [dragId, setDragId] = useState<string | null>(null);
  const toast = useToast();

  async function handleAdd() {
    if (!newLabel.trim()) return;
    await addOptionalField(template.category, newLabel.trim(), newType);
    setNewLabel('');
    setNewType('text');
    setAdding(false);
    toast('Optional field added.', 'success');
    onChanged();
  }

  async function handleRemove(id: string) {
    await removeOptionalField(template.category, id);
    toast('Optional field removed.', 'default');
    onChanged();
  }

  async function handleRename(id: string) {
    if (!renameValue.trim()) return;
    await renameOptionalField(template.category, id, renameValue.trim());
    setRenamingId(null);
    toast('Label updated.', 'success');
    onChanged();
  }

  async function handleDrop(targetId: string) {
    if (!dragId || dragId === targetId) {
      setDragId(null);
      return;
    }
    const ordered = template.optionalFields.map((f) => f.id);
    const from = ordered.indexOf(dragId);
    const to = ordered.indexOf(targetId);
    ordered.splice(from, 1);
    ordered.splice(to, 0, dragId);
    setDragId(null);
    await reorderOptionalFields(template.category, ordered);
    onChanged();
  }

  return (
    <Card material="flat" className="p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-text-primary">Optional fields - yours to edit</span>
        <Button size="sm" variant="secondary" icon={<Icon name="plus" size={14} />} onClick={() => setAdding(true)}>
          Add field
        </Button>
      </div>
      <p className="text-[12.5px] text-text-muted -mt-2">
        Excluded from the completion percentage, never appear in the intake checklist - shown only in the submission preview and case Overview.
      </p>

      {adding && (
        <div className="flex gap-2">
          <Input autoFocus placeholder="e.g. Cost-centre tag" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleAdd()} className="flex-1" />
          <Select value={newType} onChange={(e) => setNewType(e.target.value as FieldType)} className="w-[150px]">
            {(Object.entries(FIELD_TYPE_META) as [FieldType, { label: string }][]).map(([t, m]) => (
              <option key={t} value={t}>
                {m.label}
              </option>
            ))}
          </Select>
          <Button size="sm" variant="primary" onClick={handleAdd}>
            Add
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>
            Cancel
          </Button>
        </div>
      )}

      {template.optionalFields.length === 0 && !adding && <p className="text-[13px] text-text-faint py-2">No optional fields yet.</p>}

      <div className="flex flex-col gap-0.5">
        {template.optionalFields.map((f) => (
          <div
            key={f.id}
            draggable
            onDragStart={() => setDragId(f.id)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => handleDrop(f.id)}
            className={`flex items-center gap-2 px-3 py-2 rounded-md hover:bg-surface-tertiary group ${dragId === f.id ? 'opacity-50' : ''}`}
          >
            <Icon name="grip" size={14} className="text-text-faint shrink-0 cursor-grab" />
            {renamingId === f.id ? (
              <div className="flex gap-2 flex-1">
                <Input autoFocus value={renameValue} onChange={(e) => setRenameValue(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleRename(f.id)} className="py-1.5" />
                <Button size="sm" variant="primary" onClick={() => handleRename(f.id)}>
                  Save
                </Button>
              </div>
            ) : (
              <span className="flex-1 text-[13.5px] text-text-primary flex items-center gap-2">
                {f.label}
                <span className="text-[10.5px] text-text-faint font-normal">{FIELD_TYPE_META[f.type ?? 'text'].label}</span>
              </span>
            )}
            {renamingId !== f.id && (
              <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={() => {
                    setRenamingId(f.id);
                    setRenameValue(f.label);
                  }}
                  className="text-text-muted hover:text-carbon-moss cursor-pointer p-1"
                >
                  <Icon name="edit" size={14} />
                </button>
                <button onClick={() => handleRemove(f.id)} className="text-text-muted hover:text-danger cursor-pointer p-1">
                  <Icon name="trash" size={14} />
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

function VersionHistoryCard({ title, history }: { title: string; history: { version: number; publishedAt: string; note: string }[] }) {
  return (
    <Card material="flat" className="p-5 flex flex-col gap-2">
      <span className="text-sm font-bold text-text-primary">{title}</span>
      <div className="flex flex-col divide-y divide-border-hairline">
        {history
          .slice()
          .reverse()
          .map((h) => (
            <div key={h.version} className="flex items-center gap-3 py-2.5 text-[13px]">
              <span className="font-mono text-text-faint w-10">v{h.version}</span>
              <span className="text-text-secondary flex-1">{h.note}</span>
              <span className="text-text-faint text-[12px]">{h.publishedAt}</span>
            </div>
          ))}
      </div>
    </Card>
  );
}

function TemplatePanel({ template, highlightedFieldId, onChanged }: { template: Template; highlightedFieldId: string | null; onChanged: () => void }) {
  const coreFields = template.requiredFields.filter((f) => CORE_SET_KEYS.includes(f.id));
  const packFields = template.requiredFields.filter((f) => !CORE_SET_KEYS.includes(f.id));
  const packLastUpdated = template.history.at(-1)?.publishedAt;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-lg font-bold text-text-primary">{template.category}</h2>
        <p className="text-[13px] text-text-secondary">{CATEGORY_DESCRIPTIONS[template.category]}</p>
        <p className="text-[12.5px] font-semibold text-text-muted">
          {coreFields.length} core (v{CORE_SET_VERSION}) + {packFields.length} pack (v{template.version}) = {template.requiredFields.length} fields
        </p>
      </div>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="text-sm font-bold text-text-primary">Core set</span>
            <LockedBadge label="FCRM-owned" />
          </div>
          <span className="text-[11.5px] font-mono text-text-faint">v{CORE_SET_VERSION} · {CORE_SET_LAST_UPDATED}</span>
        </div>
        <GroupHeaderNote groupLabel="the core set" />
        <div className="flex flex-col divide-y divide-border-hairline">
          {coreFields.map((f) => (
            <FieldRow key={f.id} field={f} highlighted={f.id === highlightedFieldId} />
          ))}
        </div>
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="text-sm font-bold text-text-primary">Category pack</span>
            <LockedBadge label="FCRM-owned" />
          </div>
          <span className="text-[11.5px] font-mono text-text-faint">v{template.version} · {packLastUpdated}</span>
        </div>
        <GroupHeaderNote groupLabel={`the ${template.category} pack`} />
        <div className="flex flex-col divide-y divide-border-hairline">
          {packFields.map((f) => (
            <FieldRow key={f.id} field={f} highlighted={f.id === highlightedFieldId} />
          ))}
        </div>
      </Card>

      <OptionalFieldsEditor template={template} onChanged={onChanged} />

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary">AI insight</span>
        {template.aiInsight?.startsWith('Not enough') || template.aiInsight?.startsWith('No recurring') ? (
          <EmptyState icon="sparkles" title={template.aiInsight} />
        ) : (
          <div className="flex items-start gap-2 bg-[#F4F8F7] rounded-md px-3 py-2.5">
            <Icon name="sparkles" size={14} className="text-teal shrink-0 mt-0.5" />
            <p className="text-[12.5px] text-[#133338] leading-snug">{template.aiInsight}</p>
          </div>
        )}
      </Card>

      <OwnershipExplainer />

      {/* Core set and each category pack are separately versioned FCRM-owned artifacts, so each
          gets its own history - a pack change (e.g. adding a required field) never belongs under
          the core set's version, and vice versa. */}
      <VersionHistoryCard title="Core set version history" history={CORE_SET_HISTORY} />
      <VersionHistoryCard title={`${template.category} pack version history`} history={template.history} />
    </div>
  );
}

export function Templates() {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [active, setActive] = useState<TriggerCategory>('Vendor / Third-Party Onboarding');
  const [search, setSearch] = useState('');
  const [highlightedFieldId, setHighlightedFieldId] = useState<string | null>(null);

  async function reload() {
    const t = await getTemplates();
    setTemplates(t);
  }

  useEffect(() => {
    reload();
  }, []);

  const current = templates?.find((t) => t.category === active);

  const searchResults = useMemo(() => {
    if (!templates || !search.trim()) return [];
    const q = search.trim().toLowerCase();
    const results: { field: RequiredFieldDef; category: TriggerCategory }[] = [];
    for (const t of templates) {
      for (const f of t.requiredFields) {
        if (f.label.toLowerCase().includes(q)) results.push({ field: f, category: t.category });
      }
    }
    return results.slice(0, 8);
  }, [templates, search]);

  function jumpToField(field: RequiredFieldDef, category: TriggerCategory) {
    setActive(category);
    setHighlightedFieldId(field.id);
    setSearch('');
    setTimeout(() => document.getElementById(`field-${field.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 60);
    setTimeout(() => setHighlightedFieldId(null), 2200);
  }

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">Templates</h1>
        <p className="text-[13.5px] text-text-muted mt-1">
          Required fields and the completion threshold are FCRM-owned and read-only here. You control your own optional fields per category.
        </p>
      </div>

      <div className="flex gap-4 flex-wrap">
        <Card material="flat" className="p-5 flex items-center gap-4">
          <div>
            <span className="text-[32px] font-bold text-text-primary leading-none">{GLOBAL_COMPLETION_THRESHOLD}%</span>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-text-primary">Global completion threshold</span>
              <LockedBadge label="FCRM-owned · locked" />
            </div>
            <p className="text-[12.5px] text-text-muted mt-1 max-w-md">One threshold, applies to every submission regardless of category.</p>
          </div>
        </Card>
        <Card material="flat" className="p-5 flex items-center gap-4">
          <div>
            <span className="text-[22px] font-mono font-bold text-text-primary leading-none">v{CORE_SET_VERSION}</span>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-text-primary">Core set</span>
              <LockedBadge label="FCRM-owned" />
            </div>
            <p className="text-[12.5px] text-text-muted mt-1 max-w-md">Same 9 fields every category builds on · updated {CORE_SET_LAST_UPDATED}</p>
          </div>
        </Card>
        <div className="flex-1 min-w-[280px]">
          <WorkedExample />
        </div>
      </div>

      <div className="relative max-w-md">
        <Icon name="search" size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-faint" />
        <Input placeholder="Search fields across every category…" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        {searchResults.length > 0 && (
          <div className="absolute z-20 mt-1.5 w-full bg-white border border-border-strong rounded-lg shadow-md p-1.5 flex flex-col gap-0.5 max-h-[280px] overflow-y-auto">
            {searchResults.map((r) => (
              <button
                key={`${r.category}-${r.field.id}`}
                onClick={() => jumpToField(r.field, r.category)}
                className="text-left px-2.5 py-2 rounded-md hover:bg-surface-tertiary cursor-pointer flex items-center justify-between gap-2"
              >
                <span className="text-[13px] text-text-primary">{r.field.label}</span>
                <span className="text-[10.5px] text-text-faint bg-surface-tertiary px-1.5 py-0.5 rounded shrink-0">{r.category}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex gap-4">
        <div className="w-[240px] shrink-0 flex flex-col gap-1">
          {ALL_CATEGORIES.map((cat) => {
            const t = templates?.find((x) => x.category === cat);
            return (
              <button
                key={cat}
                onClick={() => setActive(cat)}
                className={
                  'text-left px-3.5 py-2.5 rounded-md text-[13.5px] font-medium transition-colors cursor-pointer flex items-center justify-between gap-2 ' +
                  (active === cat ? 'bg-[#e1e6e4] text-[#14363b] font-semibold' : 'text-text-secondary hover:bg-surface-tertiary')
                }
              >
                <span className="truncate">{cat}</span>
                <span className="flex items-center gap-1.5 shrink-0">
                  {t && t.optionalFields.length > 0 && <span title={`${t.optionalFields.length} optional field(s) added`} className="w-1.5 h-1.5 rounded-full bg-teal" />}
                  {t && <span className="text-[11px] text-text-faint">{t.requiredFields.length}</span>}
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex-1 min-w-0">
          {!current && (
            <div className="flex flex-col gap-4">
              <Skeleton className="h-24 rounded-md" />
              <Skeleton className="h-24 rounded-md" />
              <Skeleton className="h-40 rounded-md" />
            </div>
          )}
          {current && <TemplatePanel template={current} highlightedFieldId={highlightedFieldId} onChanged={reload} />}
        </div>
      </div>
    </div>
  );
}
