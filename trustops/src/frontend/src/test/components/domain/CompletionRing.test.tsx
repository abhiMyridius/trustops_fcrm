import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CompletionRing } from '@/components/domain/CompletionRing';
import { violations } from '../../axe';

const CORE_DONE = { filled: 6, required: 6, pct: 100, threshold_pct: 80, always_required_filled: 6, always_required_total: 6 };

describe('CompletionRing', () => {
  it('reads the server figures and says the total can grow while change types are pending', async () => {
    const { container } = render(<CompletionRing completion={CORE_DONE} typesPending />);
    expect(screen.getByText('6 of 6 fields')).toBeInTheDocument();
    expect(screen.getByText('Confirm change types to see the full total')).toBeInTheDocument();
    expect(await violations(container)).toEqual([]);
  });

  it('drops the note once every type is decided', () => {
    render(<CompletionRing completion={CORE_DONE} />);
    expect(screen.queryByText('Confirm change types to see the full total')).not.toBeInTheDocument();
  });
});
