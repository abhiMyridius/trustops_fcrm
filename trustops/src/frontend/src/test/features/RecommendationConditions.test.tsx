/**
 * The analyst edits the recommendation's conditions: change one, remove one, add one, then Save draft sends the
 * whole list (an existing condition by id, a new one without).
 */
import { configure, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { paths } from '@/api/endpoints';
import { resetMockState, server } from '@/mocks/server';
import * as AV from '@/mocks/views/analyst';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

const DEMO = 'FCRM-2026-0151';

describe('the Recommendation tab conditions', () => {
  it('can be edited, removed and added to, then saved as one list', async () => {
    resetMockState('released');
    let sent: { conditions?: { id?: string; text: string }[] } | null = null;
    server.use(
      http.get(paths.cases.workbench(DEMO), () =>
        HttpResponse.json({ ...AV.workbench(DEMO), is_demo: true, review_started: true, locked: false, closed: false }),
      ),
      http.get(paths.cases.recommendation(DEMO), () => HttpResponse.json({ ...AV.recommendation(DEMO), locked: false })),
      http.put(paths.cases.recommendation(DEMO), async ({ request }) => {
        sent = (await request.json()) as typeof sent;
        return HttpResponse.json(AV.recommendation(DEMO));
      }),
    );
    renderApp(`/analyst/case/${DEMO}/recommendation`, { as: USERS.rahul });

    const table = await screen.findByRole('table', { name: 'Recommended conditions' });
    const first = within(table).getAllByRole('button', { name: 'Edit' })[0]!;
    await userEvent.click(first);
    const text = await screen.findByRole('textbox', { name: /^Condition/ });
    await userEvent.clear(text);
    await userEvent.type(text, 'Reworded condition.');
    await userEvent.click(screen.getByRole('button', { name: 'Update condition' }));

    await userEvent.click(within(await screen.findByRole('table', { name: 'Recommended conditions' })).getAllByRole('button', { name: 'Remove' })[1]!);

    await userEvent.click(screen.getByRole('button', { name: 'Add condition' }));
    await userEvent.type(await screen.findByRole('textbox', { name: /^Condition/ }), 'A brand new condition.');
    await userEvent.type(screen.getByRole('textbox', { name: /^Owner/ }), 'FCRM Ops');
    await userEvent.type(screen.getByRole('textbox', { name: /^Due/ }), '2 months');
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add condition' }));

    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));

    await vi.waitFor(() => expect(sent?.conditions).toBeDefined());
    const list = sent!.conditions!;
    expect(list[0]).toMatchObject({ text: 'Reworded condition.' });
    expect(list[0]?.id).toBeTruthy();
    expect(list.at(-1)).toMatchObject({ text: 'A brand new condition.' });
    expect(list.at(-1)?.id).toBeUndefined();
  });
});
