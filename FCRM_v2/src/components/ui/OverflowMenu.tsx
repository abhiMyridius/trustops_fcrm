import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import clsx from 'clsx';
import { Icon } from './Icon';

export interface OverflowMenuItem {
  label: string;
  onSelect: () => void;
  tone?: 'default' | 'danger';
}

export function OverflowMenu({ items }: { items: OverflowMenuItem[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  return (
    <div className="relative" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Row actions"
        className="w-7 h-7 rounded-md flex items-center justify-center text-text-faint hover:bg-surface-tertiary hover:text-text-secondary cursor-pointer"
      >
        <Icon name="more" size={16} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.14 }}
            className="absolute right-0 z-20 mt-1.5 min-w-[160px] bg-white border border-border-strong rounded-lg shadow-md p-1.5 flex flex-col gap-0.5"
          >
            {items.map((item) => (
              <button
                key={item.label}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
                className={clsx(
                  'text-left px-2.5 py-1.5 rounded-md text-[13px] font-medium cursor-pointer hover:bg-surface-tertiary',
                  item.tone === 'danger' ? 'text-danger' : 'text-text-primary',
                )}
              >
                {item.label}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
