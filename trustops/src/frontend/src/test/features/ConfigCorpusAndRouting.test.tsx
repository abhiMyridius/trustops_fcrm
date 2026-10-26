/**
 * Config > Policy Corpus: search the whole corpus, and open a source to read all its clauses verbatim.
 * Config > Rules & Routing: the case SLA shows in place of the committee quorum and is not edited there.
 */
import { configure, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { resetMockState } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

describe('the Policy Corpus page', () => {
  it('labels what each source is used for and the sections ingested', async () => {
    resetMockState('fresh');
    renderApp('/analyst/config/policy-corpus', { as: USERS.rahul });
    expect((await screen.findAllByText('Used for')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Sections ingested').length).toBeGreaterThan(0);
  });

  it('opens a source to list its clauses, each opening to its verbatim text', async () => {
    resetMockState('fresh');
    renderApp('/analyst/config/policy-corpus', { as: USERS.rahul });
    await userEvent.click(await screen.findByRole('button', { name: /^View the \d+ clauses of FFIEC BSA\/AML Examination Manual$/ }));
    const panel = await screen.findByRole('dialog', { name: 'FFIEC BSA/AML Examination Manual' });
    const items = await within(panel).findAllByRole('listitem');
    expect(items.length).toBeGreaterThan(0);
    const first = items[0];
    if (!first) throw new Error('no clause listed');
    const quote = first.querySelector('blockquote');
    expect(quote?.textContent?.trim()).toBeTruthy();
  });

  it('searches the corpus and lists the matching clauses', async () => {
    resetMockState('fresh');
    renderApp('/analyst/config/policy-corpus', { as: USERS.rahul });
    const search = await screen.findByRole('search');
    await userEvent.type(within(search).getByRole('textbox', { name: 'Search clauses' }), 'due diligence');
    await userEvent.click(within(search).getByRole('button', { name: 'Search' }));
    expect(await screen.findByText(/clauses? for “due diligence”/)).toBeInTheDocument();
  });
});

describe('the Rules & Routing parameters', () => {
  it('show the case SLA, fixed, and no committee quorum', async () => {
    resetMockState('fresh');
    renderApp('/analyst/config/routing-rules', { as: USERS.rahul });
    const table = await screen.findByRole('table', { name: 'Routing parameters' });
    const sla = within(table).getByRole('row', { name: /SLA target for closing a case/ });
    expect(within(sla).getByText('2 business days')).toBeInTheDocument();
    expect(within(sla).queryByRole('button')).not.toBeInTheDocument();
    expect(within(table).queryByText(/Committee quorum/)).not.toBeInTheDocument();
    const recency = within(table).getByRole('row', { name: /Evidence recency/ });
    expect(within(recency).getByRole('button')).toBeInTheDocument();
  });
});
