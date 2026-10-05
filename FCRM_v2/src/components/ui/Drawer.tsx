import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Icon } from './Icon';

/** A side panel, not a centered dialog - for detail that's entered from a row/cell in a table
 * behind it and benefits from staying anchored to screen edge (scrolls independently, doesn't
 * reflow the table underneath) rather than covering the context it explains. */
export function Drawer({ open, onClose, title, subtitle, children, width = 460 }: { open: boolean; onClose: () => void; title?: string; subtitle?: string; children: ReactNode; width?: number }) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex justify-end"
          style={{ background: 'var(--material-scrim-bg)' }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onClick={onClose}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            className="bg-white h-full shadow-xl overflow-y-auto flex flex-col"
            style={{ width, maxWidth: '92vw' }}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            onClick={(e) => e.stopPropagation()}
          >
            {(title || subtitle) && (
              <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border-hairline sticky top-0 bg-white z-10">
                <div className="flex flex-col gap-0.5">
                  {title && <span className="text-[15px] font-bold text-text-primary">{title}</span>}
                  {subtitle && <span className="text-[12px] text-text-muted">{subtitle}</span>}
                </div>
                <button onClick={onClose} className="text-text-muted hover:text-text-primary cursor-pointer shrink-0" aria-label="Close">
                  <Icon name="close" size={18} />
                </button>
              </div>
            )}
            <div className="p-5 flex flex-col gap-4">{children}</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
