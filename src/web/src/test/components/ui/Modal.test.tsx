import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { violations } from '../../axe';

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open dialog</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Override CTL-T01" description="A reason is required." footer={<Button onClick={() => setOpen(false)}>Done</Button>}>
        <input aria-label="Reason" />
      </Modal>
    </>
  );
}

describe('Modal', () => {
  it('is a labelled modal dialog that traps focus, closes on Escape and restores focus', async () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Open dialog' });
    await userEvent.click(opener);
    const dialog = screen.getByRole('dialog', { name: 'Override CTL-T01' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleDescription('A reason is required.');

    // Focus starts inside and Tab cycles within the dialog.
    expect(dialog.contains(document.activeElement)).toBe(true);
    for (let i = 0; i < 5; i++) {
      await userEvent.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    await userEvent.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);

    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('has no axe violations when open', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open dialog' }));
    expect(await violations(document.body)).toEqual([]);
  });
});
