/**
 * The Risk & Controls tab's "How this is calculated" panel writes S2's own numbers into the published formulas.
 * It computes nothing: every value shown is a field of the area (no business arithmetic in the client).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { RiskArea } from '@/api/types/analyst';
import { CompositeRules, ScoreFormula } from '@/features/analyst/ScoreFormula';

const control = (id: string, weight_norm: number, rating: number) => ({
  id,
  name: id,
  domain: 'geography' as const,
  weight_norm,
  rating,
  proposed_rating: rating,
  evidence: 'e',
  evidence_date: '2026-06-30',
  stale: false,
  decision: 'confirmed' as const,
  override: null,
  critic: false,
});

/** The operator's screenshot of 8 Oct: geography, inherent 12, CE 4.86, retention 0.71, residual 8.5. */
const AREA = {
  domain: 'geography',
  label: 'Geography',
  L: 3,
  I: 4,
  inherent: 12,
  ce: 4.86,
  retention: 0.7084,
  residual: 8.5,
  weight: 0.5455,
  contribution: 4.64,
  controls: [control('CTL-G05', 0.2, 3), control('CTL-G01', 0.2, 5.5), control('CTL-G03', 0.28, 6), control('CTL-G02', 0.2, 5), control('CTL-G04', 0.12, 4)],
  matched_caption: '5 controls',
  justification: '',
  lock_blocking: true,
} as unknown as RiskArea;

describe('How this is calculated', () => {
  it('writes the area numbers into each formula', () => {
    render(<ScoreFormula area={AREA} />);
    expect(screen.getByText('How this is calculated')).toBeInTheDocument();
    expect(screen.getByText('3 × 4 = 12')).toBeInTheDocument();
    expect(screen.getByText('0.20 × 3.0 + 0.20 × 5.5 + 0.28 × 6.0 + 0.20 × 5.0 + 0.12 × 4.0 = 4.86')).toBeInTheDocument();
    expect(screen.getByText('1 − 0.6 × 4.86 ÷ 10 = 0.71')).toBeInTheDocument();
    expect(screen.getByText('12 × 0.71 = 8.5')).toBeInTheDocument();
    expect(screen.getByText('8.5 × 0.55 = 4.64')).toBeInTheDocument();
  });

  it('says so when an area has no matched control', () => {
    render(<ScoreFormula area={{ ...AREA, controls: [], ce: 0 } as RiskArea} />);
    expect(screen.getByText('no matched control = 0.00')).toBeInTheDocument();
  });

  it('lists the case-level rules', () => {
    render(<CompositeRules />);
    expect(screen.getByText(/Bands: 1 · 0–5.0/)).toBeInTheDocument();
    expect(screen.getByText(/within 0.5 of a band edge/)).toBeInTheDocument();
  });
});
