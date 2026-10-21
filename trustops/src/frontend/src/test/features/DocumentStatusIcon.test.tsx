/** PO Documents: each uploaded file shows its state as a line icon whose accessible name and tooltip say it in words. */
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { resetMockState } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const C = 'FCRM-2026-0151';

describe('document status icon', () => {
  it('shows a check with a tooltip, not a text label, once a file is read', async () => {
    resetMockState('submitted');
    renderApp(`/case/${C}/documents`, { as: USERS.priya });
    const list = await screen.findByRole('list', { name: 'Uploaded documents' }, { timeout: 5000 });
    const icons = await screen.findAllByRole('img', { name: 'Read · values extracted' });
    expect(icons.length).toBeGreaterThan(0);
    expect(icons[0]).toHaveAttribute('title', 'Read · values extracted');
    expect(list).not.toHaveTextContent(/\bRead\b(?! ·)/);
  });
});
