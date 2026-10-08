/**
 * The one chart palette (design C §1.6/§1.7), drawn from the token layer. Categorical order follows
 * the Myridius chart palette (brand guide §2.4): pulse-green → digital-pavo → virtual-tide → verdant
 * tints → chart-deep tones → neural-beam → chart-tide-2; band charts use band tones.
 */
export const CHART = {
  series: [
    'var(--color-pulse-green)',
    'var(--color-digital-pavo)',
    'var(--color-virtual-tide)',
    'var(--verdant-5)',
    'var(--verdant-3)',
    'var(--chart-tint-6)',
    'var(--chart-deep-1)',
    'var(--chart-deep-2)',
    'var(--color-neural-beam)',
    'var(--chart-tide-2)',
  ],
  single: 'var(--color-virtual-tide)',
  grid: 'var(--color-border-subtle)',
  gridDash: '2 4',
  axis: 'var(--color-text-muted)',
  label: 'var(--color-text-secondary)',
  band: { 1: 'var(--band-1)', 2: 'var(--band-2)', 3: 'var(--band-3)', 4: 'var(--band-4)' } as Record<number, string>,
  highlight: 'var(--color-danger)',
} as const;

export const axisTick = { fill: CHART.axis, fontSize: 11 };
