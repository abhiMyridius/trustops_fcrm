/**
 * D-119: staff read the owner's activity lines in the third person ("Priya Nair submitted the case (v1)"),
 * as S1 answers them; the owner still reads "You submitted the case (v1)" on their own Activity tab.
 */
import { configure, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

const SEEDED = 'FCRM-2026-0140';

describe('the analyst Activity tab', () => {
  it('names the owner instead of saying "You"', async () => {
    renderApp(`/analyst/case/${SEEDED}/activity`, { as: USERS.rahul });
    const list = await screen.findByRole('list', { name: 'Activity, newest first' });
    expect(list).toHaveTextContent(/submitted the case \(v1\)/);
    expect(list.textContent).not.toMatch(/You (created|submitted)/);
  });
});
