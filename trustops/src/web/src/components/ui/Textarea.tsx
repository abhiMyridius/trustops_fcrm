import { forwardRef, type ReactNode, type TextareaHTMLAttributes } from 'react';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';
import { FieldShell, control, describedBy } from './Field';

export interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id'> {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  hideLabel?: boolean;
  id?: string;
  wrapperClassName?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, hint, error, hideLabel, id, className, wrapperClassName, rows = 4, required, ...rest },
  ref,
) {
  const auto = useStableId('ta');
  const fid = id ?? auto;
  return (
    <FieldShell id={fid} label={label} hint={hint} error={error} required={required} hideLabel={hideLabel} className={wrapperClassName}>
      <textarea
        ref={ref}
        id={fid}
        rows={rows}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(fid, hint, error)}
        required={required}
        className={cn(control, 'resize-y leading-relaxed', className)}
        {...rest}
      />
    </FieldShell>
  );
});
