import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Button, IconButton } from '@/components/ui/Button';
import { violations } from '../../axe';

describe('Button', () => {
  it('fires onClick and is reachable by keyboard', async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Confirm</Button>);
    const btn = screen.getByRole('button', { name: 'Confirm' });
    await userEvent.tab();
    expect(btn).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('is disabled and busy while loading, and does not fire', async () => {
    const onClick = vi.fn();
    render(
      <Button onClick={onClick} loading>
        Save
      </Button>,
    );
    const btn = screen.getByRole('button', { name: 'Save' });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('defaults to type="button" so it never submits a form by accident', () => {
    render(<Button>Plain</Button>);
    expect(screen.getByRole('button', { name: 'Plain' })).toHaveAttribute('type', 'button');
  });

  it('IconButton takes its accessible name from the required label', () => {
    render(<IconButton label="Close panel" icon={<span aria-hidden>×</span>} />);
    expect(screen.getByRole('button', { name: 'Close panel' })).toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    const { container } = render(
      <div>
        <Button variant="primary">Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="danger" disabled>
          Danger
        </Button>
        <IconButton label="More" icon={<span aria-hidden>…</span>} />
      </div>,
    );
    expect(await violations(container)).toEqual([]);
  });
});
