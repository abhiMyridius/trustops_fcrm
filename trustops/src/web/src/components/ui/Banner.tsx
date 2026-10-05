import type { ReactNode } from 'react';
import { CircleAlert, Info, ShieldCheck, TriangleAlert } from 'lucide-react';
import { cn } from '@/lib/cn';

export type BannerTone = 'info' | 'warning' | 'danger' | 'success' | 'rule';

const tones: Record<BannerTone, { cls: string; icon: ReactNode }> = {
  info: { cls: 'border-info bg-info-bg text-chart-deep-2', icon: <Info aria-hidden size={18} className="text-info" /> },
  warning: { cls: 'border-warning bg-warning-bg text-warning-text', icon: <TriangleAlert aria-hidden size={18} className="text-warning" /> },
  danger: { cls: 'border-danger bg-danger-bg text-danger', icon: <CircleAlert aria-hidden size={18} /> },
  success: { cls: 'border-teal bg-success-bg text-band-1-text', icon: <ShieldCheck aria-hidden size={18} className="text-teal" /> },
  rule: { cls: 'border-carbon-moss bg-quantum-leaf-tint-3 text-carbon-moss-deep', icon: <ShieldCheck aria-hidden size={18} className="text-carbon-moss" /> },
};

/**
 * Inline message. `rule` tone is the "gate shown" rationale line every human gate displays
 * (the text users see explaining why a gate exists, design C §2).
 */
export function Banner({
  tone = 'info',
  title,
  children,
  action,
  live,
  className,
}: {
  tone?: BannerTone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  live?: 'polite' | 'assertive';
  className?: string;
}) {
  const t = tones[tone];
  return (
    <div
      role={live === 'assertive' ? 'alert' : live === 'polite' ? 'status' : undefined}
      className={cn('flex items-start gap-3 rounded-lg border-l-4 px-4 py-3 text-[13px]', t.cls, className)}
    >
      <span className="mt-0.5 shrink-0">{t.icon}</span>
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={cn(title && 'mt-0.5', 'leading-relaxed')}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
