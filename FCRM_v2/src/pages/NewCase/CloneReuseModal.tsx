import type { CloneSuggestion, FieldValue } from '@/types';
import { Modal } from '@/components/ui/Modal';
import { Checkbox } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';

// The expand affordance on a reuse card opens this: every field value on the prior case, one
// checkbox each. Fields already filled on the current case render greyed with a short note
// instead of being hidden - so the PO can see what overlapped without being offered a pointless
// duplicate. Reuse is field-level; there is no reuse-all action.
export function CloneReuseModal({
  suggestion,
  currentFields,
  reusedFieldIds,
  onToggleField,
  onClose,
  onViewFullCase,
}: {
  suggestion: CloneSuggestion | null;
  currentFields: FieldValue[];
  reusedFieldIds: string[];
  onToggleField: (fieldId: string) => void;
  onClose: () => void;
  onViewFullCase: (caseId: string) => void;
}) {
  const alreadyFilled = new Set(currentFields.filter((f) => f.value).map((f) => f.fieldId));

  return (
    <Modal open={!!suggestion} onClose={onClose} title={suggestion?.caseId} width={520}>
      {suggestion && (
        <div className="flex flex-col gap-4">
          <p className="text-[13px] text-text-muted -mt-1">{suggestion.summary}</p>

          <div className="flex flex-col gap-1">
            {suggestion.fields.map((f) => {
              const filled = alreadyFilled.has(f.fieldId);
              return (
                <div key={f.fieldId} className={filled ? 'flex items-start gap-2.5 px-2.5 py-2 rounded-md opacity-60' : 'flex items-start gap-2.5 px-2.5 py-2 rounded-md hover:bg-surface-tertiary'}>
                  <Checkbox
                    checked={reusedFieldIds.includes(f.fieldId)}
                    onChange={() => onToggleField(f.fieldId)}
                    disabled={filled}
                    label={
                      <span className="text-[12.5px]">
                        <span className="font-semibold text-text-primary">{f.label}</span>
                        {filled && <span className="ml-1.5 text-[11px] font-medium text-text-faint">Already filled on this case</span>}
                        <span className="block text-text-muted text-[11.5px] mt-0.5">{f.value}</span>
                      </span>
                    }
                  />
                </div>
              );
            })}
          </div>

          <button
            onClick={() => onViewFullCase(suggestion.caseId)}
            className="self-start flex items-center gap-1.5 text-[12.5px] font-semibold text-carbon-moss hover:underline cursor-pointer"
          >
            <Icon name="external-link" size={13} />
            View full case
          </button>
        </div>
      )}
    </Modal>
  );
}
