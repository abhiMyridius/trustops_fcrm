import { useEffect, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Textarea } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';
import { Skeleton } from '@/components/ui/Skeleton';
import { draftClarificationQuestions, getCaseFieldOptions, sendClarificationRound, type DraftedQuestion } from '@/services/analystApi';
import { useToast } from '@/components/ui/Toast';

let uid = 0;

export function ClarificationComposer({
  caseId,
  stage,
  open,
  onClose,
  onSent,
}: {
  caseId: string;
  stage: 'clarify' | 'mapping' | 'risk-controls';
  open: boolean;
  onClose: () => void;
  onSent: () => void;
}) {
  const [questions, setQuestions] = useState<DraftedQuestion[] | null>(null);
  const [fieldOptions, setFieldOptions] = useState<{ fieldId: string; label: string }[]>([]);
  const [sending, setSending] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!open) {
      setQuestions(null);
      return;
    }
    draftClarificationQuestions(caseId, stage).then(setQuestions);
    getCaseFieldOptions(caseId).then(setFieldOptions);
  }, [open, caseId, stage]);

  function updateQ(id: string, patch: Partial<DraftedQuestion>) {
    setQuestions((qs) => qs?.map((q) => (q.id === id ? { ...q, ...patch } : q)) ?? null);
  }

  // The anchor is a multi-select (§8.4 mod, §8.2) - zero fields checked is "not tied to a
  // specific field" (the reason box shows), one is a single-field anchor, two or more is
  // multi-field anchored. Never the same finding as "not tied to a field" (§3.3).
  function toggleField(q: DraftedQuestion, fieldId: string, checked: boolean) {
    const current = q.fieldIds ?? (q.fieldId ? [q.fieldId] : []);
    const next = checked ? [...current, fieldId] : current.filter((id) => id !== fieldId);
    if (next.length === 0) updateQ(q.id, { fieldId: undefined, fieldIds: undefined });
    else if (next.length === 1) updateQ(q.id, { fieldId: next[0], fieldIds: undefined, notAnchoredReason: undefined });
    else updateQ(q.id, { fieldId: undefined, fieldIds: next, notAnchoredReason: undefined });
  }

  function removeQ(id: string) {
    setQuestions((qs) => qs?.filter((q) => q.id !== id) ?? null);
  }

  function addQ() {
    uid += 1;
    setQuestions((qs) => [...(qs ?? []), { id: `new-${uid}`, text: '', updateForm: false }]);
  }

  async function handleSend() {
    if (!questions || questions.length === 0) return;
    setSending(true);
    const { roundNumber } = await sendClarificationRound(caseId, questions.filter((q) => q.text.trim()), stage);
    setSending(false);
    toast(`Round ${roundNumber} sent - ${questions.length} question${questions.length === 1 ? '' : 's'} batched.`, 'success');
    onSent();
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose} title="Compose clarification" width={560}>
      <div className="flex flex-col gap-4">
        <p className="text-[12.5px] text-text-muted">
          Drafted by the Clarification Agent from detected gaps. Edit, add, or remove questions - everything sends as <strong>one batched request</strong>. Raised from the {stage} stage.
        </p>

        {!questions ? (
          <Skeleton className="h-40 rounded-md" />
        ) : (
          <div className="flex flex-col gap-3 max-h-[420px] overflow-y-auto pr-1">
            {questions.map((q) => (
              <div key={q.id} className="border border-border-strong rounded-lg p-3 flex flex-col gap-2">
                <Textarea rows={2} value={q.text} onChange={(e) => updateQ(q.id, { text: e.target.value })} placeholder="Question text..." />
                <div className="flex flex-col gap-1">
                  <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Field anchor - select any that apply</span>
                  <div className="border border-border-strong rounded-md p-2 max-h-32 overflow-y-auto flex flex-col gap-1 bg-white">
                    {fieldOptions.map((f) => {
                      const selected = (q.fieldIds ?? (q.fieldId ? [q.fieldId] : [])).includes(f.fieldId);
                      return (
                        <label key={f.fieldId} className="flex items-center gap-2 text-[12px] text-text-primary cursor-pointer">
                          <input type="checkbox" className="cursor-pointer" checked={selected} onChange={(e) => toggleField(q, f.fieldId, e.target.checked)} />
                          {f.label}
                        </label>
                      );
                    })}
                  </div>
                  {!q.fieldId && !q.fieldIds && (
                    <Input
                      value={q.notAnchoredReason ?? ''}
                      onChange={(e) => updateQ(q.id, { notAnchoredReason: e.target.value })}
                      placeholder="Not tied to a specific field - why doesn't this map to one? (e.g. template gap)"
                      className="text-[12.5px] py-1.5"
                    />
                  )}
                </div>
                <div className="flex items-center justify-between">
                  <Checkbox checked={q.updateForm} onChange={(v) => updateQ(q.id, { updateForm: v })} label={<span className="text-[12px] font-medium">Update form</span>} />
                  <button onClick={() => removeQ(q.id)} className="text-text-faint hover:text-danger cursor-pointer p-1">
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              </div>
            ))}
            <Button size="sm" variant="ghost" icon={<Icon name="plus" size={14} />} onClick={addQ} className="self-start">
              Add question
            </Button>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={sending} disabled={!questions?.some((q) => q.text.trim())} onClick={handleSend}>
            Send round ({questions?.filter((q) => q.text.trim()).length ?? 0} question{(questions?.length ?? 0) === 1 ? '' : 's'})
          </Button>
        </div>
      </div>
    </Modal>
  );
}
