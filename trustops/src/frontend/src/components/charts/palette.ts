/**
 * The one chart palette (design C §1.6/§1.7), drawn from the token layer. Categorical order is
 * carbon-moss → teal → digital-pavo → verdant-6 → chart-deep-1 → warning; band charts use band tones.
 */
export const CHART = {
  series: ['var(--color-carbon-moss)', 'var(--color-teal)', 'var(--color-digital-pavo)', 'var(--verdant-6)', 'var(--chart-deep-1)', 'var(--color-warning)'],
  grid: 'var(--color-border-hairline)',
  axis: 'var(--color-text-muted)',
  label: 'var(--color-text-secondary)',
  band: { 1: 'var(--band-1)', 2: 'var(--band-2)', 3: 'var(--band-3)', 4: 'var(--band-4)' } as Record<number, string>,
  highlight: 'var(--color-danger)',
} as const;

export const axisTick = { fill: CHART.axis, fontSize: 11 };
