/**
 * Shared shell for form controls: every control has a visible label (never placeholder-only),
 * an optional hint and an error wired with aria-describedby and announced with role=alert.
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

export const control =
  'w-full rounded-md border border-border-strong bg-surface px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-faint hover:border-ink-7 focus:border-virtual-tide disabled:bg-surface-tertiary disabled:text-text-muted aria-[invalid=true]:border-danger';

interface FieldShellProps {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  required?: boolean;
  hideLabel?: boolean;
  children: ReactNode;
  className?: string;
}

export function FieldShell({ id, label, hint, error, required, hideLabel, children, className }: FieldShellProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={id} className={cn('text-[13px] font-semibold text-quantum-leaf', hideLabel && 'sr-only')}>
        {label}
        {required && (
          <span className="text-danger" aria-hidden>
            {' '}
            *
          </span>
        )}
      </label>
      {children}
      {hint && !error && (
        <p id={`${id}-hint`} className="text-xs text-digital-pavo">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-xs font-semibold text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function describedBy(id: string, hint?: ReactNode, error?: string): string | undefined {
  if (error) return `${id}-error`;
  if (hint) return `${id}-hint`;
  return undefined;
}
