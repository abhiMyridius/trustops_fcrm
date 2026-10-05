import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';

export type ButtonVariant = 'primary' | 'accent' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
}

const variants: Record<ButtonVariant, string> = {
  primary: 'bg-carbon-moss text-text-on-dark hover:bg-carbon-moss-hover disabled:bg-border disabled:text-text-muted',
  accent: 'bg-quantum-leaf text-carbon-moss-deep hover:bg-quantum-leaf-tint-1 disabled:bg-border disabled:text-text-muted',
  secondary:
    'bg-surface text-carbon-moss border-[1.5px] border-carbon-moss hover:bg-surface-tertiary disabled:border-border disabled:text-text-muted',
  ghost: 'bg-transparent text-carbon-moss-deep hover:bg-surface-secondary disabled:text-text-muted',
  danger: 'bg-danger text-text-on-dark hover:brightness-90 disabled:bg-border disabled:text-text-muted',
};

const sizes: Record<ButtonSize, string> = {
  sm: 'text-xs px-3 py-1.5 rounded-sm gap-1.5',
  md: 'text-sm px-4 py-2 rounded-md gap-2',
  lg: 'text-base px-6 py-3 rounded-lg gap-2.5',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading = false, icon, disabled, className, children, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'inline-flex items-center justify-center font-semibold whitespace-nowrap transition-colors duration-150 cursor-pointer disabled:cursor-not-allowed',
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Loader2 aria-hidden size={14} className="animate-spin" /> : icon}
      {children}
    </button>
  );
});

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  label: string;
  icon: ReactNode;
  size?: 'sm' | 'md';
}

/** Icon-only button: the label is required and becomes the accessible name. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, size = 'md', className, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex items-center justify-center rounded-md text-text-secondary hover:bg-surface-tertiary hover:text-text-primary cursor-pointer disabled:cursor-not-allowed disabled:text-text-faint',
        size === 'sm' ? 'w-7 h-7' : 'w-9 h-9',
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  );
});
