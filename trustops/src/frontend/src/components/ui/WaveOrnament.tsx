import { useMemo } from 'react';
import { cn } from '@/lib/cn';

export interface WaveOrnamentProps {
  /** Which surface the ornament sits on: dark picks Pulse Green, light picks Virtual Tide. */
  tone?: 'light' | 'dark';
  lines?: number;
  width?: number;
  height?: number;
  /** Deterministic seed so the render (and any test that mounts it) is stable. */
  seed?: number;
  className?: string;
}

/** A tiny deterministic linear congruential generator: same seed, same sequence, every render. */
function makeRng(seed: number): () => number {
  let state = seed % 2147483647;
  if (state <= 0) state += 2147483646;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

/**
 * Brand signature: 100-300 flowing parallel hairline curves fanning up the right edge of a section
 * (guide §6.1). Decorative only — aria-hidden, never carries information. The parent element needs
 * `relative overflow-hidden`; sibling content needs `relative z-10` to sit above it.
 */
export function WaveOrnament({ tone = 'dark', lines = 120, width = 420, height = 900, seed = 7, className }: WaveOrnamentProps) {
  const paths = useMemo(() => {
    const rng = makeRng(seed);
    const band = width * 0.75;
    const baseOpacity = tone === 'dark' ? 0.35 : 0.45;
    return Array.from({ length: lines }, (_, i) => {
      const t = lines > 1 ? i / (lines - 1) : 0;
      const phase = rng() * Math.PI * 2;
      const x = width - band * t;
      const b1 = 90 + 60 * Math.sin(t * Math.PI * 1.6 + phase);
      const b2 = 40 + 50 * Math.cos(t * Math.PI * 1.2 + phase);
      const drift = 24 * Math.sin(t * Math.PI * 3);
      const d = `M ${x},-20 C ${x - b1},${height * 0.33} ${x + b2},${height * 0.66} ${x + drift},${height + 20}`;
      const opacity = baseOpacity * (0.25 + 0.75 * Math.sin(t * Math.PI));
      return { d, opacity, key: i };
    });
  }, [lines, width, height, seed, tone]);

  const stroke = tone === 'dark' ? 'var(--color-pulse-green)' : 'var(--color-virtual-tide)';

  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMaxYMid slice"
      className={cn('pointer-events-none absolute inset-y-0 right-0 h-full w-auto select-none', className)}
    >
      {paths.map((p) => (
        <path key={p.key} d={p.d} fill="none" stroke={stroke} strokeWidth={0.75} strokeLinecap="round" strokeOpacity={p.opacity} vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}
