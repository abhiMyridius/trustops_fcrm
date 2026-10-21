/**
 * Smoke test over every screen of design C §2 (27 screens, 8 config sections, 4 assurance tabs):
 * each route renders for its role, through the real router, guards and MSW handlers, shows its
 * seed-derived content and never lands on an inline error.
 */
import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ScenarioKey } from '@/mocks/db';
import { resetMockState } from '@/mocks/server';
import { USERS } from '../fixtures';
import { renderApp } from '../render';

const C = 'FCRM-2026-0151';

interface Screen {
  name: string;
  as: string;
  scenario: ScenarioKey;
  route: string;
  expect: RegExp;
}

const SCREENS: Screen[] = [
  // Product owner (2.2–2.10)
  { name: '2.2 PO dashboard', as: USERS.priya, scenario: 'fresh', route: '/po', expect: /Welcome back, Priya/ },
  { name: '2.3 PO my queue', as: USERS.priya, scenario: 'fresh', route: '/queue', expect: /Instant account-to-account transfers/ },
  { name: '2.4 PO documents', as: USERS.priya, scenario: 'fresh', route: `/case/${C}/documents`, expect: /Use the demo's two files/ },
  { name: '2.5 PO overview', as: USERS.priya, scenario: 'submitted', route: `/case/${C}/overview`, expect: /Proposed change types/ },
  { name: '2.6 PO review & submit', as: USERS.priya, scenario: 'submitted', route: `/case/${C}/review`, expect: /February 2027/ },
  { name: '2.7 PO inbox', as: USERS.priya, scenario: 'released', route: `/case/${C}/inbox`, expect: /Questions from FCRM · round 1/ },
  { name: '2.8 PO conditions', as: USERS.priya, scenario: 'closed', route: `/case/${C}/conditions`, expect: /Corridor-scope vendor DD review completed and filed/ },
  { name: '2.9 PO activity', as: USERS.priya, scenario: 'closed', route: `/case/${C}/activity`, expect: /You submitted the case \(v1\)/ },
  { name: '2.10 PO integrations', as: USERS.priya, scenario: 'fresh', route: '/integrations', expect: /Connected \(demo\)/ },
  { name: 'PO new case', as: USERS.priya, scenario: 'fresh', route: '/case/new', expect: /How do you want to start?/ },
  // Analyst (2.11–2.24)
  { name: '2.11 analyst dashboard', as: USERS.rahul, scenario: 'submitted', route: '/analyst', expect: /Band distribution · 80 seeded cases/ },
  { name: '2.12 analyst queue', as: USERS.rahul, scenario: 'submitted', route: '/analyst/queue', expect: /KYC vendor renewal/ },
  { name: '2.14 workbench summary', as: USERS.rahul, scenario: 'submitted', route: `/analyst/case/${C}/summary`, expect: /Score withheld/ },
  { name: '2.15 workbench submission', as: USERS.rahul, scenario: 'released', route: `/analyst/case/${C}/submission`, expect: /What changed · v1 → v2/ },
  { name: '2.16 workbench inbox', as: USERS.rahul, scenario: 'released', route: `/analyst/case/${C}/inbox`, expect: /Round 1 · sent/ },
  { name: '2.17 workbench policy', as: USERS.rahul, scenario: 'released', route: `/analyst/case/${C}/policy`, expect: /FFIEC-TPPP-04/ },
  { name: '2.18 workbench risk & controls', as: USERS.rahul, scenario: 'released', route: `/analyst/case/${C}/risk-controls`, expect: /Vendor due-diligence assessment/ },
  { name: '2.19 workbench recommendation', as: USERS.rahul, scenario: 'released', route: `/analyst/case/${C}/recommendation`, expect: /Corridor-scope vendor DD review/ },
  { name: '2.20 workbench handoff', as: USERS.rahul, scenario: 'released', route: `/analyst/case/${C}/handoff`, expect: /Release checks/ },
  { name: '2.21 workbench activity', as: USERS.rahul, scenario: 'closed', route: `/analyst/case/${C}/activity`, expect: /Hash chain verified end to end/ },
  { name: '2.22 assurance ask', as: USERS.rahul, scenario: 'closed', route: '/analyst/assurance/ask', expect: /Ask the examiner/ },
  { name: '2.23 assurance overrides', as: USERS.rahul, scenario: 'closed', route: '/analyst/assurance/overrides', expect: /Audit predates the corridor arrangement/ },
  { name: '2.23 assurance watchlist', as: USERS.rahul, scenario: 'closed', route: '/analyst/assurance/watchlist', expect: /North Africa trade finance/ },
  { name: '2.23 assurance agent quality', as: USERS.rahul, scenario: 'closed', route: '/analyst/assurance/agent-quality', expect: /suggestion acceptance/ },
  { name: '2.24 config fields', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/field-sets', expect: /Partner AML programme attestation/ },
  { name: '2.24 config classification', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/categories', expect: /Fast lane \(R4\)/ },
  { name: '2.24 config scoring', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/scoring-model', expect: /Likelihood anchors/ },
  { name: '2.24 config controls', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/control-inventory', expect: /Fourth-party \(subcontractor\) oversight/ },
  { name: '2.24 config corpus', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/policy-corpus', expect: /197 clauses/ },
  { name: '2.24 config routing', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/routing-rules', expect: /Auto-triage/ },
  { name: '2.24 config jurisdictions', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/jurisdiction-risk', expect: /Call for action · 3/ },
  { name: '2.24 config agents', as: USERS.rahul, scenario: 'fresh', route: '/analyst/config/ai-agents', expect: /policy_gap/ },
  // Committee (2.25–2.26)
  { name: '2.25 committee queue', as: USERS.ana, scenario: 'released', route: '/committee', expect: /Consumer remittances US → Mexico/ },
  { name: '2.26 committee pack', as: USERS.ana, scenario: 'released', route: `/committee/case/${C}/pack`, expect: /Override log/ },
  { name: '2.26 committee inbox', as: USERS.ana, scenario: 'released', route: `/committee/case/${C}/inbox`, expect: /Ask the analyst/ },
  { name: '2.26 committee vote', as: USERS.ana, scenario: 'released', route: `/committee/case/${C}/vote`, expect: /1 of 3 votes in/ },
  // Harness monitor (2.27)
  { name: '2.27 harness monitor (observer)', as: USERS.observer, scenario: 'closed', route: `/monitor/${C}`, expect: /Harness monitor · FCRM-2026-0151/ },
];

describe('every screen renders from the mock backend', () => {
  for (const s of SCREENS) {
    it(s.name, async () => {
      resetMockState(s.scenario);
      renderApp(s.route, { as: s.as });
      expect((await screen.findAllByText(s.expect, {}, { timeout: 5000 })).length).toBeGreaterThan(0);
      await waitFor(() => expect(screen.queryAllByRole('status', { name: /^Loading/ })).toHaveLength(0));
      expect(screen.queryByText(/Trace id/)).not.toBeInTheDocument();
    });
  }
});
