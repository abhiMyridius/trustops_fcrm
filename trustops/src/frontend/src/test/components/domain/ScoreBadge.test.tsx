import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ScoreBadge } from '@/components/domain/ScoreBadge';
import { VoteCard } from '@/components/domain/VoteCard';
import { SourceChip } from '@/components/domain/SourceChip';
import { violations } from '../../axe';

describe('ScoreBadge', () => {
  it('prints the server number and the band label (colour is never the only carrier)', async () => {
    const { container } = render(
      <ScoreBadge size="lg" composite={10.4} band={3} bandLabel="Elevated" bandRange="10.1–17.0" routeLabel="Risk Committee (R2)" boundaryText="Boundary flag · 0.3 above the Band 3 edge (10.1)" />,
    );
    expect(screen.getByText('10.4')).toBeInTheDocument();
    expect(screen.getByText(/Band 3 · Elevated of 4 \(10\.1–17\.0\)/)).toBeInTheDocument();
    expect(screen.getByText(/Route: Risk Committee/)).toBeInTheDocument();
    expect(await violations(container)).toEqual([]);
  });
});

describe('VoteCard', () => {
  it('shows "voted · hidden" without a choice before reveal', () => {
    render(<VoteCard revealed={false} member={{ name: 'Joseph Okafor', status: 'voted', is_you: false, choice: null, rationale: null, cast_at: null }} />);
    expect(screen.getByText(/Voted · hidden/)).toBeInTheDocument();
    expect(screen.queryByText(/Approve/)).not.toBeInTheDocument();
  });

  it('shows the choice and rationale once revealed', () => {
    render(
      <VoteCard
        revealed
        member={{ name: 'Lin Chen', status: 'voted', is_you: false, choice: 'defer', rationale: 'Defer until the corridor review is complete.', cast_at: '2026-10-03T10:07:00Z' }}
      />,
    );
    expect(screen.getByText('Defer')).toBeInTheDocument();
    expect(screen.getByText(/corridor review/)).toBeInTheDocument();
  });
});

describe('SourceChip', () => {
  it('renders nothing without a source (no source, no claim)', () => {
    const { container } = render(<SourceChip source={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
