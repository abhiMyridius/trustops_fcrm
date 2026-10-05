import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/** A single figure with its label and an optional context line. Values are server-computed. */
export function KpiCard({ label, value, unit, hint, tone = 'default' }: { label: string; value: ReactNode; unit?: string; hint?: ReactNode; tone?: 'default' | 'attention' }) {
  return (
    <div className={cn('flex flex-col gap-1 rounded-lg border bg-surface px-5 py-4 shadow-sm', tone === 'attention' ? 'border-warning' : 'border-border')}>
      <p className="text-[12px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
      <p className="text-[28px] font-bold leading-tight text-text-primary tabular-nums">
        {value}
        {unit && <span className="ml-1 text-sm font-semibold text-text-secondary">{unit}</span>}
      </p>
      {hint && <p className="text-[12px] text-text-secondary">{hint}</p>}
    </div>
  );
}
