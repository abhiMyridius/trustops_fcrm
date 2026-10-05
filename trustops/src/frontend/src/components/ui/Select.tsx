import { forwardRef, type ReactNode, type SelectHTMLAttributes } from 'react';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';
import { FieldShell, control, describedBy } from './Field';

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: ReactNode;
  options: { value: string; label: string; disabled?: boolean }[];
  hint?: ReactNode;
  error?: string;
  hideLabel?: boolean;
  id?: string;
  wrapperClassName?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, options, hint, error, hideLabel, id, className, wrapperClassName, required, ...rest },
  ref,
) {
  const auto = useStableId('sel');
  const fid = id ?? auto;
  return (
    <FieldShell id={fid} label={label} hint={hint} error={error} required={required} hideLabel={hideLabel} className={wrapperClassName}>
      <select
        ref={ref}
        id={fid}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(fid, hint, error)}
        required={required}
        className={cn(control, 'pr-8 cursor-pointer', className)}
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
});
