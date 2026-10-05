import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/errors';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { violations } from '../../axe';

const REASON = 'Audit predates the corridor arrangement and covers domestic operations only.';

describe('ReasonDialog (rule 6: overrides need a reason)', () => {
  it('blocks submission with an empty or short reason and submits the trimmed reason', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<ReasonDialog open onClose={onClose} title="Remove clause" onConfirm={onConfirm} />);
    const save = screen.getByRole('button', { name: 'Save with reason' });
    expect(save).toBeDisabled();

    const reason = screen.getByRole('textbox', { name: /Reason/ });
    await userEvent.type(reason, '   ');
    expect(save).toBeDisabled();
    await userEvent.type(reason, 'too short');
    expect(save).toBeDisabled();
    await userEvent.tab();
    expect(screen.getByRole('alert')).toHaveTextContent(/at least 20 characters/);

    await userEvent.clear(reason);
    await userEvent.type(reason, `  ${REASON}  `);
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(onConfirm).toHaveBeenCalledWith({ reason: REASON, value: undefined });
    expect(onClose).toHaveBeenCalled();
  });

  it('requires a changed, in-range value for an override and passes it through', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <ReasonDialog
        open
        onClose={() => undefined}
        title="Review CTL-T01"
        valueField={{ label: 'Your rating (0–10)', initial: 7.6, min: 0, max: 10, step: 0.1 }}
        confirmLabel="Override with reason"
        onConfirm={onConfirm}
      />,
    );
    const save = screen.getByRole('button', { name: 'Override with reason' });
    await userEvent.type(screen.getByRole('textbox', { name: /Reason/ }), REASON);
    expect(save).toBeDisabled(); // value unchanged from the proposal
    const value = screen.getByRole('spinbutton', { name: /Your rating/ });
    await userEvent.clear(value);
    await userEvent.type(value, '11');
    expect(save).toBeDisabled();
    await userEvent.clear(value);
    await userEvent.type(value, '4.5');
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(onConfirm).toHaveBeenCalledWith({ reason: REASON, value: 4.5 });
  });

  it('keeps the dialog open with the typed reason when the server refuses', async () => {
    const onConfirm = vi.fn().mockRejectedValue(new ApiError(409, { type: 'x', title: 'recommendation_locked', status: 409, detail: 'The recommendation is locked.' }));
    const onClose = vi.fn();
    render(<ReasonDialog open onClose={onClose} title="Override" onConfirm={onConfirm} />);
    const reason = screen.getByRole('textbox', { name: /Reason/ });
    await userEvent.type(reason, REASON);
    await userEvent.click(screen.getByRole('button', { name: 'Save with reason' }));
    expect(await screen.findByText('The recommendation is locked.')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(reason).toHaveValue(REASON);
  });

  it('has no axe violations', async () => {
    render(<ReasonDialog open onClose={() => undefined} title="Remove clause" context="FFIEC-TPPP-04" onConfirm={() => undefined} />);
    expect(await violations(document.body)).toEqual([]);
  });
});
