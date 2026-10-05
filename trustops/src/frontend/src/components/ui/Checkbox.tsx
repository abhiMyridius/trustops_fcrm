import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'id'> {
  label: ReactNode;
  description?: ReactNode;
  id?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, description, id, className, ...rest },
  ref,
) {
  const auto = useStableId('cb');
  const fid = id ?? auto;
  return (
    <div className={cn('flex items-start gap-2.5', className)}>
      <input
        ref={ref}
        id={fid}
        type="checkbox"
        aria-describedby={description ? `${fid}-desc` : undefined}
        className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-carbon-moss disabled:cursor-not-allowed"
        {...rest}
      />
      <div className="flex flex-col">
        <label htmlFor={fid} className="text-sm text-text-primary cursor-pointer">
          {label}
        </label>
        {description && (
          <span id={`${fid}-desc`} className="text-xs text-text-muted">
            {description}
          </span>
        )}
      </div>
    </div>
  );
});
