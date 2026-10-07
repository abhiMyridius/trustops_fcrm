/**
 * WCAG 2 AA for the muted text token (axe `color-contrast`, D-110). jsdom has no layout, so axe cannot
 * check colour in a unit test; the live sweep did, and found `text-text-muted` below 4.5:1 on 28 pages
 * (every table head, the unselected tabs, the pinned-versions line, hashes). This reads the token layer
 * and holds the token to 4.5:1 on every surface and tint it is drawn on.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const TOKENS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../design/tokens.css'), 'utf8');

function token(name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(TOKENS);
  if (!match?.[1]) throw new Error(`no hex token --${name} in design/tokens.css`);
  return match[1];
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** Every background the muted text is drawn on: pages, cards, table heads, tab bars, tints and banners. */
const SURFACES = [
  'color-canvas',
  'color-surface',
  'color-surface-secondary',
  'color-surface-tertiary',
  'ink-2',
  'ink-3',
  'ink-4',
  'color-info-bg',
  'color-success-bg',
  'color-warning-bg',
  'color-danger-bg',
  'color-quantum-leaf-tint-3',
];

describe('muted text colour', () => {
  it.each(SURFACES)('is at least 4.5:1 on %s', (surface) => {
    expect(ratio(token('color-text-muted'), token(surface))).toBeGreaterThanOrEqual(4.5);
  });

  it('stays lighter than secondary text, so the hierarchy between them survives', () => {
    expect(luminance(token('color-text-muted'))).toBeGreaterThan(luminance(token('color-text-secondary')));
    expect(ratio(token('color-text-secondary'), token('color-surface'))).toBeGreaterThan(ratio(token('color-text-muted'), token('color-surface')));
  });

  it('reads the right ratio for a known pair (black on white is 21:1)', () => {
    expect(ratio('#000000', '#ffffff')).toBeCloseTo(21, 5);
  });
});
