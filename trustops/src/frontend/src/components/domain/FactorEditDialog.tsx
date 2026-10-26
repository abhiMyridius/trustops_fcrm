/**
 * Edit an area's likelihood and impact with a reason (rule 6). Saving overrides the assessment agent's
 * proposal; the server re-scores the case through the scoring engine, so the client computes nothing.
 * Save stays disabled until one of the two values changed and the reason passes the shared schema.
 */
import { useEffect, useState } from 'react';
import { userMessage } from '@/api/errors';
import type { FactorAnchor } from '@/api/types/analyst';
import { Button } from '@/components/ui/Button';
import { REASON_MIN, reasonSchema } from '@/components/ui/Form';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';

export interface FactorEditDialogProps {
  open: boolean;
  onClose: () => void;
  /** The risk area's name, e.g. "Channel". */
  area: string;
  likelihood: { value: number; scale: FactorAnchor[] };
  impact: { value: number; scale: FactorAnchor[] };
  onSave: (v: { likelihood: number; impact: number; reason: string }) => Promise<unknown> | void;
}

function options(scale: FactorAnchor[]) {
  return scale.map((a) => ({ value: String(a.value), label: `${a.value} · ${a.label}` }));
}

export function FactorEditDialog({ open, onClose, area, likelihood, impact, onSave }: FactorEditDialogProps) {
  const [l, setL] = useState(likelihood.value);
  const [i, setI] = useState(impact.value);
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setL(likelihood.value);
      setI(impact.value);
      setReason('');
      setTouched(false);
      setServerError(null);
    }
    // Reset only when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const parsed = reasonSchema.safeParse(reason);
  const reasonError = touched && !parsed.success ? parsed.error.issues[0]?.message : undefined;
  const changed = l !== likelihood.value || i !== impact.value;
  const canSave = changed && parsed.success && !busy;

  async function save() {
    setTouched(true);
    if (!canSave || !parsed.success) return;
    setBusy(true);
    setServerError(null);
    try {
      await onSave({ likelihood: l, impact: i, reason: parsed.data });
      onClose();
    } catch (err) {
      setServerError(userMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Edit likelihood and impact · ${area}`}
      description={`Proposed likelihood ${likelihood.value} · impact ${impact.value}. Saving re-scores the case.`}
      dismissible={!busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={!canSave} loading={busy}>
            Save and recalculate
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Select label="Likelihood" options={options(likelihood.scale)} value={String(l)} onChange={(e) => setL(Number(e.target.value))} />
          <Select label="Impact" options={options(impact.scale)} value={String(i)} onChange={(e) => setI(Number(e.target.value))} />
        </div>
        {!changed && <p className="text-[13px] text-text-secondary">Change the likelihood or the impact to save; to keep both, confirm them instead.</p>}
        <Textarea
          label="Reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)}
          error={reasonError}
          hint={`At least ${REASON_MIN} characters. ${reason.trim().length} so far.`}
        />
        {serverError && (
          <p role="alert" className="text-[13px] font-semibold text-danger">
            {serverError}
          </p>
        )}
      </form>
    </Modal>
  );
}
