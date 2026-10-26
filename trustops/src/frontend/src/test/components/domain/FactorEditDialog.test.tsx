import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { RiskArea, RiskAreaGate } from '@/api/types/analyst';
import { FactorBlock } from '@/components/domain/FactorBlock';
import { FactorEditDialog } from '@/components/domain/FactorEditDialog';

const SCALE = [1, 2, 3, 4, 5].map((value) => ({ value, label: `level ${value}` }));
const REASON = 'The corridor volume makes a breach far more likely than proposed.';

const AREA: RiskArea & RiskAreaGate = {
  domain: 'channel' as RiskArea['domain'],
  label: 'Channel',
  L: 3,
  I: 4,
  inherent: 12,
  ce: 0.5,
  retention: 0.5,
  residual: 6,
  weight: 0.36,
  contribution: 2.16,
  controls: [],
  matched_caption: '3 controls matched',
  justification: 'Agent justification.',
  lock_blocking: true,
  factor_item_id: 'itm_factor_channel',
  factor_gate_open: true,
};

describe('Likelihood and impact: Edit beside Confirm', () => {
  it('shows Edit next to Confirm while the area is open for review', async () => {
    const onEdit = vi.fn();
    render(<FactorBlock area={AREA} likelihood={SCALE} impact={SCALE} onConfirm={() => undefined} onEdit={onEdit} />);
    expect(screen.getByRole('button', { name: 'Confirm likelihood and impact' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(onEdit).toHaveBeenCalled();
  });

  it('offers neither once the area is decided', () => {
    const decided: RiskArea & RiskAreaGate = { ...AREA, factor_gate_open: false };
    render(<FactorBlock area={decided} likelihood={SCALE} impact={SCALE} onConfirm={() => undefined} onEdit={() => undefined} />);
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm likelihood and impact' })).not.toBeInTheDocument();
  });

  it('saves only a changed pair with a reason, and passes both values', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <FactorEditDialog
        open
        onClose={onClose}
        area="Channel"
        likelihood={{ value: 3, scale: SCALE }}
        impact={{ value: 4, scale: SCALE }}
        onSave={onSave}
      />,
    );
    const save = screen.getByRole('button', { name: 'Save and recalculate' });
    await userEvent.type(screen.getByRole('textbox', { name: /Reason/ }), REASON);
    expect(save).toBeDisabled(); // nothing changed yet

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Likelihood' }), '5');
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(onSave).toHaveBeenCalledWith({ likelihood: 5, impact: 4, reason: REASON });
    expect(onClose).toHaveBeenCalled();
  });
});
