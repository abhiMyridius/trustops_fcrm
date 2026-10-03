import { useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useFocusTrap, useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  /** When false, Escape and the scrim do not close it (e.g. a submit in flight). */
  dismissible?: boolean;
}

const widths = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl' };

/** Dialog: focus trap, Escape to close, focus restored to the opener, labelled by its title. */
export function Modal({ open, onClose, title, description, children, footer, size = 'md', dismissible = true }: ModalProps) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useStableId('dlg');
  useFocusTrap(ref, open, dismissible ? onClose : undefined);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6">
      <div
        aria-hidden
        className="absolute inset-0 bg-[var(--material-scrim-bg)]"
        onClick={dismissible ? onClose : undefined}
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={description ? `${id}-desc` : undefined}
        tabIndex={-1}
        className={cn('relative flex max-h-[88vh] w-full flex-col rounded-xl bg-surface shadow-xl', widths[size])}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border-hairline px-6 py-4">
          <div>
            <h2 id={`${id}-title`} className="text-base font-bold text-text-primary">
              {title}
            </h2>
            {description && (
              <p id={`${id}-desc`} className="mt-1 text-[13px] text-text-muted">
                {description}
              </p>
            )}
          </div>
          {dismissible && (
            <button type="button" onClick={onClose} aria-label="Close dialog" className="cursor-pointer rounded-md p-1 text-text-muted hover:text-text-primary">
              <X aria-hidden size={18} />
            </button>
          )}
        </div>
        <div className="overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-border-hairline px-6 py-3.5">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
