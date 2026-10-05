import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Icon } from '@/components/ui/Icon';

function ProgressTrack({ label, done }: { label: string; done: boolean }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[12.5px] font-semibold text-text-secondary flex items-center gap-1.5">
          {done ? <Icon name="check" size={13} className="text-teal" /> : <span className="w-2.5 h-2.5 rounded-full border-2 border-carbon-moss border-t-transparent animate-spin" />}
          {label}
        </span>
        <span className="text-[11px] text-text-faint">{done ? 'Done' : 'Running…'}</span>
      </div>
      <div className="h-1.5 rounded-full bg-ink-4 overflow-hidden">
        <motion.div
          className="h-full rounded-full bg-carbon-moss"
          initial={{ width: '0%' }}
          animate={{ width: done ? '100%' : '78%' }}
          transition={{ duration: done ? 0.3 : 1.6, ease: 'easeOut' }}
        />
      </div>
    </div>
  );
}

export function ExtractionProgress({ onDone }: { onDone: () => void }) {
  const [extractDone, setExtractDone] = useState(false);
  const [classifyDone, setClassifyDone] = useState(false);

  useEffect(() => {
    const t1 = setTimeout(() => setExtractDone(true), 1500);
    const t2 = setTimeout(() => setClassifyDone(true), 1250);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  useEffect(() => {
    if (extractDone && classifyDone) {
      const t = setTimeout(onDone, 250);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extractDone, classifyDone]);

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="bg-white border border-border-strong rounded-lg p-4 flex flex-col gap-3.5 max-w-[86%]">
      <ProgressTrack label="Extraction Agent - pulling field values" done={extractDone} />
      <ProgressTrack label="Classification Agent - proposing categories" done={classifyDone} />
    </motion.div>
  );
}
