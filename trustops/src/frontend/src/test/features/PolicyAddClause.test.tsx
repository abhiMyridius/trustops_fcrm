/**
 * ADR-74 on the Policy tab: each corpus-search hit has "Add to case", off (with the reason shown) when
 * the clause is already on the case or the stage does not allow it; adding asks for a reason, posts
 * the clause id and the reason only (never the text), says so in a toast and refreshes the clause
 * list; a clause the analyst added says who added it and why and can still be removed with a reason.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { Outlet, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { ClauseItem, PolicySearchHit, PolicyView, WorkbenchHeader } from '@/api/types/analyst';
import { PolicyTab } from '@/features/analyst/tabs/Policy';
import { addBlocker } from '@/features/analyst/tabs/addBlocker';
import { server } from '@/mocks/server';
import { renderUi } from '../render';

const CASE = 'FCRM-2026-0151';
const REASON = 'The payout partner runs an agent network the research did not cover.';

function header(over: Partial<WorkbenchHeader> = {}): WorkbenchHeader {
  return {
    id: CASE,
    title: 'Consumer remittances US to Mexico via payout partner',
    unit: 'Payments',
    owner_name: 'Priya Nair',
    analyst_name: 'Rahul Iyer',
    change_types: ['new_product'],
    domains: ['channel', 'geography', 'product'],
    stage: 'review',
    stage_label: 'Review',
    lifecycle: [],
    clocks: { analyst_active: '1h', total_elapsed: '2d', analyst_paused: false },
    pins: { scoring: 'v3', corpus: 'v14', controls: 'v9', field_set: 'v6', jurisdictions: '2026-06', routing: 'v4', prompts: 'v1' },
    pending_acknowledgements: [],
    ack_message: null,
    closed: false,
    submitted: true,
    review_started: true,
    completeness_decision: 'proceed',
    submission_version: 1,
    locked: false,
    route: null,
    audit: { count: 40, verified: true },
    is_demo: true,
    ...over,
  };
}

const PROPOSED: ClauseItem = {
  id: 'FFIEC-TPPP-04',
  source: 'FFIEC',
  title: 'Third-Party Payment Processors',
  quote: 'banks should assess the risks of the processor',
  ref: 'FFIEC 2024 ed. · p.312',
  corpus_version: 'v14',
  decision: 'pending',
  reason: null,
};

const ADDED: ClauseItem = {
  id: 'MOCK-AGT-01',
  source: 'MOCK',
  title: 'Agent network oversight',
  quote: 'know how each agent is selected, trained and monitored',
  ref: 'Mock corpus · p.1',
  corpus_version: 'v14',
  decision: 'accepted',
  reason: REASON,
  origin: 'analyst',
  added_by: 'Rahul Iyer',
  added_reason: REASON,
};

const HITS: PolicySearchHit[] = [
  { clause_id: 'FFIEC-TPPP-04', title: 'Third-Party Payment Processors', quote: 'q', ref: 'FFIEC 2024 ed. · p.312' },
  { clause_id: 'MOCK-AGT-01', title: 'Agent network oversight', quote: 'q', ref: 'Mock corpus · p.1', quotable: true },
];

interface Served {
  policyReads: number;
  posted: unknown[];
  decided: unknown[];
}

function serve(clauses: ClauseItem[], after: ClauseItem[] = clauses): Served {
  const served: Served = { policyReads: 0, posted: [], decided: [] };
  server.use(
    http.get(paths.cases.policy(CASE), () => {
      served.policyReads += 1;
      const view: PolicyView = { clauses: served.posted.length ? after : clauses, gap: null, guard4_note: null, corpus_version: 'v14' };
      return HttpResponse.json(view);
    }),
    http.post(paths.policy.search, () => HttpResponse.json({ query: 'agent network', results: HITS, corpus_version: 'v14' })),
    http.post(paths.cases.clauses(CASE), async ({ request }) => {
      served.posted.push(await request.json());
      return HttpResponse.json(
        { item_id: 'acl_1', case_id: CASE, clause_id: 'MOCK-AGT-01', corpus_version: 'v14', status: 'accepted', replayed: false },
        { status: 201 },
      );
    }),
    http.post(paths.cases.item(CASE, 'MOCK-AGT-01'), async ({ request }) => {
      served.decided.push(await request.json());
      return HttpResponse.json({ item_id: 'acl_1', status: 'removed', replayed: false });
    }),
  );
  return served;
}

function Frame({ w }: { w: WorkbenchHeader }) {
  return <Outlet context={w} />;
}

function renderPolicy(w: WorkbenchHeader = header()) {
  return renderUi(
    <Routes>
      <Route path="/analyst/cases/:id" element={<Frame w={w} />}>
        <Route path="policy" element={<PolicyTab />} />
      </Route>
    </Routes>,
    { route: `/analyst/cases/${CASE}/policy` },
  );
}

async function search(): Promise<void> {
  await userEvent.click(await screen.findByRole('button', { name: /search corpus/i }));
  await screen.findByText(/2 results/);
}

describe('adding a clause from a corpus search', () => {
  it('offers Add to case for a hit not on the case and says why it is off for one that is', async () => {
    serve([PROPOSED]);
    renderPolicy();
    await search();

    expect(screen.getByRole('button', { name: 'Add clause FFIEC-TPPP-04 to the case' })).toBeDisabled();
    expect(screen.getByText('Already on the case.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add clause MOCK-AGT-01 to the case' })).toBeEnabled();
  });

  it('asks for a reason, posts the id and the reason only, confirms and refreshes the clauses', async () => {
    const served = serve([PROPOSED], [PROPOSED, ADDED]);
    renderPolicy();
    await search();
    const before = served.policyReads;

    await userEvent.click(screen.getByRole('button', { name: 'Add clause MOCK-AGT-01 to the case' }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Add clause' });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByRole('textbox'), REASON);
    await userEvent.click(confirm);

    await waitFor(() => expect(served.posted).toEqual([{ clause_id: 'MOCK-AGT-01', reason: REASON }]));
    expect(await screen.findByText('Clause added')).toBeInTheDocument();
    await waitFor(() => expect(served.policyReads).toBeGreaterThan(before));
    expect(await screen.findByText(/Added by Rahul Iyer from a corpus search/)).toBeInTheDocument();
  });

  it('keeps Add to case off outside review and the recommendation', async () => {
    serve([PROPOSED]);
    renderPolicy(header({ stage: 'committee', stage_label: 'Committee' }));
    await search();

    expect(screen.getByRole('button', { name: 'Add clause MOCK-AGT-01 to the case' })).toBeDisabled();
    expect(screen.getByText(/added during review or while the recommendation is drafted/)).toBeInTheDocument();
  });

  it('keeps Add to case off once the recommendation is locked', async () => {
    serve([PROPOSED]);
    renderPolicy(header({ stage: 'recommend', locked: true }));
    await search();

    expect(screen.getByRole('button', { name: 'Add clause MOCK-AGT-01 to the case' })).toBeDisabled();
  });

  it('lets the analyst remove a clause they added, with a reason', async () => {
    const served = serve([ADDED]);
    renderPolicy();

    expect(await screen.findByText(/Added by Rahul Iyer from a corpus search: The payout partner/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Remove clause MOCK-AGT-01' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByRole('textbox'), 'Added by mistake: the agent network is out of scope.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove clause' }));

    await waitFor(() =>
      expect(served.decided).toEqual([{ decision: 'remove', reason: 'Added by mistake: the agent network is out of scope.' }]),
    );
  });
});

describe('addBlocker', () => {
  const ok = { readOnly: false, stageOk: true };

  it('allows a quotable hit not on the case, or one whose clause was removed', () => {
    expect(addBlocker(HITS[1] as PolicySearchHit, [PROPOSED], ok)).toBeNull();
    expect(addBlocker(HITS[0] as PolicySearchHit, [{ ...PROPOSED, decision: 'removed' }], ok)).toBeNull();
  });

  it('refuses a catalogue entry that may not be quoted', () => {
    expect(addBlocker({ ...(HITS[1] as PolicySearchHit), quotable: false }, [], ok)).toMatch(/may not be quoted/);
  });
});
