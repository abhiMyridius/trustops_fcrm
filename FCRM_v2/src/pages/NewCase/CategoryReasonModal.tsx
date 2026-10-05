import { useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Form';
import { Button } from '@/components/ui/Button';
import type { TriggerCategory } from '@/types';

export function CategoryReasonModal({
  category,
  suggestedReason,
  onCancel,
  onSubmit,
}: {
  category: TriggerCategory | null;
  suggestedReason?: string;
  onCancel: () => void;
  onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState(suggestedReason ?? '');

  return (
    <Modal open={!!category} onClose={onCancel} title="Remove category tag" width={460}>
      <div className="flex flex-col gap-4">
        <p className="text-[13.5px] text-text-secondary">
          Removing <strong className="text-text-primary">{category}</strong> changes which fields are required. This needs a one-line reason - it's written to the Activity Log alongside
          the AI's original proposal.
        </p>
        <Textarea rows={3} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why doesn't this category apply?" />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={!reason.trim()} onClick={() => onSubmit(reason.trim())}>
            Remove tag
          </Button>
        </div>
      </div>
    </Modal>
  );
}
