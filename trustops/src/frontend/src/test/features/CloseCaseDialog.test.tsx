import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CloseCaseDialog } from '@/features/analyst/CloseCaseDialog';

const REASON = 'Delegated authority applies: the residual sits inside the analyst band.';
const SUMMARY = 'The change can go ahead once the payout partner sends its licence.';

// Seen live on FCRM-2026-0152 (19 Oct): the delegated close sent `plain_summary: ''` with a fixed outcome,
// and the API (summary required) refused every close with a 422.
describe('CloseCaseDialog (delegated close, R1)', () => {
  it('starts from the locked recommendation and sends the outcome, the summary and the reason', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <CloseCaseDialog open onClose={() => undefined} suggested={{ outcome: 'defer', summary: SUMMARY }} onConfirm={onConfirm} />,
    );
    const close = screen.getByRole('button', { name: 'Close case' });
    expect(screen.getByRole('combobox', { name: /Outcome/ })).toHaveValue('defer');
    expect(screen.getByRole('textbox', { name: /Summary for the owner/ })).toHaveValue(SUMMARY);
    expect(close).toBeDisabled(); // no reason yet

    await userEvent.type(screen.getByRole('textbox', { name: /Reason/ }), REASON);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /Outcome/ }), 'approve_with_conditions');
    await userEvent.click(close);

    expect(onConfirm).toHaveBeenCalledWith({ outcome: 'approve_with_conditions', plain_summary: SUMMARY, reason: REASON });
  });

  it('will not close without a summary for the owner', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<CloseCaseDialog open onClose={() => undefined} suggested={null} onConfirm={onConfirm} />);
    await userEvent.type(screen.getByRole('textbox', { name: /Reason/ }), REASON);
    const close = screen.getByRole('button', { name: 'Close case' });
    expect(close).toBeDisabled();

    await userEvent.type(screen.getByRole('textbox', { name: /Summary for the owner/ }), '   ');
    expect(close).toBeDisabled();
    await userEvent.type(screen.getByRole('textbox', { name: /Summary for the owner/ }), SUMMARY);
    expect(close).toBeEnabled();
    await userEvent.click(close);
    expect(onConfirm).toHaveBeenCalledWith({ outcome: 'approve_with_conditions', plain_summary: SUMMARY, reason: REASON });
  });
});
