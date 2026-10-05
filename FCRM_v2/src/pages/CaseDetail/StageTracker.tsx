import { motion } from 'framer-motion';
import type { CaseStatus } from '@/types';
import { Icon } from '@/components/ui/Icon';
import { formatDate } from '@/utils/format';

type StageId = 'draft' | 'submitted' | 'assessment' | 'committee' | 'closed';

const STAGES: { id: StageId; label: string; statuses: CaseStatus[] }[] = [
  { id: 'draft', label: 'Draft', statuses: ['Draft'] },
  { id: 'submitted', label: 'Submitted', statuses: ['Submitted'] },
  { id: 'assessment', label: 'Under Assessment', statuses: ['Under Assessment'] },
  { id: 'committee', label: 'Committee Review', statuses: ['Committee Review'] },
  { id: 'closed', label: 'Closed', statuses: ['Approved', 'Rejected', 'Closed'] },
];

/** Dates are optional (§8.1 mod, §4) - the PO persona's CaseDetailPage renders this same tracker
 * with none, and gets the pre-existing dateless behaviour unchanged. */
export function StageTracker({
  status,
  outcome,
  delegatedClose,
  dates,
}: {
  status: CaseStatus;
  outcome?: 'Approved' | 'Rejected';
  delegatedClose?: boolean;
  dates?: Partial<Record<StageId, string>>;
}) {
  const currentIndex = STAGES.findIndex((s) => s.statuses.includes(status));
  const committeeIndex = STAGES.findIndex((s) => s.id === 'committee');
  const skipCommittee = delegatedClose && currentIndex === STAGES.length - 1;

  return (
    <div className="flex items-center w-full">
      {STAGES.map((stage, i) => {
        const done = i < currentIndex;
        const active = i === currentIndex;
        const isClosedStage = stage.id === 'closed';
        const isSkippedCommittee = skipCommittee && i === committeeIndex;
        const color = isClosedStage && active ? (outcome === 'Rejected' ? 'var(--color-danger)' : 'var(--color-quantum-leaf)') : done || active ? 'var(--color-carbon-moss)' : 'var(--ink-5)';

        return (
          <div key={stage.id} className="flex items-center flex-1 last:flex-none">
            <div className="flex flex-col items-center gap-1.5 shrink-0" title={isSkippedCommittee ? 'Not required - closed under delegated authority' : undefined}>
              <motion.div
                initial={false}
                animate={{ backgroundColor: isSkippedCommittee ? 'var(--ink-5)' : color, scale: active ? 1.15 : 1 }}
                transition={{ duration: 0.3 }}
                className="w-4 h-4 rounded-full flex items-center justify-center"
              >
                {isSkippedCommittee ? (
                  <Icon name="close" size={8} className="text-text-faint" strokeWidth={3} />
                ) : (
                  done && <Icon name="check" size={9} className="text-white" strokeWidth={3} />
                )}
              </motion.div>
              <span className={`text-[11px] font-semibold whitespace-nowrap ${isSkippedCommittee ? 'text-text-faint line-through decoration-1' : active || done ? 'text-text-primary' : 'text-text-faint'}`}>
                {isClosedStage && active && outcome ? outcome : stage.label}
              </span>
              {isSkippedCommittee && <span className="text-[9.5px] text-text-faint whitespace-nowrap -mt-1">not required</span>}
              {!isSkippedCommittee && (done || active) && dates?.[stage.id] && (
                <span className="text-[9.5px] text-text-faint whitespace-nowrap -mt-1">{formatDate(dates[stage.id]!)}</span>
              )}
            </div>
            {i < STAGES.length - 1 && (
              <div className="flex-1 h-[2px] mx-1.5 bg-ink-5 relative overflow-hidden -mt-4">
                <motion.div
                  className="absolute inset-y-0 left-0 bg-carbon-moss"
                  initial={false}
                  animate={{ width: i < currentIndex ? '100%' : '0%' }}
                  transition={{ duration: 0.4 }}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
