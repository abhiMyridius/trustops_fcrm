import type { ReactNode } from 'react';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface SwitchProps {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  description?: ReactNode;
}

/** WAI-ARIA switch: a button with role=switch and aria-checked, named by its visible label. */
export function Switch({ label, checked, onChange, disabled, description }: SwitchProps) {
  const id = useStableId('sw');
  return (
    <div className="flex items-center gap-3">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        aria-describedby={description ? `${id}-desc` : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60',
          checked ? 'bg-teal' : 'bg-ink-7',
        )}
      >
        <span
          aria-hidden
          className={cn('inline-block h-4 w-4 rounded-full bg-surface shadow-sm transition-transform', checked ? 'translate-x-4.5' : 'translate-x-0.5')}
        />
      </button>
      <div className="flex flex-col">
        <span id={`${id}-label`} className="text-sm text-text-primary">
          {label}
        </span>
        {description && (
          <span id={`${id}-desc`} className="text-xs text-text-muted">
            {description}
          </span>
        )}
      </div>
    </div>
  );
}
