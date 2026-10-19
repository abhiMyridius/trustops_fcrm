/**
 * Close a case under delegated authority (R1): the outcome, the summary the owner reads, and the reason.
 *
 * The dialog used to ask for a reason only and sent `plain_summary: ''` with a fixed outcome, so every
 * delegated close was refused (`CloseCaseRequest.plain_summary` needs words; seen live on FCRM-2026-0152,
 * 19 Oct). The outcome and the summary now start from the locked recommendation and can be edited.
 */
import { useEffect, useState } from 'react';
import { userMessage } from '@/api/errors';
import type { Outcome } from '@/api/types/common';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { REASON_MIN, reasonSchema } from '@/components/ui/Form';
import { OUTCOME_LABEL } from '@/lib/format';

/** `CloseCaseRequest.plain_summary`'s length limit. */
export const SUMMARY_MAX = 2000;

const OUTCOME_OPTIONS = (Object.keys(OUTCOME_LABEL) as Outcome[]).map((value) => ({ value, label: OUTCOME_LABEL[value] }));

export interface CloseCaseValues {
  outcome: Outcome;
  plain_summary: string;
  reason: string;
}

export interface CloseCaseDialogProps {
  open: boolean;
  onClose: () => void;
  /** The locked recommendation's outcome and business summary, when there is one. */
  suggested: { outcome: Outcome; summary: string } | null;
  onConfirm: (v: CloseCaseValues) => Promise<unknown>;
}

export function CloseCaseDialog({ open, onClose, suggested, onConfirm }: CloseCaseDialogProps) {
  const [outcome, setOutcome] = useState<Outcome>(suggested?.outcome ?? 'approve_with_conditions');
  const [summary, setSummary] = useState(suggested?.summary ?? '');
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setOutcome(suggested?.outcome ?? 'approve_with_conditions');
      setSummary(suggested?.summary ?? '');
      setReason('');
      setTouched(false);
      setServerError(null);
    }
    // Reset only when the dialog opens: the recommendation's identity may change on re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const parsedReason = reasonSchema.safeParse(reason);
  const trimmedSummary = summary.trim();
  const summaryOk = trimmedSummary.length > 0 && trimmedSummary.length <= SUMMARY_MAX;
  const reasonError = touched && !parsedReason.success ? parsedReason.error.issues[0]?.message : undefined;
  const summaryError = touched && !summaryOk ? (trimmedSummary ? `At most ${SUMMARY_MAX} characters.` : 'Write what the owner will read.') : undefined;
  const canSave = parsedReason.success && summaryOk && !busy;

  async function save() {
    setTouched(true);
    if (!canSave || !parsedReason.success) return;
    setBusy(true);
    setServerError(null);
    try {
      await onConfirm({ outcome, plain_summary: trimmedSummary, reason: parsedReason.data });
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
      title="Close under delegated authority (R1)"
      description="The outcome and the summary go to the owner. The reason stays in the audit record."
      dismissible={!busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={!canSave} loading={busy}>
            Close case
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
        <Select
          label="Outcome"
          options={OUTCOME_OPTIONS}
          value={outcome}
          onChange={(e) => setOutcome(e.target.value as Outcome)}
          hint={suggested ? `The recommendation suggests: ${OUTCOME_LABEL[suggested.outcome]}.` : undefined}
        />
        <Textarea
          label="Summary for the owner"
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          onBlur={() => setTouched(true)}
          error={summaryError}
          hint={
            suggested
              ? 'Starts from the locked recommendation’s business summary. Plain business words: no score, band or rating.'
              : 'Plain business words: no score, band or rating.'
          }
          rows={5}
        />
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
