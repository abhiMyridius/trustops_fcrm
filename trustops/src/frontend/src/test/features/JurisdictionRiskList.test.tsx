/**
 * Config > Jurisdiction Risk List: every jurisdiction on one 1-5 scale. FATF listings (blacklist 5, grey
 * list 4) are read-only; the internal methodology's ratings are editable into the draft. The anchors explain
 * each level.
 */
import { configure, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { resetMockState } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

async function openList() {
  resetMockState('fresh');
  renderApp('/analyst/config/jurisdiction-risk', { as: USERS.rahul });
  return screen.findByRole('table', { name: 'Jurisdiction risk list' });
}

describe('the jurisdiction risk list', () => {
  it('rates FATF listings read-only and internal ratings editable', async () => {
    const table = await openList();
    const iran = within(table).getByRole('row', { name: /^Iran\b/ });
    expect(within(iran).getByText('5 · Severe')).toBeInTheDocument();
    expect(within(iran).getByText('FATF blacklist (call for action)')).toBeInTheDocument();
    expect(within(iran).queryByRole('button')).not.toBeInTheDocument();

    const kenya = within(table).getByRole('row', { name: /^Kenya\b/ });
    expect(within(kenya).getByText('4 · High')).toBeInTheDocument();
    expect(within(kenya).queryByRole('button')).not.toBeInTheDocument();

    const mexico = within(table).getByRole('row', { name: /^Mexico\b/ });
    expect(within(mexico).getByText('3 · Elevated')).toBeInTheDocument();
    expect(within(mexico).getByText('Internal AML jurisdiction methodology')).toBeInTheDocument();
    expect(within(mexico).getByRole('button', { name: 'Edit Mexico risk rating (1–5)' })).toBeInTheDocument();
  });

  it('finds a jurisdiction by name', async () => {
    const table = await openList();
    await userEvent.type(screen.getByRole('textbox', { name: 'Find a jurisdiction' }), 'phil');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('Philippines');
  });

  it('explains the five levels of the scale', async () => {
    await openList();
    await userEvent.click(screen.getByText('Show the five levels'));
    expect(screen.getByText('Severe')).toBeInTheDocument();
    expect(screen.getByText(/FATF blacklist, comprehensive sanctions program/)).toBeInTheDocument();
  });
});
