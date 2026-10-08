/** Toasts announced politely (aria-live=polite); errors auto-dismiss after 10 s, others after 5 s. */
import { createContext, use, useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import { CheckCircle2, Info, TriangleAlert, X, XCircle } from 'lucide-react';
import { cn } from '@/lib/cn';

export type ToastTone = 'success' | 'info' | 'warning' | 'danger';

interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  detail?: string;
}

interface ToastApi {
  push: (t: Omit<ToastItem, 'id'>) => void;
  success: (title: string, detail?: string) => void;
  error: (title: string, detail?: string) => void;
  info: (title: string, detail?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const icons: Record<ToastTone, ReactNode> = {
  success: <CheckCircle2 aria-hidden size={18} className="text-success" />,
  info: <Info aria-hidden size={18} className="text-info" />,
  warning: <TriangleAlert aria-hidden size={18} className="text-warning" />,
  danger: <XCircle aria-hidden size={18} className="text-danger" />,
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);

  const push = useCallback(
    (t: Omit<ToastItem, 'id'>) => {
      const id = nextId.current++;
      setItems((xs) => [...xs.slice(-3), { ...t, id }]);
      setTimeout(() => dismiss(id), t.tone === 'danger' ? 10_000 : 5000);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (title, detail) => push({ tone: 'success', title, detail }),
      error: (title, detail) => push({ tone: 'danger', title, detail }),
      info: (title, detail) => push({ tone: 'info', title, detail }),
    }),
    [push],
  );

  return (
    <ToastContext value={api}>
      {children}
      <div aria-live="polite" aria-relevant="additions" className="no-print pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(380px,calc(100vw-32px))] flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            role={t.tone === 'danger' ? 'alert' : 'status'}
            className={cn('toast-in pointer-events-auto flex items-start gap-3 rounded-lg border border-border bg-surface p-3.5 shadow-lg')}
          >
            {icons[t.tone]}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-text-primary">{t.title}</p>
              {t.detail && <p className="mt-0.5 text-xs text-text-muted">{t.detail}</p>}
            </div>
            <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss notification" className="cursor-pointer text-text-muted hover:text-text-primary">
              <X aria-hidden size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext>
  );
}

export function useToast(): ToastApi {
  const ctx = use(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}
