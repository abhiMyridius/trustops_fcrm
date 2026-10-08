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
  primary: 'bg-virtual-tide text-text-on-accent enabled:hover:bg-virtual-tide-hover enabled:hover:-translate-y-px enabled:hover:shadow-accent',
  accent: 'bg-quantum-leaf text-text-on-dark enabled:hover:bg-quantum-leaf-hover enabled:hover:-translate-y-px',
  secondary: 'bg-transparent text-quantum-leaf border-[1.5px] border-current enabled:hover:bg-quantum-leaf/10',
  ghost: 'bg-transparent text-virtual-tide-text underline-offset-4 enabled:hover:underline enabled:hover:bg-surface-secondary',
  danger: 'bg-danger-action text-text-on-accent enabled:hover:brightness-90 enabled:hover:-translate-y-px',
};

const sizes: Record<ButtonSize, string> = {
  sm: 'text-xs px-3 py-1.5 rounded-sm gap-1.5',
  md: 'text-sm px-5 py-2 rounded-full gap-2',
  lg: 'text-base px-7 py-3 rounded-full gap-2.5',
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
        'inline-flex items-center justify-center font-semibold tracking-button whitespace-nowrap transition-[color,background-color,border-color,box-shadow,transform] duration-150 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50',
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
        'inline-flex items-center justify-center rounded-full text-text-secondary hover:bg-virtual-tide-tint hover:text-quantum-leaf cursor-pointer disabled:cursor-not-allowed disabled:text-text-faint',
        size === 'sm' ? 'w-7 h-7' : 'w-9 h-9',
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  );
});
