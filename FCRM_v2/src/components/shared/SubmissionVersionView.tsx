import { useEffect, useMemo, useState } from 'react';
import type { CaseDetail, Document, FieldValue, SubmissionVersionSnapshot, TriggerCategory } from '@/types';
import { poConfirmFieldEdit, poEditFieldWithAI } from '@/services/analystApi';
import { CATEGORY_PACK_KEYS, CORE_SET_KEYS, fieldByKey } from '@/mocks/seed/requiredFields';
import { Button } from '@/components/ui/Button';
import { Select, Switch, Textarea } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';
import { formatDateTime } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';

/** Quiet, non-badge label naming where a field's current value came from (§8.2 mod, §5) - a
 * clarification-round revision outranks the field's own extracted/manual/clone source, since
 * that's the most specific and most recent explanation for the value on screen. */
function fieldProvenance(field: FieldValue, versions: CaseDetail['submissionVersions']): { text: string; caseLink?: string } | null {
  if (!field.value) return null;
  // Latest version touching this field wins - the most specific, most recent explanation for
  // the value on screen (§8.3 mod, §5; §8.4 mod, §7: an analyst write-back like a re-tag needs
  // its own label here too, not just "Entered"/"Extracted" from whoever originally typed it).
  let latest: SubmissionVersionSnapshot | undefined;
  for (const v of versions ?? []) {
    if (v.changedFieldIds.includes(field.fieldId)) latest = v;
  }
  if (latest?.raisedInRound != null) return { text: `Revised, round ${latest.raisedInRound}` };
  if (latest?.changedBy === 'analyst') return { text: latest.reason ? `Revised by analyst - ${latest.reason}` : 'Revised by analyst' };
  if (field.clonedFromCaseId) return { text: `Reused from ${field.clonedFromCaseId}`, caseLink: field.clonedFromCaseId };
  if (field.source === 'manual') return { text: 'Entered' };
  if (field.source === 'extracted') return { text: 'Extracted' };
  return null;
}

/**
 * Renders the submission as of a selected version, with an optional diff
 * view. Used identically by the PO's Overview tab and the analyst's
 * Submission tab (§6.2: "Identical rendering for PO and analyst").
 *
 * `editable` turns on the PO-only "Edit with AI" affordance - the analyst's
 * Submission tab never passes it, so the analyst view stays read-only.
 */
export function SubmissionVersionView({
  caseData,
  editable,
  onFieldEdited,
  onViewDocument,
  onNavigateTab,
}: {
  caseData: CaseDetail;
  editable?: boolean;
  onFieldEdited?: () => void;
  onViewDocument?: (doc: Document) => void;
  /** Lets a field link back to the clarification thread(s) anchored to it (§8.3 mod, §5.3) - the
   * analyst's Submission tab passes this; the PO's Overview tab doesn't, so the link simply
   * doesn't render there. */
  onNavigateTab?: (tab: string) => void;
}) {
  const versions = caseData.submissionVersions ?? [];
  const latest = versions.at(-1)?.version ?? 1;
  const [selected, setSelected] = useState(latest);
  const [showDiff, setShowDiff] = useState(versions.length > 1);

  // Follow the newest version as new ones land (e.g. right after confirming
  // an edit) - otherwise the view keeps showing a stale version and the
  // edit appears not to have taken effect.
  useEffect(() => {
    setSelected(latest);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editingFieldId, setEditingFieldId] = useState<string | null>(null);
  const [freeText, setFreeText] = useState('');
  const [draftedValue, setDraftedValue] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  function startEdit(fieldId: string, currentValue?: string | null) {
    setEditingFieldId(fieldId);
    setFreeText(currentValue ?? '');
    setDraftedValue(null);
  }

  function cancelEdit() {
    setEditingFieldId(null);
    setFreeText('');
    setDraftedValue(null);
  }

  async function draftWithAI() {
    if (!editingFieldId || !freeText.trim()) return;
    setDrafting(true);
    const { draftedValue: drafted } = await poEditFieldWithAI(caseData.id, editingFieldId, freeText.trim());
    setDraftedValue(drafted);
    setDrafting(false);
  }

  async function confirmEdit(value: string) {
    if (!editingFieldId) return;
    setSaving(true);
    await poConfirmFieldEdit(caseData.id, editingFieldId, value);
    setSaving(false);
    toast('Field updated.', 'success');
    cancelEdit();
    onFieldEdited?.();
  }

  const snapshot = versions.find((v) => v.version === selected);
  const prevSnapshot = versions.find((v) => v.version === selected - 1);

  const rows = useMemo(() => {
    return caseData.fields.map((f) => {
      const value = snapshot ? snapshot.fieldValues[f.fieldId] : f.value;
      const prevValue = prevSnapshot ? prevSnapshot.fieldValues[f.fieldId] : undefined;
      const changed = showDiff && snapshot?.changedFieldIds.includes(f.fieldId);
      const changeType: 'added' | 'edited' | null = changed ? (prevValue == null && value != null ? 'added' : 'edited') : null;
      return { field: f, value, prevValue, changeType };
    });
  }, [caseData.fields, snapshot, prevSnapshot, showDiff]);

  if (versions.length === 0) {
    return <p className="text-[13px] text-text-faint">No submission recorded yet.</p>;
  }

  // Group by source (§8.2 mod, §1) - core set, then one group per confirmed category pack, a
  // shared field rendered once under the first pack that contributed it.
  const coreRows = rows.filter((r) => CORE_SET_KEYS.includes(r.field.fieldId));
  const packGroups: { category: TriggerCategory; rows: typeof rows }[] = caseData.categories.map((cat) => ({
    category: cat,
    rows: rows.filter((r) => !CORE_SET_KEYS.includes(r.field.fieldId) && (CATEGORY_PACK_KEYS[cat] ?? []).includes(r.field.fieldId)),
  }));
  const seen = new Set<string>();
  for (const group of packGroups) {
    group.rows = group.rows.filter((r) => {
      if (seen.has(r.field.fieldId)) return false;
      seen.add(r.field.fieldId);
      return true;
    });
  }
  const packCount = rows.length - coreRows.length;

  function renderRow({ field, value, prevValue, changeType }: (typeof rows)[number]) {
    const neverBlank = fieldByKey(field.fieldId)?.neverBlank;
    const isDataIntegrityGap = neverBlank && !value;
    const provenance = fieldProvenance(field, versions);
    const isDocumentsField = fieldByKey(field.fieldId)?.type === 'documents';
    const namedDocCount = isDocumentsField ? (value ?? '').split(',').map((s) => s.trim()).filter(Boolean).length : 0;

    return (
      <div key={field.fieldId} className={`flex flex-col gap-0.5 ${isDataIntegrityGap ? 'bg-danger-bg rounded-md px-2 -mx-2 py-1.5' : ''}`}>
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11.5px] font-semibold text-text-muted">{field.label}</span>
          {neverBlank && <span className="text-[9px] font-bold uppercase tracking-wide text-danger bg-danger-bg px-1.5 py-0.5 rounded-full shrink-0">Always required</span>}
          {changeType && (
            <button
              onClick={() => setExpanded(expanded === field.fieldId ? null : field.fieldId)}
              className={`text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full cursor-pointer ${changeType === 'added' ? 'bg-verdant-2 text-[#163439]' : 'bg-danger-bg text-danger'}`}
            >
              {changeType}
            </button>
          )}
          {editable && selected === latest && editingFieldId !== field.fieldId && (
            <button
              onClick={() => startEdit(field.fieldId, value)}
              className="text-text-faint hover:text-carbon-moss cursor-pointer p-0.5 ml-auto"
              title="Edit with AI"
            >
              <Icon name="sparkles" size={13} />
            </button>
          )}
        </div>
        {editingFieldId === field.fieldId ? (
          <div className="flex flex-col gap-2 bg-[#F4F8F7] rounded-md p-3 -mx-1">
            {draftedValue === null ? (
              <>
                <Textarea
                  autoFocus
                  rows={2}
                  value={freeText}
                  onChange={(e) => setFreeText(e.target.value)}
                  placeholder="Describe the update in your own words - the AI will draft the field value..."
                  className="text-[13px]"
                />
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="ghost" onClick={cancelEdit}>
                    Cancel
                  </Button>
                  <Button size="sm" variant="primary" icon={<Icon name="sparkles" size={13} />} onClick={draftWithAI} loading={drafting} disabled={!freeText.trim()}>
                    Draft with AI
                  </Button>
                </div>
              </>
            ) : (
              <>
                <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">AI-drafted value</span>
                <Textarea rows={2} value={draftedValue} onChange={(e) => setDraftedValue(e.target.value)} className="text-[13px]" />
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="ghost" onClick={cancelEdit}>
                    Cancel
                  </Button>
                  <Button size="sm" variant="primary" onClick={() => confirmEdit(draftedValue)} loading={saving}>
                    Confirm and save
                  </Button>
                </div>
              </>
            )}
          </div>
        ) : (
          <>
            {changeType === 'edited' ? (
              <>
                <span className="text-[13px] text-danger line-through decoration-1">{prevValue ?? 'Not provided'}</span>
                <span className="text-[13.5px] text-[#163439] font-medium">{value}</span>
              </>
            ) : (
              <span className={`text-[13.5px] ${changeType === 'added' ? 'text-[#163439] font-medium' : 'text-text-primary'}`}>
                {value ?? <span className="text-text-faint italic">Not provided</span>}
              </span>
            )}
            {isDataIntegrityGap && (
              <span className="flex items-center gap-1 text-[11px] font-semibold text-danger">
                <Icon name="warning" size={11} />
                Always-required field is missing on a submitted case - data integrity issue.
              </span>
            )}
            {provenance && (
              <span className="text-[10.5px] text-text-faint">
                {provenance.caseLink ? (
                  <>
                    Reused from{' '}
                    <a href={`/analyst/case/${provenance.caseLink}`} className="text-carbon-moss hover:text-carbon-moss-hover underline">
                      {provenance.caseLink}
                    </a>
                  </>
                ) : (
                  provenance.text
                )}
              </span>
            )}
            {onNavigateTab &&
              (() => {
                const relatedThreads = caseData.clarifications.filter((th) => th.fieldId === field.fieldId || th.fieldIds?.includes(field.fieldId));
                if (relatedThreads.length === 0) return null;
                return (
                  <button onClick={() => onNavigateTab('inbox')} className="text-[10.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer text-left w-fit underline">
                    {relatedThreads.length} clarification thread{relatedThreads.length === 1 ? '' : 's'} on this field
                  </button>
                );
              })()}
            {isDocumentsField && caseData.documents.length > 0 && (
              <div className="flex flex-col gap-1 mt-1">
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {caseData.documents.map((d) => (
                    <button
                      key={d.id}
                      onClick={() => onViewDocument?.(d)}
                      className="inline-flex items-center gap-1 text-[11px] text-carbon-moss hover:text-carbon-moss-hover underline cursor-pointer"
                    >
                      <Icon name="document" size={11} />
                      {d.name}
                    </button>
                  ))}
                </div>
                {namedDocCount !== caseData.documents.length && (
                  <span className="flex items-center gap-1 text-[11px] text-warning">
                    <Icon name="warning" size={11} />
                    Field names {namedDocCount} document{namedDocCount === 1 ? '' : 's'}; {caseData.documents.length} uploaded.
                  </span>
                )}
              </div>
            )}
            {expanded === field.fieldId && snapshot && (
              <div className="flex items-center gap-1.5 mt-1 text-[11px] text-text-faint bg-surface-tertiary rounded-md px-2 py-1.5 w-fit">
                <Icon name="history" size={11} />
                Changed by {snapshot.changedBy === 'analyst' ? 'the analyst' : 'PO'} {formatDateTime(snapshot.capturedAt)}
                {snapshot.raisedInRound && ` - clarification round ${snapshot.raisedInRound}`}
                {snapshot.reason && ` - ${snapshot.reason}`}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <span className="text-[12.5px] text-text-muted">
        <strong className="text-text-primary">{rows.length}</strong> required fields · {coreRows.length} core{packCount > 0 ? ` + ${packCount} pack` : ''}
      </span>

      <div className="flex items-center justify-between flex-wrap gap-3">
        {versions.length <= 1 ? (
          <span className="text-[12.5px] font-medium text-text-muted">v1 (as submitted) · no revisions</span>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="text-[12px] font-semibold text-text-muted">Version</span>
              <Select value={selected} onChange={(e) => setSelected(Number(e.target.value))} className="w-[140px] py-1.5">
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version} {v.version === 1 ? '(as submitted)' : v.raisedInRound ? `(after clarification round ${v.raisedInRound})` : ''}
                  </option>
                ))}
              </Select>
            </div>
            <Switch checked={showDiff} onChange={setShowDiff} label={<span className="text-[12.5px] font-medium">Show changes since submission</span>} />
          </>
        )}
      </div>

      {coreRows.length > 0 && (
        <div className="flex flex-col gap-3">
          <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Core set</span>
          <div className="grid grid-cols-2 gap-x-8 gap-y-4">{coreRows.map(renderRow)}</div>
        </div>
      )}

      {packGroups.map(
        (group) =>
          group.rows.length > 0 && (
            <div key={group.category} className="flex flex-col gap-3">
              <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">{group.category}</span>
              <div className="grid grid-cols-2 gap-x-8 gap-y-4">{group.rows.map(renderRow)}</div>
            </div>
          ),
      )}
    </div>
  );
}
