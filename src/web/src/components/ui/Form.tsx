/**
 * react-hook-form + zod wrapper. `reasonSchema` is the one reason rule shared by every ReasonDialog
 * and vote rationale (design C §1.5 rule 5): trimmed, at least 20 characters.
 */
import type { FormHTMLAttributes, ReactNode } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm, type FieldValues, type Resolver, type UseFormProps, type UseFormReturn } from 'react-hook-form';
import { z } from 'zod';

export const REASON_MIN = 20;

export const reasonSchema = z
  .string()
  .trim()
  .min(1, 'A reason is required.')
  .min(REASON_MIN, `Give at least ${REASON_MIN} characters so the record explains the decision.`);

/** useForm with the zod resolver; values are typed from the schema's output. */
export function useZodForm<T extends FieldValues>(
  schema: z.ZodType<T>,
  options?: Omit<UseFormProps<T>, 'resolver'>,
): UseFormReturn<T> {
  return useForm<T>({
    ...options,
    // The resolver's generic signature differs between zod v4 input/output types; the runtime contract is the same.
    resolver: zodResolver(schema as never) as unknown as Resolver<T>,
    mode: options?.mode ?? 'onChange',
  });
}

interface FormProps<T extends FieldValues> extends Omit<FormHTMLAttributes<HTMLFormElement>, 'onSubmit' | 'onError'> {
  form: UseFormReturn<T>;
  onValid: (values: T) => void | Promise<void>;
  children: ReactNode;
}

/** A <form> that runs the zod resolver and never submits natively. */
export function Form<T extends FieldValues>({ form, onValid, children, ...rest }: FormProps<T>) {
  return (
    <form
      noValidate
      onSubmit={(e) => {
        void form.handleSubmit(onValid)(e);
      }}
      {...rest}
    >
      {children}
    </form>
  );
}

/** Field-level error text for custom controls. */
export function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="text-xs font-semibold text-danger">
      {message}
    </p>
  );
}
