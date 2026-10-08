import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/cn';

export type Tone = 'neutral' | 'success' | 'info' | 'warning' | 'danger';

const badgeTone: Record<Tone, string> = {
  neutral: 'bg-surface-secondary text-text-secondary',
  success: 'bg-success-bg text-band-1-text',
  info: 'bg-info-bg text-chart-deep-1',
  warning: 'bg-warning-bg text-warning-text',
  danger: 'bg-danger-bg text-danger',
};

const dotTone: Record<Tone, string> = {
  neutral: 'bg-ink-8',
  success: 'bg-success',
  info: 'bg-info',
  warning: 'bg-warning',
  danger: 'bg-danger',
};

/** Status label. Tone is never the only signal: the text always says what the state is. */
export function Badge({ tone = 'neutral', children, className, icon }: { tone?: Tone; children: ReactNode; className?: string; icon?: ReactNode }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[12px] font-semibold whitespace-nowrap', badgeTone[tone], className)}>
      {icon}
      {children}
    </span>
  );
}

export function StatusDot({ tone = 'neutral', label, pulse }: { tone?: Tone; label: string; pulse?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-text-secondary">
      <span aria-hidden className={cn('h-2 w-2 shrink-0 rounded-full', dotTone[tone], pulse && 'animate-pulse')} />
      {label}
    </span>
  );
}

/** Small count; never rendered at zero (callers pass a positive number). */
export function Pill({ count, label }: { count: number; label: string }) {
  if (count <= 0) return null;
  return (
    <span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold text-text-on-dark">
      {count}
      <span className="sr-only"> {label}</span>
    </span>
  );
}

export function Tag({ children, onRemove, removeLabel, className }: { children: ReactNode; onRemove?: () => void; removeLabel?: string; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-md bg-surface-secondary px-2 py-0.5 text-[12px] font-semibold text-quantum-leaf', className)}>
      {children}
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label={removeLabel ?? 'Remove'} className="cursor-pointer text-text-muted hover:text-danger">
          <X aria-hidden size={12} />
        </button>
      )}
    </span>
  );
}
