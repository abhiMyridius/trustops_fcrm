import { motion } from 'framer-motion';
import { useState } from 'react';
import type { CloneSuggestion } from '@/types';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';

// Compact row only - case ID, one-line summary, expand icon. No outcome label, no status dot,
// no insight text: the point is field reuse, not a verdict on the prior case.
function SuggestionRow({ suggestion, onExpand }: { suggestion: CloneSuggestion; onExpand: (caseId: string) => void }) {
  return (
    <button
      onClick={() => onExpand(suggestion.caseId)}
      className="w-full text-left flex items-center gap-3 border border-border-strong rounded-lg px-3.5 py-3 bg-white hover:border-carbon-moss hover:bg-surface-tertiary transition-colors cursor-pointer"
    >
      <span className="text-[12px] font-mono text-text-faint shrink-0">{suggestion.caseId}</span>
      <span className="flex-1 text-[13px] text-text-primary truncate">{suggestion.summary}</span>
      <Icon name="expand" size={15} className="text-carbon-moss shrink-0" />
    </button>
  );
}

export function CloneSuggestionCard({
  suggestions,
  reused,
  onExpand,
  onContinue,
}: {
  suggestions: CloneSuggestion[];
  reused: Record<string, string[]>;
  onExpand: (caseId: string) => void;
  onContinue: () => void;
}) {
  const [dismissed, setDismissed] = useState(false);
  const totalReused = Object.values(reused).reduce((s, arr) => s + arr.length, 0);

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col gap-3 max-w-[92%]">
      <span className="text-[12.5px] font-bold text-text-primary">Similar prior cases - worth reusing before you answer more questions</span>
      <motion.div className="flex flex-col gap-2" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.1 } } }}>
        {suggestions.map((s) => (
          <motion.div key={s.caseId} variants={{ hidden: { opacity: 0, x: 12 }, show: { opacity: 1, x: 0 } }}>
            <SuggestionRow suggestion={s} onExpand={onExpand} />
          </motion.div>
        ))}
      </motion.div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" onClick={() => { setDismissed(true); onContinue(); }} disabled={dismissed}>
          {totalReused > 0 ? `Continue with ${totalReused} reused field${totalReused === 1 ? '' : 's'}` : 'Continue without reusing'}
        </Button>
      </div>
    </motion.div>
  );
}
