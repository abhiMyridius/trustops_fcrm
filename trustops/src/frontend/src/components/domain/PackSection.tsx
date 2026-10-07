import type { ReactNode } from 'react';
import { Lock, PencilLine } from 'lucide-react';
import { cn } from '@/lib/cn';

/** A section of the decision pack: locked (frozen snapshot) or editable (analyst before lock). */
export function PackSection({
  n,
  title,
  locked = true,
  children,
  className,
}: {
  n: number;
  title: string;
  locked?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-labelledby={`pack-s${n}`} className={cn('print-section rounded-lg border border-border bg-surface p-5 shadow-sm', className)}>
      <header className="mb-3 flex items-center justify-between gap-2">
        <h2 id={`pack-s${n}`} className="text-sm font-bold text-text-primary">
          <span className="mr-2 font-mono text-[11px] text-text-muted">§{n}</span>
          {title}
        </h2>
        <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-text-muted">
          {locked ? <Lock aria-hidden size={11} /> : <PencilLine aria-hidden size={11} />}
          {locked ? 'Frozen' : 'Editable'}
        </span>
      </header>
      <div className="text-[13px] leading-relaxed text-text-primary">{children}</div>
    </section>
  );
}
