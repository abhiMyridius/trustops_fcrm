import { useState } from 'react';
import { motion } from 'framer-motion';
import type { ClassificationProposal, TriggerCategory } from '@/types';
import { ALL_CATEGORIES } from '@/types';
import { CATEGORY_DESCRIPTIONS } from '@/mocks/seed/templates';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Form';
import { Modal } from '@/components/ui/Modal';

function BrowseCategoriesModal({
  open,
  proposedCategories,
  removedCategories,
  addedCategories,
  onAdd,
  onRemoveAdded,
  onRemove,
  onClose,
}: {
  open: boolean;
  proposedCategories: TriggerCategory[];
  removedCategories: TriggerCategory[];
  addedCategories: TriggerCategory[];
  onAdd: (cat: TriggerCategory) => void;
  onRemoveAdded: (cat: TriggerCategory) => void;
  onRemove: (cat: TriggerCategory) => void;
  onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Browse all categories" width={560}>
      <div className="flex flex-col gap-4">
        <p className="text-[13px] text-text-muted -mt-1">
          The Classification Agent proposed a subset. Select any others that apply - no reason needed for adding. Removing an AI-proposed tag needs a reason,
          logged to the audit trail - use Remove on its row.
        </p>
        <div className="flex flex-col gap-1">
          {ALL_CATEGORIES.map((cat) => {
            const isProposed = proposedCategories.includes(cat) && !removedCategories.includes(cat);
            const isAdded = addedCategories.includes(cat);
            const checked = isProposed || isAdded;
            return (
              <div key={cat} className={`flex items-center gap-2.5 px-2.5 py-2.5 rounded-md ${isProposed ? '' : 'hover:bg-surface-tertiary'}`}>
                <Checkbox
                  checked={checked}
                  disabled={isProposed}
                  onChange={() => (isAdded ? onRemoveAdded(cat) : onAdd(cat))}
                  label={
                    <span className="text-[13px]">
                      <span className="font-semibold text-text-primary">{cat}</span>
                      {isProposed && <span className="ml-1.5 text-[11px] font-medium text-text-faint">AI-proposed</span>}
                      {isAdded && <span className="ml-1.5 text-[11px] font-semibold text-digital-pavo">Added by you</span>}
                      <span className="block text-text-muted text-[11.5px] mt-0.5">{CATEGORY_DESCRIPTIONS[cat]}</span>
                    </span>
                  }
                />
                {isProposed && (
                  <button onClick={() => onRemove(cat)} className="text-[11.5px] font-semibold text-danger hover:underline cursor-pointer shrink-0 ml-auto">
                    Remove
                  </button>
                )}
              </div>
            );
          })}
        </div>
        <Button variant="primary" size="sm" onClick={onClose} className="self-end">
          Done
        </Button>
      </div>
    </Modal>
  );
}

export function ClassificationCard({
  proposals,
  removedCategories,
  addedCategories,
  confirmed,
  onRemove,
  onAdd,
  onRemoveAdded,
  onConfirm,
}: {
  proposals: ClassificationProposal[];
  removedCategories: TriggerCategory[];
  addedCategories: TriggerCategory[];
  confirmed: TriggerCategory[] | null;
  onRemove: (cat: TriggerCategory) => void;
  onAdd: (cat: TriggerCategory) => void;
  onRemoveAdded: (cat: TriggerCategory) => void;
  onConfirm: () => void;
}) {
  const [browsing, setBrowsing] = useState(false);
  const proposedCategories = proposals.map((p) => p.category);

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="bg-white border border-border-strong rounded-lg p-4 flex flex-col gap-3 max-w-[92%]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12.5px] font-bold text-text-primary">Classification Agent proposes</span>
        {!confirmed && (
          <button onClick={() => setBrowsing(true)} className="text-[11.5px] font-semibold text-carbon-moss hover:underline cursor-pointer shrink-0">
            Browse all categories
          </button>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {proposals.map((p) => {
          const removed = confirmed ? !confirmed.includes(p.category) : removedCategories.includes(p.category);
          return (
            <div key={p.category} className={`flex items-start gap-2.5 rounded-md px-3 py-2.5 border ${removed ? 'border-border-hairline bg-surface-tertiary opacity-50' : 'border-verdant-3 bg-verdant-1'}`}>
              <span className="text-[13px] font-semibold text-text-primary flex-1">
                {p.category}
                {removed && <span className="ml-2 text-[11px] font-normal text-danger">Removed</span>}
                <div className="text-[12px] font-normal text-text-muted mt-0.5">{p.reason}</div>
              </span>
              {!confirmed && !removed && (
                <button onClick={() => onRemove(p.category)} className="text-text-faint hover:text-danger cursor-pointer shrink-0 mt-0.5">
                  <Icon name="close" size={14} />
                </button>
              )}
            </div>
          );
        })}
        {!confirmed &&
          addedCategories.map((cat) => (
            <div key={cat} className="flex items-start gap-2.5 rounded-md px-3 py-2.5 border border-digital-pavo bg-[#EAF3F3]">
              <span className="text-[13px] font-semibold text-text-primary flex-1">
                {cat}
                <span className="ml-2 text-[11px] font-medium text-digital-pavo">Added by you</span>
                <div className="text-[12px] font-normal text-text-muted mt-0.5">{CATEGORY_DESCRIPTIONS[cat]}</div>
              </span>
              <button onClick={() => onRemoveAdded(cat)} className="text-text-faint hover:text-danger cursor-pointer shrink-0 mt-0.5">
                <Icon name="close" size={14} />
              </button>
            </div>
          ))}
      </div>
      {!confirmed && (
        <Button size="sm" variant="primary" onClick={onConfirm} className="self-start">
          Confirm categories
        </Button>
      )}

      <BrowseCategoriesModal
        open={browsing}
        proposedCategories={proposedCategories}
        removedCategories={removedCategories}
        addedCategories={addedCategories}
        onAdd={onAdd}
        onRemoveAdded={onRemoveAdded}
        onRemove={onRemove}
        onClose={() => setBrowsing(false)}
      />
    </motion.div>
  );
}
