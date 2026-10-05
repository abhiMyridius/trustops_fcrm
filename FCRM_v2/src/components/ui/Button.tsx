import type { ButtonHTMLAttributes, ReactNode } from 'react';
import clsx from 'clsx';
import { Icon } from './Icon';

type Variant = 'primary' | 'accent' | 'secondary' | 'ghost' | 'destructive';
type Size = 'sm' | 'md' | 'lg';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

const variantClasses: Record<Variant, string> = {
  primary: 'bg-carbon-moss text-white hover:bg-carbon-moss-hover disabled:bg-border disabled:text-text-faint',
  accent: 'bg-quantum-leaf text-white hover:bg-[#19b48a] disabled:bg-border disabled:text-text-faint',
  secondary: 'bg-white text-carbon-moss border-[1.5px] border-carbon-moss hover:bg-surface-tertiary disabled:border-border disabled:text-text-faint',
  ghost: 'bg-transparent text-[#15393e] hover:bg-surface-secondary disabled:text-text-faint',
  destructive: 'bg-danger text-white hover:brightness-90 disabled:bg-border disabled:text-text-faint',
};

const sizeClasses: Record<Size, string> = {
  sm: 'text-xs px-3.5 py-1.5 rounded-sm gap-1.5',
  md: 'text-sm px-5 py-2.5 rounded-md gap-2',
  lg: 'text-base px-7 py-3.5 rounded-lg gap-2.5',
};

export function Button({ variant = 'secondary', size = 'md', loading, icon, disabled, className, children, ...rest }: Props) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center font-semibold font-sans transition-colors duration-150 cursor-pointer disabled:cursor-not-allowed',
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Icon name="refresh" size={14} className="animate-spin" /> : icon}
      {children}
    </button>
  );
}
