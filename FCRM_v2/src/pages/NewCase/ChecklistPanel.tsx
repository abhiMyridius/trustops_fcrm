import { AnimatePresence, motion } from 'framer-motion';
import type { FieldValue, TriggerCategory } from '@/types';
import { CORE_SET_KEYS, CATEGORY_PACK_KEYS, fieldByKey } from '@/mocks/seed/requiredFields';
import { CompletionRing } from '@/components/ui/CompletionRing';
import { Icon } from '@/components/ui/Icon';
import { Input } from '@/components/ui/Form';
import { useState } from 'react';
import clsx from 'clsx';

const STATE_STYLE: Record<FieldValue['state'], { color: string; icon: 'check' | 'edit' | 'close'; label: string }> = {
  Extracted: { color: 'text-teal', icon: 'check', label: 'Extracted' },
  'Needs confirmation': { color: 'text-warning', icon: 'edit', label: 'Needs confirmation' },
  Missing: { color: 'text-text-faint', icon: 'close', label: 'Missing' },
};

function FieldRow({ field, index, neverBlank, onEdit }: { field: FieldValue; index: number; neverBlank?: boolean; onEdit: (value: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.value ?? '');
  const style = STATE_STYLE[field.state];
  // Third, distinct state: missing AND always-required - the ones that block submission no
  // matter the percentage, so this needs to read differently from an ordinary missing field, not
  // just carry the same chip.
  const blocksSubmission = neverBlank && !field.value;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.32, delay: Math.min(index, 10) * 0.05 }}
      className={clsx('flex flex-col gap-1 py-2.5 px-3 rounded-md hover:bg-surface-tertiary group', blocksSubmission && 'bg-danger-bg hover:bg-danger-bg')}
    >
      <div className="flex items-start gap-2">
        <span className={clsx('mt-0.5 shrink-0', blocksSubmission ? 'text-danger' : style.color)}>
          <Icon name={blocksSubmission ? 'warning' : style.icon} size={13} strokeWidth={2.2} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12.5px] font-semibold text-text-primary flex items-center gap-1.5">
              {field.label}
              {neverBlank && <span className="text-[9px] font-bold uppercase tracking-wide text-danger bg-danger-bg px-1.5 py-0.5 rounded-full shrink-0">Always required</span>}
            </span>
            {field.value && !editing && (
              <button onClick={() => setEditing(true)} className="opacity-0 group-hover:opacity-100 text-text-faint hover:text-carbon-moss cursor-pointer transition-opacity shrink-0">
                <Icon name="edit" size={12} />
              </button>
            )}
          </div>
          {editing ? (
            <div className="flex gap-1.5 mt-1">
              <Input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="py-1 text-[12.5px]"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    onEdit(draft);
                    setEditing(false);
                  }
                }}
              />
              <button
                onClick={() => {
                  onEdit(draft);
                  setEditing(false);
                }}
                className="text-[11px] font-semibold text-carbon-moss px-2 cursor-pointer"
              >
                Save
              </button>
            </div>
          ) : (
            <p className={clsx('text-[12px] mt-0.5 line-clamp-2', blocksSubmission ? 'text-danger font-medium' : 'text-text-muted')}>
              {field.value ?? 'Not yet provided'}
              {field.clonedFromCaseId && <span className="ml-1.5 font-semibold text-digital-pavo">· Reused from {field.clonedFromCaseId}</span>}
              {field.humanOverridden && <span className="ml-1.5 font-semibold text-teal">· edited</span>}
            </p>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export function ChecklistPanel({
  categories,
  fields,
  optionalCount,
  completionPct,
  threshold,
  fieldSetVersion,
  activeFieldSetVersion,
  onEditField,
  expanded,
  onToggleExpand,
}: {
  categories: TriggerCategory[];
  fields: FieldValue[];
  optionalCount: number;
  completionPct: number;
  threshold: number;
  fieldSetVersion?: number;
  activeFieldSetVersion?: number | null;
  onEditField: (fieldId: string, value: string) => void;
  expanded?: boolean;
  onToggleExpand?: () => void;
}) {
  const filled = fields.filter((f) => f.value).length;

  const coreFields = fields.filter((f) => CORE_SET_KEYS.includes(f.fieldId));
  const packGroups: { category: TriggerCategory; fields: FieldValue[] }[] = categories.map((cat) => ({
    category: cat,
    fields: fields.filter((f) => !CORE_SET_KEYS.includes(f.fieldId) && (CATEGORY_PACK_KEYS[cat] ?? []).includes(f.fieldId)),
  }));
  // A shared field (e.g. process/data-flow) can appear in more than one pack's key list -
  // only show it once, under the first category that claims it.
  const seen = new Set<string>();
  for (const group of packGroups) {
    group.fields = group.fields.filter((f) => {
      if (seen.has(f.fieldId)) return false;
      seen.add(f.fieldId);
      return true;
    });
  }

  return (
    <div className="w-full h-full flex flex-col bg-white border-l border-border">
      <div className="p-5 border-b border-border-hairline flex items-center gap-4">
        <CompletionRing pct={completionPct} threshold={threshold} />
        <div className="flex flex-col gap-1 min-w-0 flex-1">
          <span className="text-sm font-bold text-text-primary">Submission checklist</span>
          <span className="text-[12px] text-text-muted">
            {filled} of {fields.length} required fields
          </span>
          {categories.length > 0 && <span className="text-[11.5px] text-text-faint truncate">Core + {categories.join(' · ')}</span>}
        </div>
        {onToggleExpand && (
          <button
            onClick={onToggleExpand}
            title={expanded ? 'Collapse checklist' : 'Expand checklist'}
            className="text-text-faint hover:text-carbon-moss cursor-pointer shrink-0 self-start"
          >
            <Icon name={expanded ? 'collapse' : 'expand'} size={16} />
          </button>
        )}
      </div>

      {fieldSetVersion != null && (
        <div className="px-5 py-2 border-b border-border-hairline bg-[#F4F8F7] text-[11.5px] text-[#285055] flex items-center gap-1.5">
          <Icon name="info" size={12} className="shrink-0" />
          <span>
            Started on field set v{fieldSetVersion}
            {activeFieldSetVersion != null && activeFieldSetVersion > fieldSetVersion
              ? ` - v${activeFieldSetVersion} is now active, but this draft finishes on the version it started on.`
              : '.'}
          </span>
        </div>
      )}

      <div className={`flex-1 overflow-y-auto px-2 py-2 ${expanded ? 'max-w-[1000px] mx-auto w-full' : ''}`}>
        {fields.length === 0 ? (
          <div className="px-3 py-8 text-center text-[13px] text-text-faint">Fields will appear here once we know what category this falls under.</div>
        ) : (
          <AnimatePresence initial={false}>
            {coreFields.length > 0 && (
              <div key="group-core" className="px-3 pt-2 pb-1 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">
                Core set
              </div>
            )}
            <div className={expanded ? 'grid grid-cols-2 gap-x-4' : ''}>
              {coreFields.map((f, i) => (
                <FieldRow key={f.fieldId} field={f} index={i} neverBlank={fieldByKey(f.fieldId)?.neverBlank} onEdit={(v) => onEditField(f.fieldId, v)} />
              ))}
            </div>
            {packGroups.map(
              (group) =>
                group.fields.length > 0 && (
                  <div key={`group-${group.category}`}>
                    <div className="px-3 pt-3 pb-1 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Pack: {group.category}</div>
                    <div className={expanded ? 'grid grid-cols-2 gap-x-4' : ''}>
                      {group.fields.map((f, i) => (
                        <FieldRow key={f.fieldId} field={f} index={coreFields.length + i} neverBlank={fieldByKey(f.fieldId)?.neverBlank} onEdit={(v) => onEditField(f.fieldId, v)} />
                      ))}
                    </div>
                  </div>
                ),
            )}
          </AnimatePresence>
        )}
      </div>

      {optionalCount > 0 && (
        <div className="px-5 py-3 border-t border-border-hairline text-[11.5px] text-text-faint">
          {optionalCount} optional field{optionalCount === 1 ? '' : 's'} - shown only in the submission preview, not counted here.
        </div>
      )}
    </div>
  );
}
