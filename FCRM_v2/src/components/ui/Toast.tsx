import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Icon } from './Icon';

interface ToastItem {
  id: number;
  message: string;
  tone: 'default' | 'success' | 'danger';
}

const ToastContext = createContext<{ show: (message: string, tone?: ToastItem['tone']) => void } | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx.show;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const show = useCallback((message: string, tone: ToastItem['tone'] = 'default') => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="fixed bottom-6 right-6 z-[100] flex flex-col gap-2 items-end">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.22 }}
              className="flex items-center gap-2.5 bg-carbon-moss-deep text-white px-4 py-3 rounded-lg shadow-lg text-[13.5px] font-medium max-w-sm"
            >
              {t.tone === 'success' && <Icon name="check" size={16} className="text-quantum-leaf shrink-0" />}
              {t.tone === 'danger' && <Icon name="warning" size={16} className="text-[#e08a7a] shrink-0" />}
              {t.message}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
