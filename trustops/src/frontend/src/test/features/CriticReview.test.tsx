/**
 * The critic review on the Summary: one card per finding that a reviewer understands without opening
 * anything (what was found, the control, the server's numbers, the record's reason, what to do) and three
 * actions. Before, the banner read "CTL-G02 at 4.5 is 2.6 below the 7.1 mean ... #12 [Explain…]".
 */
import { configure, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { CriticFinding, CriticFindingGate } from '@/api/types/analyst';
import { CriticReview } from '@/features/analyst/CriticReview';
import { resetMockState } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp, renderUi } from '../render';

configure({ asyncUtilTimeout: 5000 });

const CASE = 'FCRM-2026-0151';

function finding(over: Partial<CriticFinding & CriticFindingGate> = {}): CriticFinding & CriticFindingGate {
  return {
    id: 'cf_1',
    type: 'precedent_deviation',
    severity: 'high',
    text: 'CTL-G02 at 4.5 is 2.6 below the 7.1 mean of the closest cases (range 4.1-9.0).',
    records: [12],
    target: 'CTL-G02',
    target_name: 'Corridor enhanced due diligence',
    reason: 'The EDD procedure was not provided; only a draft sample was filed.',
    comparison: {
      control_id: 'CTL-G02',
      control_name: 'Corridor enhanced due diligence',
      rating: 4.5,
      mean: 7.1,
      low: 4.1,
      high: 9.0,
      gap: 2.6,
      cases: [
        { case_id: 'FCRM-2026-0118', rating: 9.0, closed_date: '2026-06-15' },
        { case_id: 'FCRM-2026-0097', rating: 4.1, closed_date: null },
      ],
    },
    gate_id: 'gate_1',
    gate_open: true,
    ...over,
  };
}

function Where() {
  const l = useLocation();
  return <p data-testid="where">{`${l.pathname}${l.hash}`}</p>;
}

function show(findings: CriticFinding[], { closed = false, canExplain = true, onExplain = vi.fn() } = {}) {
  renderUi(
    <Routes>
      <Route
        path="*"
        element={
          <>
            <CriticReview findings={findings} caseId={CASE} closed={closed} canExplain={canExplain} onExplain={onExplain} />
            <Where />
          </>
        }
      />
    </Routes>,
    { route: `/analyst/case/${CASE}/summary` },
  );
  return onExplain;
}

describe('the critic review card', () => {
  it('says what was found, the control, the numbers the server sent, the reason and what to do', () => {
    show([finding()]);
    expect(screen.getByRole('heading', { name: /Critic review · 1 finding to explain/ })).toBeInTheDocument();
    expect(screen.getByText(/A second AI checked the assessment/)).toBeInTheDocument();
    const card = screen.getByRole('listitem', { name: 'Rating lower than in similar cases' });
    const c = within(card);
    expect(c.getByText('High')).toBeInTheDocument();
    expect(c.getByText('Severity:')).toBeInTheDocument();
    expect(c.getByText('CTL-G02 · Corridor enhanced due diligence')).toBeInTheDocument();
    expect(c.getByText('This case').nextElementSibling).toHaveTextContent('4.5 / 10');
    expect(c.getByText('Similar cases').nextElementSibling).toHaveTextContent('7.1 on average (4.1 – 9.0)');
    expect(c.getByText('Why it was rated 4.5')).toBeInTheDocument();
    expect(c.getByText('The EDD procedure was not provided; only a draft sample was filed.')).toBeInTheDocument();
    expect(c.getByLabelText('Audit record 12')).toBeInTheDocument();
    expect(c.getByText(/Confirm the low rating is right/)).toBeInTheDocument();
    expect(c.getByText('Show the 2 cases')).toBeInTheDocument();
    expect(c.getByRole('link', { name: 'FCRM-2026-0118' })).toHaveAttribute('href', '/analyst/case/FCRM-2026-0118/summary');
    expect(c.getByText('rated 4.1')).toBeInTheDocument();
  });

  it('says "higher" when this case rates the control above the similar cases', () => {
    show([finding({ comparison: { ...finding().comparison!, rating: 7.6, mean: 4.9, gap: -2.7 } })]);
    expect(screen.getByRole('heading', { name: 'Rating higher than in similar cases' })).toBeInTheDocument();
    expect(screen.getByText(/Confirm the high rating is supported/)).toBeInTheDocument();
  });

  it('offers the three actions: explain, change the rating on Risk & controls, ask the owner', async () => {
    const onExplain = show([finding()]);
    await userEvent.click(screen.getByRole('button', { name: 'Explain why' }));
    expect(onExplain).toHaveBeenCalledWith(expect.objectContaining({ id: 'cf_1' }));
    await userEvent.click(screen.getByRole('button', { name: 'Change rating' }));
    expect(screen.getByTestId('where')).toHaveTextContent(`/analyst/case/${CASE}/risk-controls#control-CTL-G02`);
    await userEvent.click(screen.getByRole('button', { name: 'Ask the owner for the evidence' }));
    expect(screen.getByTestId('where')).toHaveTextContent(`/analyst/case/${CASE}/inbox`);
  });

  it('without a comparison shows the finding text and reason; without a reason omits that row', () => {
    show([
      finding({ id: 'cf_2', type: 'evidence_recency', severity: 'medium', comparison: null, reason: 'Audit of November 2023 only.', text: 'Only evidence is a 2023 audit.' }),
      finding({ id: 'cf_3', type: 'rule_violation', severity: 'low', comparison: null, reason: null, text: 'Rule R4 may not hold.' }),
    ]);
    const recency = within(screen.getByRole('listitem', { name: 'Evidence may be out of date' }));
    expect(recency.getByText('Only evidence is a 2023 audit.')).toBeInTheDocument();
    expect(recency.getByText('Why it was raised')).toBeInTheDocument();
    expect(recency.queryByText('This case')).not.toBeInTheDocument();
    expect(recency.getByText('Medium')).toBeInTheDocument();
    const rule = within(screen.getByRole('listitem', { name: 'A rule may not be met' }));
    expect(rule.getByText('Rule R4 may not hold.')).toBeInTheDocument();
    expect(rule.queryByText(/Why it was/)).not.toBeInTheDocument();
    expect(rule.getByLabelText('Audit record 12')).toBeInTheDocument();
  });

  it('sorts high first and names an unknown type plainly', () => {
    show([finding({ id: 'a', type: 'other', severity: 'low', comparison: null }), finding({ id: 'b', severity: 'high' })]);
    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(['Rating lower than in similar cases', 'Needs a look']);
  });

  it('collapses an explained finding to one line with no actions', () => {
    show([finding({ gate_open: false })]);
    expect(screen.getByRole('heading', { name: /all findings explained/ })).toBeInTheDocument();
    expect(screen.getByText('Explained')).toBeInTheDocument();
    expect(screen.queryByText('This case')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('hides explain when it is not available and every action on a closed case', () => {
    show([finding()], { canExplain: false });
    expect(screen.queryByRole('button', { name: 'Explain why' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change rating' })).toBeInTheDocument();
  });

  it('shows no action on a closed case', () => {
    show([finding()], { closed: true, canExplain: false });
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('the critic review in the app', () => {
  it('shows the demo findings with the control name, the comparison and the reason (mock backend)', async () => {
    resetMockState('released');
    renderApp(`/analyst/case/${CASE}/summary`, { as: USERS.rahul });
    const card = within(await screen.findByRole('listitem', { name: 'Rating higher than in similar cases' }));
    expect(card.getByText('CTL-T01 · Vendor due-diligence assessment')).toBeInTheDocument();
    expect(card.getByText('This case').nextElementSibling).toHaveTextContent('7.6 / 10');
    expect(card.getByText('Similar cases').nextElementSibling).toHaveTextContent('4.9 on average');
    expect(card.getByText('Show the 5 cases')).toBeInTheDocument();
    expect(card.getByText(/established AML programme/)).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: 'Evidence may be out of date' })).toBeInTheDocument();
  });

  it('"Change rating" lands on the control row on Risk & controls', async () => {
    resetMockState('released');
    renderApp(`/analyst/case/${CASE}/risk-controls#control-CTL-T01`, { as: USERS.rahul });
    await waitFor(() => expect(document.getElementById('control-CTL-T01')).toHaveFocus());
  });
});
