/**
 * The picture of a chart is hidden from assistive technology (the same figures are in a visually hidden
 * table), and a hidden region must hold nothing a keyboard can land on: axe rule aria-hidden-focus
 * (D-110). recharts' own accessibility layer puts tabindex="0" on the svg, which is what failed on the
 * analyst dashboard.
 */
import { render } from '@testing-library/react';
import { configureAxe } from 'vitest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChartCard } from '@/components/charts/ChartCard';
import { SimpleBarChart } from '@/components/charts/Charts';

/** jsdom has no layout, so a responsive chart never gets a size; this observer reports a 400 x 200 box on observe. */
class SizedObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element): void {
    this.callback([{ target, contentRect: { width: 400, height: 200 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve(): void {}
  disconnect(): void {}
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', SizedObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const axeHiddenFocus = configureAxe({ rules: { 'aria-hidden-focus': { enabled: true } } });

const DATA = [
  { label: 'Band 1', value: 19 },
  { label: 'Band 2', value: 49 },
  { label: 'Band 3', value: 11 },
];

function renderChart(layout: 'horizontal' | 'vertical') {
  return render(
    <ChartCard title="Band distribution" insight="Most changes land in Band 2." data={DATA.map((d) => ({ label: d.label, value: d.value }))}>
      <SimpleBarChart layout={layout} data={DATA} />
    </ChartCard>,
  );
}

describe('ChartCard', () => {
  it.each(['horizontal', 'vertical'] as const)('draws a %s chart that holds nothing focusable inside its hidden region', (layout) => {
    const { container } = renderChart(layout);
    const hidden = container.querySelector('[aria-hidden="true"].w-full');
    expect(hidden).not.toBeNull();
    expect(hidden?.querySelector('svg')).not.toBeNull(); // the chart really rendered
    const reachable = hidden?.querySelectorAll('[tabindex]:not([tabindex="-1"]), a[href], button, input, select, textarea');
    expect([...(reachable ?? [])].map((el) => el.outerHTML.slice(0, 80))).toEqual([]);
  });

  it('passes the axe aria-hidden-focus rule', async () => {
    const { container } = renderChart('vertical');
    const result = await axeHiddenFocus(container);
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
  });

  it('still gives the figures to a screen reader as a table', () => {
    const { getByRole } = renderChart('horizontal');
    const table = getByRole('table', { name: 'Band distribution', hidden: true });
    expect(table).toHaveTextContent('Band 249');
  });
});
