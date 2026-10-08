import type { CSSProperties, ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface ProgressRingProps {
  /** 0..100, supplied by the server. */
  value: number;
  label: string;
  size?: number;
  stroke?: number;
  /** Optional threshold tick (e.g. the 80 % submit threshold). */
  threshold?: number;
  children?: ReactNode;
  tone?: 'teal' | 'warning';
}

/** SVG completion ring with role=progressbar; the centre shows the value (or children). */
export function ProgressRing({ value, label, size = 96, stroke = 9, threshold, children, tone = 'teal' }: ProgressRingProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - clamped / 100);
  const tickAngle = threshold !== undefined ? (threshold / 100) * 2 * Math.PI - Math.PI / 2 : 0;
  const cx = size / 2;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="relative inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="-rotate-90">
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--ink-4)" strokeWidth={stroke} />
        <circle
          cx={cx}
          cy={cx}
          r={r}
          fill="none"
          stroke={tone === 'teal' ? 'var(--color-virtual-tide)' : 'var(--color-warning)'}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          className="ring-animate transition-[stroke-dashoffset] duration-500"
          style={{ '--ring-circumference': `${c}` } as CSSProperties}
        />
      </svg>
      {threshold !== undefined && (
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="absolute inset-0">
          <line
            x1={cx + (r - stroke) * Math.cos(tickAngle)}
            y1={cx + (r - stroke) * Math.sin(tickAngle)}
            x2={cx + (r + stroke) * Math.cos(tickAngle)}
            y2={cx + (r + stroke) * Math.sin(tickAngle)}
            stroke="var(--color-quantum-leaf)"
            strokeWidth={2}
          />
        </svg>
      )}
      <span className={cn('absolute text-center font-bold text-text-primary tabular-nums', size >= 90 ? 'text-xl' : 'text-sm')}>
        {children ?? `${Math.round(clamped)}%`}
      </span>
    </div>
  );
}

export function ProgressBar({ value, label, tone = 'teal', showValue = true }: { value: number; label: string; tone?: 'teal' | 'warning' | 'danger'; showValue?: boolean }) {
  const clamped = Math.max(0, Math.min(100, value));
  const fill = { teal: 'bg-virtual-tide', warning: 'bg-warning', danger: 'bg-danger' }[tone];
  return (
    <div className="flex items-center gap-2">
      <div role="progressbar" aria-label={label} aria-valuenow={Math.round(clamped)} aria-valuemin={0} aria-valuemax={100} className="h-2 flex-1 overflow-hidden rounded-full bg-ink-4">
        <div className={cn('h-full rounded-full transition-[width] duration-500', fill)} style={{ width: `${clamped}%` }} />
      </div>
      {showValue && <span className="w-12 text-right text-xs font-semibold tabular-nums text-text-secondary">{Math.round(clamped)}%</span>}
    </div>
  );
}
