import type { ReactNode } from 'react';
import clsx from 'clsx';

type Tone = 'default' | 'success' | 'warning' | 'danger' | 'info';

const dotColor: Record<Tone, string> = {
  default: '#6b7f80',
  success: '#09A78D',
  warning: '#B98A1E',
  danger: '#8a3324',
  info: '#337077',
};

export function Badge({ tone = 'default', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={clsx('inline-flex items-center gap-1.5 text-[12px] font-semibold text-[#133338]', className)}>
      <span className="w-[6px] h-[6px] rounded-full shrink-0" style={{ background: dotColor[tone] }} />
      {children}
    </span>
  );
}

/** Same visual treatment as the topbar bell's unread badge (§8.8 mod, §5) - a small red circle,
 * white bold count - just inline instead of absolutely positioned, so a number on a tab and a
 * number on the bell read as the same kind of signal. Never rendered at zero; callers check that. */
export function CountPill({ count }: { count: number }) {
  return (
    <span className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-danger text-white text-[9px] font-bold ml-1.5 shrink-0">
      {count}
    </span>
  );
}

export function IllustrativeBadge() {
  return (
    <span
      title="This chart shows what the view looks like at full data volume - it is not computed from live seed data."
      className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-digital-pavo bg-[#EAF3F3] px-2 py-0.5 rounded-full cursor-help"
    >
      Illustrative
    </span>
  );
}

export function Tag({ children, onRemove, className }: { children: ReactNode; onRemove?: () => void; className?: string }) {
  return (
    <span className={clsx('inline-flex items-center gap-1.5 bg-surface-secondary rounded-md px-2.5 py-1 text-[12.5px] font-semibold text-[#15393e]', className)}>
      {children}
      {onRemove && (
        <button onClick={onRemove} className="text-text-muted font-bold hover:text-danger cursor-pointer leading-none" aria-label="Remove">
          ×
        </button>
      )}
    </span>
  );
}

export function LockedBadge({ label = 'FCRM-owned' }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted bg-surface-tertiary px-2 py-1 rounded-full">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3l7 3v6c0 5-3.5 7.5-7 9-3.5-1.5-7-4-7-9V6l7-3z" />
      </svg>
      {label}
    </span>
  );
}
