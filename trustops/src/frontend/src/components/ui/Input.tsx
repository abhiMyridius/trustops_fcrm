import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';
import { FieldShell, control, describedBy } from './Field';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  hideLabel?: boolean;
  id?: string;
  wrapperClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, hideLabel, id, className, wrapperClassName, required, ...rest },
  ref,
) {
  const auto = useStableId('in');
  const fid = id ?? auto;
  return (
    <FieldShell id={fid} label={label} hint={hint} error={error} required={required} hideLabel={hideLabel} className={wrapperClassName}>
      <input
        ref={ref}
        id={fid}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(fid, hint, error)}
        required={required}
        className={cn(control, className)}
        {...rest}
      />
    </FieldShell>
  );
});
