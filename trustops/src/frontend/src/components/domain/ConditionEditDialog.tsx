/**
 * Add a condition to the recommendation, or edit one. Nothing is sent from here: the analyst's list is saved with
 * "Save draft" on the Recommendation tab, and the server keeps each condition's source.
 */
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';

export interface ConditionDraft {
  /** Set for an existing condition; absent for a new one. */
  id?: string;
  text: string;
  owner: string;
  due: string;
  status: 'open' | 'scheduled';
}

export function ConditionEditDialog({
  open,
  onClose,
  initial,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  /** The condition being edited; null when adding. */
  initial: ConditionDraft | null;
  onSave: (c: ConditionDraft) => void;
}) {
  const [text, setText] = useState('');
  const [owner, setOwner] = useState('');
  const [due, setDue] = useState('');
  const [status, setStatus] = useState<ConditionDraft['status']>('open');

  useEffect(() => {
    if (open) {
      setText(initial?.text ?? '');
      setOwner(initial?.owner ?? '');
      setDue(initial?.due ?? '');
      setStatus(initial?.status ?? 'open');
    }
    // Reset only when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const valid = text.trim() !== '' && owner.trim() !== '' && due.trim() !== '';

  function save() {
    if (!valid) return;
    onSave({ id: initial?.id, text: text.trim(), owner: owner.trim(), due: due.trim(), status });
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initial ? 'Edit condition' : 'Add condition'}
      description="The product owner reads this condition. Do not name a score, band, rating or control."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} disabled={!valid}>
            {initial ? 'Update condition' : 'Add condition'}
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <Textarea label="Condition" value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={2000} />
        <Input label="Owner" value={owner} onChange={(e) => setOwner(e.target.value)} maxLength={120} />
        <Input label="Due" value={due} onChange={(e) => setDue(e.target.value)} maxLength={80} hint="For example: before go-live, or 3 months." />
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as ConditionDraft['status'])}
          options={[
            { value: 'open', label: 'Open' },
            { value: 'scheduled', label: 'Scheduled' },
          ]}
        />
      </form>
    </Modal>
  );
}
