/**
 * Band labels and tones ONLY. This module never turns a number into a band: the band always arrives
 * computed by S2 (fcrm_scoring) with the case's pinned model. Ranges shown in the UI come from the
 * server too (`band_range` on the score box). Not importable from features/po (ESLint fence).
 */
import type { BandNo } from '@/api/types/analyst';

export interface BandTone {
  /** Tailwind classes for a badge: tinted background, readable text, coloured edge. */
  badge: string;
  /** Solid swatch (dot, matrix cell). */
  swatch: string;
  /** CSS variable for SVG/recharts fills. */
  cssVar: string;
}

export const BAND_TONE: Record<BandNo, BandTone> = {
  1: { badge: 'bg-band-1-bg text-band-1-text border-band-1', swatch: 'bg-band-1', cssVar: 'var(--band-1)' },
  2: { badge: 'bg-band-2-bg text-band-2-text border-band-2', swatch: 'bg-band-2', cssVar: 'var(--band-2)' },
  3: { badge: 'bg-band-3-bg text-band-3-text border-band-3', swatch: 'bg-band-3', cssVar: 'var(--band-3)' },
  4: { badge: 'bg-band-4-bg text-band-4-text border-band-4', swatch: 'bg-band-4', cssVar: 'var(--band-4)' },
};

export function bandTone(band: BandNo): BandTone {
  return BAND_TONE[band];
}

export function bandName(band: BandNo, label?: string | null): string {
  return label ? `Band ${band} · ${label}` : `Band ${band}`;
}

export function routeLabel(route: 'analyst' | 'committee'): string {
  return route === 'committee' ? 'Risk Committee' : 'Delegated close';
}
