import type { ReactNode } from 'react';
import clsx from 'clsx';

export function Tabs<T extends string>({ tabs, active, onChange }: { tabs: { id: T; label: ReactNode }[]; active: T; onChange: (id: T) => void }) {
  return (
    <div className="flex gap-1 border-b-[1.5px] border-border" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            'px-4 py-2.5 text-sm cursor-pointer transition-colors -mb-[1.5px] border-b-2 flex items-center',
            active === t.id ? 'font-semibold text-carbon-moss border-carbon-moss' : 'font-medium text-text-muted border-transparent hover:text-text-primary',
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
