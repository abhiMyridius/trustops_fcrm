/**
 * Config > Categories & Fields shows each change type's FFIEC AML risk categories: the risk areas a case of
 * that type is assessed in on Risk & Controls. The mapping is read-only.
 */
import { configure, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { resetMockState } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

describe('the change type to AML risk category table', () => {
  it('maps every change type to its categories, with no control to edit them', async () => {
    resetMockState('fresh');
    renderApp('/analyst/config/field-sets', { as: USERS.rahul });
    const table = await screen.findByRole('table', { name: 'Change type to FFIEC AML risk category' });
    const row = (type: RegExp) => within(table).getByRole('row', { name: type });

    expect(within(row(/Vendor \/ third-party onboarding/i)).getByText('Delivery channel and third party')).toBeInTheDocument();
    expect(within(row(/New geography/i)).getByText('Geography')).toBeInTheDocument();
    const process = row(/Process change/i);
    expect(within(process).getByText('Product and service')).toBeInTheDocument();
    expect(within(process).getByText('Customer and entity')).toBeInTheDocument();
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 6);
    expect(within(table).queryByRole('combobox')).not.toBeInTheDocument();
  });
});
