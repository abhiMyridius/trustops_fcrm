/**
 * The one dialog for every "reason required" action (rule 6): overrides, removals, unlocks, re-tags,
 * discards. Save stays disabled until the reason passes the shared schema (trimmed, >= 20 chars) and
 * the value (when asked for) is valid. On a server error the dialog stays open with the text kept.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { userMessage } from '@/api/errors';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { REASON_MIN, reasonSchema } from '@/components/ui/Form';

export interface ReasonValueField {
  label: string;
  initial: number;
  min: number;
  max: number;
  step: number;
}

export interface ReasonDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** e.g. "proposed 7.6 · evidence: audit 2023-11 · weight 0.35" */
  context?: ReactNode;
  /** What happens when this is saved, in plain words. */
  consequence?: ReactNode;
  valueField?: ReasonValueField;
  /** Live consequence preview for a candidate value (e.g. scoring/simulate). */
  preview?: (value: number) => ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: (v: { reason: string; value?: number }) => Promise<unknown> | void;
}

export function ReasonDialog({
  open,
  onClose,
  title,
  context,
  consequence,
  valueField,
  preview,
  confirmLabel = 'Save with reason',
  danger,
  onConfirm,
}: ReasonDialogProps) {
  const [reason, setReason] = useState('');
  const [value, setValue] = useState<string>(valueField ? String(valueField.initial) : '');
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setReason('');
      setValue(valueField ? String(valueField.initial) : '');
      setTouched(false);
      setServerError(null);
    }
    // Reset only when the dialog opens; the value field's identity may change on re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const parsed = reasonSchema.safeParse(reason);
  const reasonError = touched && !parsed.success ? parsed.error.issues[0]?.message : undefined;
  const num = Number(value);
  const valueOk =
    !valueField || (value.trim() !== '' && Number.isFinite(num) && num >= valueField.min && num <= valueField.max && num !== valueField.initial);
  const valueError =
    valueField && value.trim() !== '' && !valueOk
      ? num === valueField.initial
        ? 'Enter the value you are changing it to.'
        : `Enter a value between ${valueField.min} and ${valueField.max}.`
      : undefined;
  const canSave = parsed.success && valueOk && !busy;

  async function save() {
    setTouched(true);
    if (!canSave || !parsed.success) return;
    setBusy(true);
    setServerError(null);
    try {
      await onConfirm({ reason: parsed.data, value: valueField ? num : undefined });
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
      title={title}
      description={context}
      dismissible={!busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={() => void save()} disabled={!canSave} loading={busy}>
            {confirmLabel}
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
        {valueField && (
          <Input
            label={valueField.label}
            type="number"
            inputMode="decimal"
            min={valueField.min}
            max={valueField.max}
            step={valueField.step}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            error={valueError}
          />
        )}
        {valueField && preview && valueOk && <div aria-live="polite">{preview(num)}</div>}
        <Textarea
          label="Reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)}
          error={reasonError}
          hint={`At least ${REASON_MIN} characters. ${reason.trim().length} so far.`}
          data-autofocus
          required
        />
        {consequence && <p className="text-[13px] text-text-secondary">{consequence}</p>}
        {serverError && (
          <p role="alert" className="text-[13px] font-semibold text-danger">
            {serverError}
          </p>
        )}
      </form>
    </Modal>
  );
}
