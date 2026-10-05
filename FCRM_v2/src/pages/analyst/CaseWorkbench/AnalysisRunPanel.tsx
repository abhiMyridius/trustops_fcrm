import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { AnalysisRun } from '@/types/analyst';
import { getAnalysisRun, rerunStage } from '@/services/analystApi';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Form';
import { Modal } from '@/components/ui/Modal';

function StageRow({ stage, index }: { stage: AnalysisRun['stages'][number]; index: number }) {
  return (
    <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: index * 0.03 }} className="flex gap-3">
      <div className="flex flex-col items-center">
        <span
          className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${
            stage.status === 'done' ? 'bg-teal' : stage.status === 'running' ? 'bg-carbon-moss' : 'bg-ink-5'
          }`}
        >
          {stage.status === 'done' && <Icon name="check" size={12} className="text-white" strokeWidth={3} />}
          {stage.status === 'running' && <span className="w-2.5 h-2.5 rounded-full border-2 border-white border-t-transparent animate-spin" />}
        </span>
        {index < 5 && <span className={`w-px flex-1 min-h-[24px] ${stage.status === 'done' ? 'bg-teal' : 'bg-ink-5'}`} />}
      </div>
      <div className="flex-1 pb-5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-[13.5px] font-semibold ${stage.status === 'queued' ? 'text-text-faint' : 'text-text-primary'}`}>{stage.label}</span>
          {stage.deterministic && (
            <span className="text-[9.5px] font-bold uppercase tracking-wide text-carbon-moss bg-surface-tertiary px-1.5 py-0.5 rounded">Deterministic</span>
          )}
          {stage.status === 'done' && stage.durationMs != null && <span className="text-[11px] text-text-faint">{(stage.durationMs / 1000).toFixed(1)}s</span>}
        </div>
        {stage.status === 'done' && stage.output && <p className="text-[12.5px] text-text-secondary mt-1 leading-snug">{stage.output}</p>}
        {stage.status === 'running' && <p className="text-[12px] text-text-faint mt-1">Running...</p>}
      </div>
    </motion.div>
  );
}

export function AnalysisRunPanel({ caseId, open, canWrite, onClose, onProgress }: { caseId: string; open: boolean; canWrite: boolean; onClose: () => void; onProgress?: () => void }) {
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [rerunOpen, setRerunOpen] = useState(false);
  const [rerunReason, setRerunReason] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    async function poll() {
      const r = await getAnalysisRun(caseId);
      if (cancelled) return;
      setRun(r);
      onProgress?.();
      if (r && !r.completedAt) {
        pollRef.current = setTimeout(poll, 450) as unknown as ReturnType<typeof setInterval>;
      }
    }
    poll();
    return () => {
      cancelled = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, caseId]);

  async function handleRerun() {
    if (!rerunReason.trim()) return;
    await rerunStage(caseId, rerunReason.trim());
    setRerunOpen(false);
    setRerunReason('');
    const r = await getAnalysisRun(caseId);
    setRun(r);
  }

  const done = run?.completedAt != null;

  return (
    <>
      <AnimatePresence>
        {open && (
          <>
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-[rgba(12,34,38,0.45)] z-40" onClick={onClose} />
            <motion.div
              initial={{ x: 440 }}
              animate={{ x: 0 }}
              exit={{ x: 440 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
              className="fixed right-0 top-0 bottom-0 w-[440px] bg-white shadow-xl z-50 flex flex-col"
            >
              <div className="flex items-center justify-between px-5 py-4 border-b border-border-hairline">
                <div>
                  <span className="text-[15px] font-bold text-text-primary block">Analysis run</span>
                  {run?.rerunReason && <span className="text-[11px] text-text-faint">Rerun - {run.rerunReason}</span>}
                </div>
                <button onClick={onClose} className="text-text-muted hover:text-text-primary cursor-pointer">
                  <Icon name="close" size={18} />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto px-5 py-5">
                {!run ? (
                  <p className="text-[13px] text-text-faint">No run yet.</p>
                ) : (
                  <>
                    {run.discardedSummary && (
                      <div className="mb-4 bg-danger-bg border border-[#e3c3ba] rounded-md px-3 py-2.5 text-[12px] text-danger">{run.discardedSummary}</div>
                    )}
                    {run.stages.map((s, i) => (
                      <StageRow key={s.id} stage={s} index={i} />
                    ))}
                  </>
                )}
              </div>

              <div className="px-5 py-4 border-t border-border-hairline flex flex-col gap-2">
                {done && (
                  <p className="text-[12px] text-teal font-semibold flex items-center gap-1.5">
                    <Icon name="check" size={13} /> Run complete.
                  </p>
                )}
                {canWrite && (
                  <>
                    <Button variant="secondary" disabled={!done} onClick={() => setRerunOpen(true)}>
                      Rerun in full
                    </Button>
                    <p className="text-[10.5px] text-text-faint">No per-stage rerun or interruption - one button reruns everything, and it requires a reason.</p>
                  </>
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>

      <Modal open={rerunOpen} onClose={() => setRerunOpen(false)} title="Rerun analysis" width={440}>
        <div className="flex flex-col gap-4">
          <p className="text-[13px] text-text-secondary">
            This discards control overrides, narrative edits, and policy clause removals made since the last run, and runs all six stages again in full. The discarded version stays in
            the case record.
          </p>
          <div className="flex flex-col gap-1.5">
            <label className="text-[12.5px] font-semibold text-[#15393e]">Reason for rerun</label>
            <Textarea rows={3} autoFocus value={rerunReason} onChange={(e) => setRerunReason(e.target.value)} placeholder="Why does this need to run again?" />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRerunOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" disabled={!rerunReason.trim()} onClick={handleRerun}>
              Rerun and discard changes
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
