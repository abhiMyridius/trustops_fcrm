/** ADR-75: New case is a form first; nothing is created until "Create case"; two paths; owner-added types. */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { getDB } from '@/mocks/db';
import { violations } from '../axe';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const created = () => getDB().created;

/** The card whose heading is `title`. */
async function cardOf(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: title });
  const card = heading.closest('section');
  if (!card) throw new Error(`no card titled `);
  return card;
}

async function openForm() {
  const app = renderApp('/case/new', { as: USERS.priya });
  await screen.findByRole('heading', { name: 'New case', level: 1 });
  return app;
}

describe('New case form', () => {
  it('opens from the dashboard button without creating anything', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/po', { as: USERS.priya });
    await user.click(within(await screen.findByRole('main')).getByRole('button', { name: 'New case' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/case/new'));
    expect(created()).toHaveLength(0);
  });

  it('opens from the sidebar button without creating anything', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/queue', { as: USERS.priya });
    const sidebar = await screen.findByRole('complementary', { name: 'Primary' });
    await user.click(within(sidebar).getByRole('button', { name: 'New case' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/case/new'));
    expect(created()).toHaveLength(0);
  });

  it('prefills the business unit and has no axe violations', async () => {
    const { container } = await openForm();
    expect(screen.getByLabelText('Business unit')).toHaveValue('Payments');
    expect(await violations(container)).toEqual([]);
  });

  it('Cancel creates nothing and goes back to the dashboard', async () => {
    const user = userEvent.setup();
    const { router } = await openForm();
    await user.type(screen.getByLabelText(/^Title/), 'Wallet top-ups in Canada');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/po'));
    expect(created()).toHaveLength(0);
  });

  it('needs a title', async () => {
    const user = userEvent.setup();
    await openForm();
    await user.click(screen.getByRole('button', { name: 'Create case' }));
    expect(await screen.findByText('Give the case a title.')).toBeInTheDocument();
    expect(created()).toHaveLength(0);
  });

  it('the manual path needs at least one change type and lists all six', async () => {
    const user = userEvent.setup();
    await openForm();
    await user.type(screen.getByLabelText(/^Title/), 'Wallet top-ups in Canada');
    await user.click(screen.getByLabelText(/I'll choose the change types/));
    const boxes = screen.getAllByRole('checkbox');
    expect(boxes).toHaveLength(6);
    await user.click(screen.getByRole('button', { name: 'Create case' }));
    expect(await screen.findByText('Choose at least one change type.')).toBeInTheDocument();
    expect(created()).toHaveLength(0);
  });

  it('the AI-assisted path creates the case and lands on Documents', async () => {
    const user = userEvent.setup();
    const { router } = await openForm();
    await user.type(screen.getByLabelText(/^Title/), 'Instant payouts for gig workers');
    await user.click(screen.getByRole('button', { name: 'Create case' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/case/FCRM-2026-0152/documents'));
    expect(created()).toHaveLength(1);
    expect(created()[0]).toMatchObject({ title: 'Instant payouts for gig workers', unit: 'Payments', mode: 'ai_assisted', types: [] });
  });

  it('the manual path creates the case with the chosen types and lands on Overview to fill in', async () => {
    const user = userEvent.setup();
    const { router } = await openForm();
    await user.type(screen.getByLabelText(/^Title/), 'Wallet top-ups in Canada');
    const unit = screen.getByLabelText('Business unit');
    await user.clear(unit);
    await user.type(unit, 'Cards');
    await user.click(screen.getByLabelText(/I'll choose the change types/));
    await user.click(screen.getByLabelText('New geography'));
    await user.click(screen.getByRole('button', { name: 'Create case' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/case/FCRM-2026-0152/overview'));
    expect(created()[0]).toMatchObject({ unit: 'Cards', mode: 'manual', types: ['new_geography'] });
    const card = await cardOf('Change types');
    expect(within(card).getByText('New geography')).toBeInTheDocument();
    expect(within(card).getByText('Added by you')).toBeInTheDocument();
    expect(await cardOf('Details to fill in')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^Fill in F/ }).length).toBeGreaterThan(0);
  });
});

describe('owner-added change types on the case', () => {
  it('adds a type from the remaining ones and removes one with a reason', async () => {
    const user = userEvent.setup();
    const { router } = await openForm();
    await user.type(screen.getByLabelText(/^Title/), 'Wallet top-ups in Canada');
    await user.click(screen.getByLabelText(/I'll choose the change types/));
    await user.click(screen.getByLabelText('New geography'));
    await user.click(screen.getByRole('button', { name: 'Create case' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/case/FCRM-2026-0152/overview'));

    const card = await cardOf('Change types');
    const picker = within(card).getByRole('combobox', { name: 'Add change type' });
    // The type the case already holds is not offered again.
    expect(within(picker).queryByRole('option', { name: /^New geography/ })).not.toBeInTheDocument();
    await user.selectOptions(picker, 'vendor_onboarding');
    await user.click(within(card).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(created()[0]?.types).toEqual(['new_geography', 'vendor_onboarding']));
    expect(await within(card).findByText('Vendor / third-party onboarding')).toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: 'Remove New geography' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/^Reason/), 'Canada is served already by the cards unit');
    await user.click(within(dialog).getByRole('button', { name: 'Remove type' }));
    await waitFor(() => expect(created()[0]?.types).toEqual(['vendor_onboarding']));
  });

  it('states the real number of required fields once the types are decided', async () => {
    const user = userEvent.setup();
    const { router } = await openForm();
    await user.type(screen.getByLabelText(/^Title/), 'Wallet top-ups in Canada');
    await user.click(screen.getByLabelText(/I'll choose the change types/));
    await user.click(screen.getByLabelText('New geography'));
    await user.click(screen.getByRole('button', { name: 'Create case' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/case/FCRM-2026-0152/overview'));
    const card = await cardOf('Change types');
    expect(within(card).getByText(/required fields? for this type\./)).toBeInTheDocument();
    expect(within(card).queryByText(/18 for these three types/)).not.toBeInTheDocument();
  });
});
