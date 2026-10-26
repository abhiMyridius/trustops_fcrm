/**
 * The committee's case view: the Pack tab is now "Summary" and shows the analyst's response under each critic
 * finding, and a new Submission tab shows the owner's latest submitted form.
 */
import { configure, screen } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { resetMockState, server } from '@/mocks/server';
import * as CV from '@/mocks/views/committee';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

configure({ asyncUtilTimeout: 5000 });

const CASE = 'FCRM-2026-0151';

describe('the committee case view', () => {
  it('shows the analyst response under a critic finding, and the Summary and Submission tabs', async () => {
    resetMockState('released');
    const pack = CV.packView(CASE);
    if (!pack) throw new Error('no pack');
    const findings = [
      { id: 'cf_1', type: 'scope_gap', severity: 'high' as const, text: 'The register does not cover the new segment.', records: [], target: null, response: 'The segment is covered by the vendor attestation.', responded_by: 'Rahul Iyer' },
      { id: 'cf_2', type: 'unsourced_claim', severity: 'low' as const, text: 'A quote is not in the span.', records: [], target: null },
    ];
    server.use(http.get(paths.decision.latestPack(CASE), () => HttpResponse.json({ ...pack, critic_findings: findings })));
    renderApp(`/committee/case/${CASE}/pack`, { as: USERS.ana });

    expect(await screen.findByText('The segment is covered by the vendor attestation.')).toBeInTheDocument();
    expect(screen.getByText(/Analyst response \(Rahul Iyer\):/)).toBeInTheDocument();
    expect(screen.getByText('No response recorded.')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Summary' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Submission' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Pack' })).not.toBeInTheDocument();
  });

  it('shows the latest submitted form on the Submission tab', async () => {
    resetMockState('released');
    renderApp(`/committee/case/${CASE}/submission`, { as: USERS.ana });

    expect(await screen.findByText(/^Submission v\d/)).toBeInTheDocument();
    expect(await screen.findByRole('table', { name: /^Fields in v\d/ })).toBeInTheDocument();
  });
});
