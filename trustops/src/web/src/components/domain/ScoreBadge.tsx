/**
 * Score + band, exactly as S2 returned them for the case's pinned model. Never computes a band.
 * Analyst and committee only: features/po cannot import this file (ESLint no-restricted-imports).
 */
import { TriangleAlert } from 'lucide-react';
import type { BandNo } from '@/api/types/analyst';
import { bandName, bandTone } from '@/lib/bands';
import { formatFixed } from '@/lib/format';
import { cn } from '@/lib/cn';

export interface ScoreBadgeProps {
  composite: number;
  band: BandNo;
  bandLabel?: string | null;
  bandRange?: string | null;
  routeLabel?: string | null;
  boundaryText?: string | null;
  size?: 'sm' | 'lg';
}

export function ScoreBadge({ composite, band, bandLabel, bandRange, routeLabel, boundaryText, size = 'sm' }: ScoreBadgeProps) {
  const tone = bandTone(band);
  if (size === 'sm') {
    return (
      <span className={cn('inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[12px] font-bold tabular-nums', tone.badge)}>
        {formatFixed(composite)}
        <span className="font-semibold">· {bandName(band)}</span>
      </span>
    );
  }
  return (
    <div className={cn('inline-flex flex-col gap-1 rounded-lg border-l-4 px-4 py-3', tone.badge)}>
      <div className="flex items-baseline gap-3">
        <span className="text-3xl font-bold tabular-nums">{formatFixed(composite)}</span>
        <span className="text-sm font-bold">
          {bandName(band, bandLabel)} of 4{bandRange ? ` (${bandRange})` : ''}
        </span>
      </div>
      {routeLabel && <span className="text-[13px] font-semibold">Route: {routeLabel}</span>}
      {boundaryText && (
        <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold">
          <TriangleAlert aria-hidden size={13} />
          {boundaryText}
        </span>
      )}
    </div>
  );
}

/** Band-only chip for tables (band arrives from the server; label printed, colour secondary). */
export function BandChip({ band, label }: { band: BandNo; label?: string | null }) {
  return <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-[12px] font-semibold', bandTone(band).badge)}>{bandName(band, label)}</span>;
}
