import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface CardProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  padded?: boolean;
  as?: 'section' | 'article' | 'div';
  headingLevel?: 2 | 3;
}

/** Raised surface with an optional header. A titled card is a labelled region for screen readers. */
export function Card({ title, subtitle, actions, padded = true, as = 'section', headingLevel = 2, className, children, ...rest }: CardProps) {
  const Tag = as;
  const H = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <Tag className={cn('rounded-lg border border-border bg-surface shadow-sm print-section', className)} {...rest}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border-hairline px-5 py-3.5">
          <div className="min-w-0">
            {title && <H className="text-[15px] font-bold text-text-primary">{title}</H>}
            {subtitle && <p className="mt-0.5 text-xs text-text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn(padded && 'p-5')}>{children}</div>
    </Tag>
  );
}
