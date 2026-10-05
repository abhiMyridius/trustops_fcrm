import type { ScoringResult } from '@/types/analyst';
import { Badge } from '@/components/ui/Badge';

const BAND_TONE = { 1: 'success', 2: 'info', 3: 'warning', 4: 'danger' } as const;

export const BAND_RANGES: Record<number, string> = { 1: '0.0-5.0', 2: '5.1-10.0', 3: '10.1-17.0', 4: '17.1-25.0' };

/**
 * Always renders composite residual + band + range + route together (§3.2 rule: never a
 * bare number, never a qualitative label - numeric band only).
 */
export function ScoreBadge({ scoring, compact = false, inline = false }: { scoring: ScoringResult; compact?: boolean; inline?: boolean }) {
  const bandRanges = BAND_RANGES;
  if (inline) {
    return (
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        <span className="text-text-faint">Residual</span>
        <strong className="text-text-primary">{scoring.compositeResidual}</strong>
        <span className="text-text-faint">·</span>
        <Badge tone={BAND_TONE[scoring.band]}>
          Band {scoring.band} of 4 ({bandRanges[scoring.band]})
        </Badge>
        <span className="text-text-faint">·</span>
        <span className="text-text-secondary">{scoring.route}</span>
      </span>
    );
  }
  if (compact) {
    return (
      <span className="inline-flex items-center gap-2">
        <span className="text-[15px] font-bold text-text-primary">{scoring.compositeResidual}</span>
        <Badge tone={BAND_TONE[scoring.band]}>Band {scoring.band} of 4</Badge>
      </span>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2.5">
        <span className="text-[22px] font-bold text-text-primary leading-none">{scoring.compositeResidual}</span>
        <Badge tone={BAND_TONE[scoring.band]}>
          Band {scoring.band} of 4 ({bandRanges[scoring.band]})
        </Badge>
      </div>
      <span className="text-[12px] text-text-muted">{scoring.route}</span>
    </div>
  );
}
