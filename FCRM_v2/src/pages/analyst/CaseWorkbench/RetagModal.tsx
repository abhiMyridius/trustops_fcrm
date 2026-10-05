import { useState } from 'react';
import type { CaseDetail } from '@/types';
import { ALL_CATEGORIES, type TriggerCategory } from '@/types';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Select, Textarea } from '@/components/ui/Form';
import { analystAddCategory } from '@/services/analystApi';
import { useToast } from '@/components/ui/Toast';

export function RetagModal({ caseData, open, onClose, onAdded }: { caseData: CaseDetail; open: boolean; onClose: () => void; onAdded: () => void }) {
  const available = ALL_CATEGORIES.filter((c) => !caseData.categories.includes(c));
  const [category, setCategory] = useState<TriggerCategory>(available[0] ?? ALL_CATEGORIES[0]);
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  async function handleAdd() {
    if (!reason.trim()) return;
    setSaving(true);
    await analystAddCategory(caseData.id, category, reason.trim());
    setSaving(false);
    toast(`Added "${category}" - reason logged.`, 'success');
    setReason('');
    onAdded();
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose} title="Re-tag case" width={460}>
      <div className="flex flex-col gap-4">
        <p className="text-[13px] text-text-secondary">
          Adding a category expands the required-field set and may drop completion below 100%. Both the PO's original tags and this addition are logged with a reason.
        </p>
        <div className="flex flex-col gap-1.5">
          <label className="text-[12.5px] font-semibold text-[#15393e]">Category to add</label>
          <Select value={category} onChange={(e) => setCategory(e.target.value as TriggerCategory)}>
            {available.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-[12.5px] font-semibold text-[#15393e]">Reason</label>
          <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why does this category apply?" />
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={saving} disabled={!reason.trim() || available.length === 0} onClick={handleAdd}>
            Add category
          </Button>
        </div>
      </div>
    </Modal>
  );
}
