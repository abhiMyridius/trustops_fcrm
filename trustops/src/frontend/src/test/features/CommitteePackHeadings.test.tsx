/**
 * Heading order on the committee pack page (D-110, axe `heading-order`): the page title is the h1 and
 * each pack section was an h3, skipping a level. A heading may go down only one level at a time.
 */
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { resetMockState } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const CASE = 'FCRM-2026-0151';

describe('committee pack headings', () => {
  it('never skips a level and names the five pack sections as h2', async () => {
    resetMockState('released');
    renderApp(`/committee/case/${CASE}/pack`, { as: USERS.ana });
    expect(await screen.findByText('Override log')).toBeInTheDocument();

    const levels = screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)));
    expect(levels[0]).toBe(1);
    levels.slice(1).forEach((level, i) => {
      const previous = levels[i] as number;
      expect(level - previous, `heading ${i + 2} of ${levels.length}`).toBeLessThanOrEqual(1);
    });

    const sections = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent ?? '');
    expect(sections.some((t) => t.includes('Score and route'))).toBe(true);
    expect(sections.some((t) => t.includes('Override log'))).toBe(true);
    expect(screen.queryAllByRole('heading', { level: 3 })).toHaveLength(0);
  });
});
