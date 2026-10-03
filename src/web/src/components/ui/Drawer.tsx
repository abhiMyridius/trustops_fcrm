import { useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useFocusTrap, useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  side?: 'right' | 'left';
  width?: 'sm' | 'md' | 'lg';
}

const widths = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' };

/** Side panel dialog with the same focus contract as Modal. */
export function Drawer({ open, onClose, title, children, side = 'right', width = 'md' }: DrawerProps) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useStableId('drw');
  useFocusTrap(ref, open, onClose);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex">
      <div aria-hidden className="absolute inset-0 bg-[var(--material-scrim-bg)]" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        tabIndex={-1}
        className={cn('relative flex h-full w-full flex-col bg-surface shadow-xl', widths[width], side === 'right' ? 'ml-auto' : 'mr-auto')}
      >
        <div className="flex items-center justify-between border-b border-border-hairline px-5 py-4">
          <h2 id={`${id}-title`} className="text-base font-bold">
            {title}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close panel" className="cursor-pointer rounded-md p-1 text-text-muted hover:text-text-primary">
            <X aria-hidden size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
