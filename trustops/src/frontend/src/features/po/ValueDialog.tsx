/**
 * Fill in or correct one required value (Overview and Review & submit). A correction becomes "owner
 * declared" and the server keeps the extracted value with its page:line; a value typed into an empty
 * field (the manual path, ADR-75) is owner declared from the start.
 */
import { useState } from 'react';
import { useCorrectValue } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { SourceRef } from '@/api/types/common';
import type { ValueAlternativePO } from '@/api/types/po';
import { SourceChip } from '@/components/domain/SourceChip';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { RadioGroup } from '@/components/ui/Radio';
import { useToast } from '@/components/ui/Toast';

export interface EditableValue {
  field_id: string;
  label: string;
  value: string | null;
  source: SourceRef | null;
  /** Other values found in the documents; the owner may switch to one. */
  alternatives?: ValueAlternativePO[];
}

export function ValueDialog({ caseId, row, onClose }: { caseId: string; row: EditableValue | null; onClose: () => void }) {
  const correct = useCorrectValue(caseId);
  const [value, setValue] = useState(row?.value ?? '');
  const toast = useToast();
  const filling = !row?.value;
  // The value the assistant chose first, then the others it found: one pick fills the box below.
  const found: { value: string; source: SourceRef }[] =
    row?.value && row.source && row.alternatives?.length ? [{ value: row.value, source: row.source }, ...row.alternatives] : [];
  const picked = found.findIndex((f) => f.value === value);
  return (
    <Modal
      open={row !== null}
      onClose={onClose}
      title={`${filling ? 'Fill in' : 'Correct a value'} · ${row?.field_id ?? ''} ${row?.label ?? ''}`}
      description={
        filling
          ? 'Your value is recorded as “owner declared”.'
          : 'Your correction is recorded as “owner declared”.'
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!value.trim() || value === row?.value}
            loading={correct.isPending}
            onClick={() =>
              row &&
              correct.mutate(
                { field_id: row.field_id, value: value.trim() },
                {
                  onSuccess: () => {
                    if (filling) toast.success('Value saved', `${row.field_id} is recorded as owner declared.`);
                    else toast.success('Value corrected', `${row.field_id} is now owner declared.`);
                    onClose();
                  },
                  onError: (err) => toast.error('Not saved', userMessage(err)),
                },
              )
            }
          >
            {filling ? 'Save value' : 'Save correction'}
          </Button>
        </>
      }
    >
      {found.length > 0 ? (
        <div className="mb-4">
          <RadioGroup
            legend="Found in your documents. Pick one, or type your own below."
            name={`found-${row?.field_id ?? ''}`}
            value={picked >= 0 ? String(picked) : null}
            onChange={(index) => setValue(found[Number(index)]?.value ?? value)}
            options={found.map((f, index) => ({
              value: String(index),
              label: (
                <span className="flex flex-wrap items-center gap-2">
                  <span>{f.value}</span>
                  <SourceChip source={f.source} />
                </span>
              ),
              description: index === 0 ? 'Chosen first by the assistant' : undefined,
            }))}
          />
        </div>
      ) : (
        row?.source &&
        row.value && (
          <p className="mb-3 flex items-center gap-2 text-[13px] text-text-secondary">
            Extracted: “{row.value}” <SourceChip source={row.source} />
          </p>
        )
      )}
      <Input label={filling ? 'Value' : 'Corrected value'} value={value} onChange={(e) => setValue(e.target.value)} data-autofocus />
    </Modal>
  );
}
