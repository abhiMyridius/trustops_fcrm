import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react';
import clsx from 'clsx';
import { Icon } from './Icon';

export function Field({ label, error, help, children }: { label: string; error?: string; help?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[13px] font-semibold text-[#15393e]">{label}</label>
      {children}
      {error && <span className="text-[12px] text-danger">{error}</span>}
      {!error && help && <span className="text-[12px] text-text-muted">{help}</span>}
    </div>
  );
}

const baseInput =
  'w-full px-3 py-2.5 border-[1.5px] rounded-md text-sm font-sans outline-none transition-colors bg-white placeholder:text-text-faint focus:border-carbon-moss focus:ring-[3px] focus:ring-carbon-moss/15 disabled:bg-surface-tertiary disabled:text-text-faint disabled:border-border disabled:cursor-not-allowed disabled:opacity-80';

export function Input({ error, className, ...rest }: InputHTMLAttributes<HTMLInputElement> & { error?: boolean }) {
  return <input className={clsx(baseInput, error ? 'border-danger bg-danger-bg' : 'border-border-strong', className)} {...rest} />;
}

export function Textarea({ error, className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { error?: boolean }) {
  return <textarea className={clsx(baseInput, 'resize-y', error ? 'border-danger bg-danger-bg' : 'border-border-strong', className)} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select className={clsx(baseInput, 'appearance-none pr-9 cursor-pointer', className)} {...rest}>
        {children}
      </select>
      <Icon name="chevron-down" size={15} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted" />
    </div>
  );
}

export function Checkbox({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={clsx('flex items-center gap-2.5 text-left', disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer')}
    >
      <span
        className={clsx(
          'w-[19px] h-[19px] rounded-[5px] flex items-center justify-center shrink-0 transition-colors',
          checked ? 'bg-carbon-moss' : 'border-[1.5px] border-[#8aaeb2]',
        )}
      >
        {checked && <Icon name="check" size={12} className="text-white" strokeWidth={2.4} />}
      </span>
      {label && <span className="text-sm text-text-primary">{label}</span>}
    </button>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={clsx('flex items-center gap-2.5', disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer')}
    >
      <span
        className={clsx('w-10 h-[22px] rounded-full p-0.5 transition-colors shrink-0', checked ? 'bg-carbon-moss' : 'bg-ink-6')}
      >
        <span
          className="block w-[18px] h-[18px] rounded-full bg-white transition-transform duration-150"
          style={{ transform: checked ? 'translateX(18px)' : 'translateX(0)' }}
        />
      </span>
      {label && <span className="text-sm text-text-primary">{label}</span>}
    </button>
  );
}
