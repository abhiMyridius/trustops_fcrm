import { cloneElement, isValidElement, useState, type FocusEvent, type KeyboardEvent, type MouseEvent, type ReactElement, type ReactNode } from 'react';
import { useStableId } from '@/lib/a11y';

type TriggerProps = {
  onMouseEnter?: (e: MouseEvent) => void;
  onMouseLeave?: (e: MouseEvent) => void;
  onFocus?: (e: FocusEvent) => void;
  onBlur?: (e: FocusEvent) => void;
  onKeyDown?: (e: KeyboardEvent) => void;
  'aria-describedby'?: string;
};

/**
 * Tooltip for supplementary text only (never the sole carrier of meaning, design C §1.8). The
 * trigger (an interactive child) shows it on hover and focus and hides it on Escape; the trigger
 * gets aria-describedby while it is shown. Handlers are composed with the child's own.
 */
export function Tooltip({ content, children }: { content: ReactNode; children: ReactElement<TriggerProps> }) {
  const [open, setOpen] = useState(false);
  const id = useStableId('tip');
  if (!isValidElement(children)) return children;
  const p = children.props;
  const trigger = cloneElement(children, {
    'aria-describedby': open ? id : p['aria-describedby'],
    onMouseEnter: (e: MouseEvent) => {
      p.onMouseEnter?.(e);
      setOpen(true);
    },
    onMouseLeave: (e: MouseEvent) => {
      p.onMouseLeave?.(e);
      setOpen(false);
    },
    onFocus: (e: FocusEvent) => {
      p.onFocus?.(e);
      setOpen(true);
    },
    onBlur: (e: FocusEvent) => {
      p.onBlur?.(e);
      setOpen(false);
    },
    onKeyDown: (e: KeyboardEvent) => {
      p.onKeyDown?.(e);
      if (e.key === 'Escape') setOpen(false);
    },
  });
  return (
    <span className="relative inline-flex">
      {trigger}
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute bottom-full left-1/2 z-40 mb-2 w-max max-w-64 -translate-x-1/2 rounded-md bg-carbon-moss-deep px-2.5 py-1.5 text-xs font-medium text-text-on-dark shadow-md"
        >
          {content}
        </span>
      )}
    </span>
  );
}
