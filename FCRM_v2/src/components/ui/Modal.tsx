import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Icon } from './Icon';

export function Modal({ open, onClose, title, children, width = 480 }: { open: boolean; onClose?: () => void; title?: string; children: ReactNode; width?: number }) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-6"
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
            className="bg-white rounded-xl shadow-xl w-full max-h-[86vh] overflow-y-auto"
            style={{ maxWidth: width }}
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            onClick={(e) => e.stopPropagation()}
          >
            {title && (
              <div className="flex items-center justify-between px-6 py-4 border-b border-border-hairline">
                <span className="text-base font-bold text-text-primary">{title}</span>
                {onClose && (
                  <button onClick={onClose} className="text-text-muted hover:text-text-primary cursor-pointer" aria-label="Close">
                    <Icon name="close" size={18} />
                  </button>
                )}
              </div>
            )}
            <div className="p-6">{children}</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
