import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import clsx from 'clsx';
import { Icon } from './Icon';
import { Checkbox } from './Form';

export function MultiSelect({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  function toggle(opt: string) {
    onChange(selected.includes(opt) ? selected.filter((s) => s !== opt) : [...selected, opt]);
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          'flex items-center gap-2 px-3.5 py-2 rounded-md border-[1.5px] text-sm font-medium cursor-pointer transition-colors',
          selected.length > 0 ? 'border-carbon-moss text-carbon-moss bg-[#F4F8F7]' : 'border-border-strong text-text-secondary hover:bg-surface-tertiary',
        )}
      >
        {label}
        {selected.length > 0 && <span className="bg-carbon-moss text-white text-[11px] font-bold rounded-full w-4.5 h-4.5 min-w-[18px] px-1 flex items-center justify-center">{selected.length}</span>}
        <Icon name="chevron-down" size={14} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.14 }}
            className="absolute z-20 mt-1.5 min-w-[220px] bg-white border border-border-strong rounded-lg shadow-md p-2 flex flex-col gap-0.5"
          >
            {options.map((opt) => (
              <label key={opt} className="px-2 py-1.5 rounded-md hover:bg-surface-tertiary flex items-center">
                <Checkbox checked={selected.includes(opt)} onChange={() => toggle(opt)} label={<span className="text-[13px]">{opt}</span>} />
              </label>
            ))}
            {selected.length > 0 && (
              <button onClick={() => onChange([])} className="mt-1 text-[12px] text-text-muted hover:text-danger text-left px-2 py-1 cursor-pointer">
                Clear
              </button>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
