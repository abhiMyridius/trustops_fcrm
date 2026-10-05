import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import type { ValidationResult } from '@/types';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';

function StepRow({ label, status, detail }: { label: string; status: 'pending' | 'running' | 'passed' | 'failed'; detail?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -6 }}
      animate={status === 'failed' ? { opacity: 1, x: [0, -6, 6, -4, 4, 0] } : { opacity: status === 'pending' ? 0.4 : 1, x: 0 }}
      transition={{ duration: status === 'failed' ? 0.4 : 0.25 }}
      className={`flex flex-col gap-1.5 rounded-md px-3.5 py-3 ${status === 'failed' ? 'bg-danger-bg' : status === 'passed' ? 'bg-verdant-1' : 'bg-surface-tertiary'}`}
    >
      <div className="flex items-center gap-2.5">
        {status === 'running' && <span className="w-4 h-4 rounded-full border-2 border-carbon-moss border-t-transparent animate-spin shrink-0" />}
        {status === 'pending' && <span className="w-4 h-4 rounded-full border-2 border-ink-6 shrink-0" />}
        {status === 'passed' && (
          <span className="w-4 h-4 rounded-full bg-teal flex items-center justify-center shrink-0">
            <Icon name="check" size={11} className="text-white" strokeWidth={3} />
          </span>
        )}
        {status === 'failed' && (
          <span className="w-4 h-4 rounded-full bg-danger flex items-center justify-center shrink-0">
            <Icon name="close" size={11} className="text-white" strokeWidth={3} />
          </span>
        )}
        <span className={`text-[13px] font-medium ${status === 'failed' ? 'text-danger' : 'text-text-primary'}`}>{label}</span>
      </div>
      {detail && status !== 'pending' && <p className="text-[12px] text-text-secondary pl-6.5 ml-[26px]">{detail}</p>}
    </motion.div>
  );
}

export function ValidationModal({
  open,
  result,
  onClose,
  onReopen,
}: {
  open: boolean;
  result: ValidationResult | null;
  onClose: () => void;
  onReopen: () => void;
}) {
  const [revealed, setRevealed] = useState(0);

  useEffect(() => {
    if (!open || !result) {
      setRevealed(0);
      return;
    }
    setRevealed(0);
    let i = 0;
    const interval = setInterval(() => {
      i += 1;
      setRevealed(i);
      if (i >= result.steps.length) clearInterval(interval);
    }, 550);
    return () => clearInterval(interval);
  }, [open, result]);

  if (!result) return null;
  const allRevealed = revealed >= result.steps.length;

  return (
    <Modal open={open} onClose={allRevealed ? onClose : undefined} title="Submission Validation Agent" width={520}>
      <div className="flex flex-col gap-2.5">
        {result.steps.map((step, i) => {
          const status = i >= revealed ? 'pending' : i === revealed - 1 && !allRevealed ? 'running' : step.passed ? 'passed' : 'failed';
          return <StepRow key={step.id} label={step.label} status={i < revealed ? (step.passed ? 'passed' : 'failed') : status} detail={i < revealed ? step.detail : undefined} />;
        })}

        {allRevealed && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="pt-2 flex flex-col gap-3">
            {result.passed ? (
              <>
                <p className="text-[13.5px] text-teal font-semibold">All checks passed - {result.completionPct}% complete.</p>
                <Button variant="primary" onClick={onClose}>
                  Continue
                </Button>
              </>
            ) : (
              <>
                {(() => {
                  const thresholdFailed = result.completionPct < result.threshold;
                  const neverBlankFailed = result.missingNeverBlankLabels.length > 0;
                  if (thresholdFailed && neverBlankFailed) {
                    return (
                      <p className="text-[13.5px] text-danger font-medium">
                        Two separate failures. Below completion threshold at {result.completionPct}% (requires {result.threshold}%), and
                        {' '}{result.missingNeverBlankLabels.length} always-required field{result.missingNeverBlankLabels.length === 1 ? '' : 's'} still empty regardless of that percentage.
                      </p>
                    );
                  }
                  if (neverBlankFailed) {
                    return (
                      <p className="text-[13.5px] text-danger font-medium">
                        Always-required fields still empty, independent of the {result.completionPct}% completion (threshold met at {result.threshold}%).
                      </p>
                    );
                  }
                  return (
                    <p className="text-[13.5px] text-danger font-medium">
                      Below completion threshold. {result.completionPct}% complete - the field set requires {result.threshold}%.
                    </p>
                  );
                })()}
                <div className="flex justify-end gap-2">
                  <Button variant="ghost" onClick={onClose}>
                    Close
                  </Button>
                  <Button variant="primary" onClick={onReopen}>
                    Reopen intake chat
                  </Button>
                </div>
              </>
            )}
          </motion.div>
        )}
      </div>
    </Modal>
  );
}
